# -*- coding: utf-8 -*-
"""
LaTeX 数学 → Qt 富文本（QTextDocument 支持的 HTML 子集）。

界面没有 WebEngine（安装包瘦身时已剔除），不能用 KaTeX / MathJax；这里把模型常用的
LaTeX 转成：<sup>/<sub> 上下标、Unicode 数学符号、a/b 分数、居中的独立公式、
带边框的 \\boxed、逐行显示的 aligned 环境。目标是"读得懂"，不是排版级还原。

支持的分隔符：\\[ … \\]、$$ … $$（独立公式），\\( … \\)（行内）；
单个 $ … $ 只在内容明显是公式时才识别（Houdini 里 $HIP / $F 等变量很常见）。

纯 Python，不依赖 Qt，便于离线测试。
"""

import re

# ------------------------------------------------------------------ symbol tables
_SYMBOLS = {
    # greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # big operators
    "sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
    "bigcup": "⋃", "bigcap": "⋂", "bigoplus": "⨁", "bigotimes": "⨂",
    # binary operators / relations
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆",
    "circ": "∘", "bullet": "•", "oplus": "⊕", "otimes": "⊗", "cup": "∪", "cap": "∩",
    "setminus": "∖", "wedge": "∧", "land": "∧", "vee": "∨", "lor": "∨", "neg": "¬", "lnot": "¬",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝", "ll": "≪", "gg": "≫",
    "leqslant": "⩽", "geqslant": "⩾", "prec": "≺", "succ": "≻",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃", "subseteq": "⊆",
    "supseteq": "⊇", "mid": "∣", "nmid": "∤", "parallel": "∥", "perp": "⊥", "vdash": "⊢",
    "models": "⊨",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔", "implies": "⟹",
    "impliedby": "⟸", "iff": "⟺", "mapsto": "↦", "longrightarrow": "⟶",
    "Longrightarrow": "⟹", "uparrow": "↑", "downarrow": "↓",
    # misc
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃", "nexists": "∄",
    "emptyset": "∅", "varnothing": "∅", "angle": "∠", "triangle": "△", "degree": "°",
    "prime": "′", "ldots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱", "dots": "…",
    "therefore": "∴", "because": "∵", "aleph": "ℵ", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ",
    "Im": "ℑ", "wp": "℘", "square": "□", "blacksquare": "■", "qed": "∎", "checkmark": "✓",
    # delimiters
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "lvert": "|", "rvert": "|", "vert": "|", "lVert": "‖", "rVert": "‖", "Vert": "‖",
    "{": "{", "}": "}", "|": "‖", "%": "%", "$": "$", "#": "#", "&": "&amp;", "_": "_",
    # spacing
    ",": "\u2009", ":": "\u2005", ";": "\u2005", ">": "\u2005", "!": "", " ": " ",
    "quad": "\u2003", "qquad": "\u2003\u2003", "enspace": "\u2002", "thinspace": "\u2009",
}
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
          "tanh", "log", "ln", "lg", "exp", "lim", "liminf", "limsup", "max", "min", "sup", "inf",
          "gcd", "lcm", "deg", "det", "dim", "ker", "arg", "Pr", "mod", "bmod", "sgn", "tr", "rank"}
_IGNORE = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr",
           "displaystyle", "textstyle", "scriptstyle", "limits", "nolimits", "middle"}
_BB = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ"))
_CAL = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"))
_ACCENT = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0305",
           "vec": "\u20d7", "dot": "\u0307", "ddot": "\u0308", "tilde": "\u0303",
           "widetilde": "\u0303", "acute": "\u0301", "grave": "\u0300", "check": "\u030c",
           "underline": "\u0332"}

_MATH_FONT = "Cambria Math"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ tokenizer / parser
_TOKEN = re.compile(r"\\([A-Za-z]+)\*?|\\(.)|([{}^_&])|(\s+)|(.)", re.S)


def _tokens(src):
    out = []
    for m in _TOKEN.finditer(src):
        if m.group(1) is not None:
            out.append(("cmd", m.group(1)))
        elif m.group(2) is not None:
            out.append(("cmd", m.group(2)) if m.group(2) != "\\" else ("nl", "\\\\"))
        elif m.group(3) is not None:
            out.append((m.group(3), m.group(3)))
        elif m.group(4) is not None:
            out.append(("ws", " "))
        else:
            out.append(("ch", m.group(5)))
    return out


class _Parser:
    def __init__(self, src, display):
        self.t = _tokens(src)
        self.i = 0
        self.display = display

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def skip_ws(self):
        while self.peek()[0] == "ws":
            self.i += 1

    # a raw {...} group as text (for \text, \begin{...})
    def raw_group(self):
        self.skip_ws()
        if self.peek()[0] != "{":
            k, v = self.take()
            return v or ""
        self.take()
        depth, buf = 1, []
        while self.i < len(self.t):
            k, v = self.take()
            if k == "{":
                depth += 1
            elif k == "}":
                depth -= 1
                if depth == 0:
                    break
            buf.append(("\\" + v) if k == "cmd" else v)
        return "".join(buf)

    def optional_arg(self):
        self.skip_ws()
        if self.peek() == ("ch", "["):
            self.take()
            buf = []
            while self.i < len(self.t) and self.peek() != ("ch", "]"):
                buf.append(self.take())
            self.take()
            return _Parser.from_tokens(buf, self.display).parse_seq()
        return None

    @staticmethod
    def from_tokens(toks, display):
        p = _Parser("", display)
        p.t = list(toks)
        return p

    def atom(self):
        """One argument: a {group} or a single token. Returns (html, is_simple)."""
        self.skip_ws()
        k, v = self.peek()
        if k == "{":
            self.take()
            html = self.parse_seq(stop="}")
            self.take()
            return html, False
        if k is None:
            return "", True
        return self.parse_one(), True

    def parse_seq(self, stop=None, env=None):
        parts = []
        while self.i < len(self.t):
            k, v = self.peek()
            if stop and k == stop:
                break
            if env and k == "cmd" and v == "end":
                break
            parts.append(self.parse_one())
        return "".join(parts)

    def script(self, base):
        """Attach ^/_ scripts following an atom."""
        while True:
            self.skip_ws()
            k, v = self.peek()
            if k == "^":
                self.take()
                html, _ = self.atom()
                base += "<sup>%s</sup>" % html
            elif k == "_":
                self.take()
                html, _ = self.atom()
                base += "<sub>%s</sub>" % html
            elif k == "ch" and v == "'":
                self.take()
                base += "′"
            else:
                return base

    def parse_one(self):
        k, v = self.take()
        if k == "ws":
            return " "
        if k == "nl":
            return "<br>"
        if k == "&":
            return "\u2002"
        if k in ("^", "_"):
            self.i -= 1
            return self.script("")
        if k == "{":
            html = self.parse_seq(stop="}")
            self.take()
            return self.script(html)
        if k == "}":
            return ""
        if k == "ch":
            if v.isalpha() and v.isascii():
                return self.script("<i>%s</i>" % v)
            return self.script(_esc(v).replace("-", "−").replace("*", "∗"))
        # ---------------- commands
        if v in _IGNORE:
            self.skip_ws()
            if self.peek() == ("ch", "."):
                self.take()
            return ""
        if v in ("frac", "dfrac", "tfrac", "cfrac"):
            num, ns = self.atom()
            den, ds = self.atom()
            n = num if ns or _simple(num) else "(%s)" % num
            d = den if ds or _simple(den) else "(%s)" % den
            return self.script("%s/%s" % (n, d))
        if v == "binom":
            a, _ = self.atom()
            b, _ = self.atom()
            return self.script("C(%s, %s)" % (a, b))
        if v == "sqrt":
            idx = self.optional_arg()
            body, simple = self.atom()
            root = ("<sup>%s</sup>" % idx if idx else "") + "√"
            return self.script(root + (body if simple or re.fullmatch(r"[\w.]+", re.sub(r"<[^>]+>", "", body)) else "(%s)" % body))
        if v == "boxed" or v == "fbox":
            body, _ = self.atom()
            if self.display:
                return ("<table border='1' cellspacing='0' cellpadding='6' style='border-color:#8a8473'>"
                        "<tr><td>%s</td></tr></table>" % body)
            return "<b>%s</b>" % body
        if v in ("text", "textrm", "textup", "mbox", "mathrm", "operatorname", "textnormal"):
            return self.script(_esc(self.raw_group()))
        if v in ("textbf", "mathbf", "boldsymbol", "bm"):
            body, _ = self.atom()
            return self.script("<b>%s</b>" % body)
        if v in ("textit", "mathit", "emph"):
            body, _ = self.atom()
            return self.script("<i>%s</i>" % body)
        if v in ("mathbb", "mathbbm"):
            s = self.raw_group()
            return self.script("".join(_BB.get(c, c) for c in s))
        if v in ("mathcal", "mathscr"):
            s = self.raw_group()
            return self.script("".join(_CAL.get(c, c) for c in s))
        if v in _ACCENT:
            body, _ = self.atom()
            plain = re.sub(r"<[^>]+>", "", body)
            mark = _ACCENT[v]
            if v in ("overline", "underline"):
                return self.script("".join(c + mark for c in plain))
            return self.script(plain + mark)
        if v == "pmod":
            body, _ = self.atom()
            return self.script("\u2002(mod %s)" % body)
        if v in ("pod",):
            body, _ = self.atom()
            return self.script("\u2002(%s)" % body)
        if v == "not":
            nxt = self.parse_one()
            return nxt + "\u0338"
        if v == "begin":
            return self.environment(self.raw_group())
        if v == "end":
            self.raw_group()
            return ""
        if v in _FUNCS:
            name = "mod" if v == "bmod" else v
            return self.script(name + ("\u2009" if v not in ("lim", "max", "min", "sup", "inf") else ""))
        if v in _SYMBOLS:
            return self.script(_SYMBOLS[v])
        # unknown command: show its name upright
        return self.script(_esc(v))

    def environment(self, name):
        name = name.strip().rstrip("*")
        body = self.parse_seq(env=True)
        if self.peek() == ("cmd", "end"):
            self.take()
            self.raw_group()
        rows = [r for r in body.split("<br>")]
        if name in ("pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "matrix", "smallmatrix", "array"):
            l, r = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
                    "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖")}.get(name, ("", ""))
            if self.display:
                cells = "".join("<tr>%s</tr>" % "".join("<td align='center'>%s</td>" % c.strip()
                                                        for c in row.split("\u2002"))
                                for row in rows if row.strip())
                return ("%s<table cellspacing='0' cellpadding='3' style='display:inline'>%s</table>%s"
                        % (l, cells, r))
            return l + "; ".join(", ".join(c.strip() for c in row.split("\u2002")) for row in rows if row.strip()) + r
        if name == "cases":
            return "{ " + "<br>\u2003".join(row.replace("\u2002", ",\u2002") for row in rows if row.strip())
        # aligned / align / gathered / split / eqnarray …: one line per row
        return "<br>".join(row for row in rows if row.strip())


def _simple(html):
    plain = re.sub(r"<[^>]+>", "", html)
    return not re.search(r"[\s+\-−±=/<>&]", plain) or bool(re.fullmatch(r"[\w.]+", plain))


def latex_to_html(src, display=False):
    """Convert one LaTeX math snippet to Qt rich text."""
    try:
        body = _Parser(src.strip(), display).parse_seq()
    except Exception:
        body = _esc(src)
    body = re.sub(r"(<br>\s*)+$", "", body).strip()
    if display:
        return ("<div align='center' style='font-family:\"%s\"; font-size:16px; margin-top:6px; "
                "margin-bottom:6px'>%s</div>" % (_MATH_FONT, body))
    return "<span style='font-family:\"%s\"'>%s</span>" % (_MATH_FONT, body)


# ------------------------------------------------------------------ extraction from markdown text
_PH = "\x00M%d\x00"            # 行内公式占位
_DPH = "\x00D%d\x00"           # 独立公式占位（块级）
_PH_RE = re.compile("\x00[MD](\\d+)\x00")
_DPH_AROUND_RE = re.compile("(?:<br>)*(\x00D\\d+\x00)(?:<br>)*")
_CODE_PH = "\x00C%d\x00"
_CODE_PH_RE = re.compile("\x00C(\\d+)\x00")

_DISPLAY_RE = re.compile(r"\\\[(.+?)\\\]|\$\$(.+?)\$\$", re.S)
_INLINE_RE = re.compile(r"\\\((.+?)\\\)", re.S)
# 单个 $…$：内容必须含 \ ^ _{ 之一，两侧不贴空白，且不像 $HIP / $F 这种 Houdini 变量
_DOLLAR_RE = re.compile(r"(?<![\w$\\])\$(?=\S)([^$\n]{1,200}?)(?<=\S)\$(?![\w$])")


def extract_math(text, clean=None):
    """Replace math spans with placeholders. Returns (text, [html, ...]).
    Display math becomes its own paragraph (surrounded by newlines).
    clean：可选，渲染前先处理公式原文（重渲染历史富文本时用来去标签、反转义）。"""
    rendered = []
    codes = []
    clean = clean or (lambda s: s)

    def keep_code(m):
        codes.append(m.group(0))
        return _CODE_PH % (len(codes) - 1)

    text = re.sub(r"`[^`\n]+`", keep_code, text)

    def disp(m):
        rendered.append(latex_to_html(clean(m.group(1) if m.group(1) is not None else m.group(2)), display=True))
        return "\n" + (_DPH % (len(rendered) - 1)) + "\n"

    def inline(m):
        rendered.append(latex_to_html(clean(m.group(1)), display=False))
        return _PH % (len(rendered) - 1)

    def dollar(m):
        body = m.group(1)
        if not re.search(r"\\[A-Za-z]|\^|_\{", body):
            return m.group(0)
        rendered.append(latex_to_html(clean(body), display=False))
        return _PH % (len(rendered) - 1)

    text = _DISPLAY_RE.sub(disp, text)
    text = _INLINE_RE.sub(inline, text)
    text = _DOLLAR_RE.sub(dollar, text)
    text = _CODE_PH_RE.sub(lambda m: codes[int(m.group(1))], text)
    return text, rendered


def restore_math(html, rendered):
    # 独立公式本身是块级 div（自带上下间距），去掉它前后由空行转成的 <br>，否则上下各空一大段
    html = _DPH_AROUND_RE.sub(r"\1", html)
    return _PH_RE.sub(lambda m: rendered[int(m.group(1))], html)


_HAS_MATH_RE = re.compile(r"\\\[|\\\(|\$")


def _clean_stored(body):
    """历史富文本里的公式原文：<br> 已在外面换回换行；这里去掉 Markdown 插进来的标签并反转义。"""
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", body))


def rerender_html(stored):
    """老会话的回答是保存时就转好的富文本，公式仍是 LaTeX 源码。加载历史时用它补渲染：
    只处理公式片段，其余富文本原样保留；已渲染过的内容里没有分隔符，不会被二次处理。"""
    if not stored or "\x00" in stored or not _HAS_MATH_RE.search(stored):
        return stored
    text = stored.replace("<br>", "\n")
    text, maths = extract_math(text, clean=_clean_stored)
    if not maths:
        return stored
    return restore_math(text.replace("\n", "<br>"), maths)
