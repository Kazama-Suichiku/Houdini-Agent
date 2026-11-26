# -*- coding: utf-8 -*-
"""ControllerMarkdownMixin — extracted from controller.py."""

import re
from ..controller_shared import _esc


class ControllerMarkdownMixin:
    def _split(self, chunk):
        self._cbuf += chunk
        OPEN, CLOSE = "<think>", "</think>"
        vis, thk = [], []
        while True:
            if not self._in_think:
                i = self._cbuf.find(OPEN)
                if i < 0:
                    keep = self._tail_keep(self._cbuf, OPEN)
                    cut = len(self._cbuf) - keep
                    vis.append(self._cbuf[:cut]); self._cbuf = self._cbuf[cut:]
                    break
                vis.append(self._cbuf[:i]); self._cbuf = self._cbuf[i + len(OPEN):]; self._in_think = True
            else:
                j = self._cbuf.find(CLOSE)
                if j < 0:
                    keep = self._tail_keep(self._cbuf, CLOSE)
                    cut = len(self._cbuf) - keep
                    thk.append(self._cbuf[:cut]); self._cbuf = self._cbuf[cut:]
                    break
                thk.append(self._cbuf[:j]); self._cbuf = self._cbuf[j + len(CLOSE):]; self._in_think = False
        return "".join(vis), "".join(thk)

    @staticmethod
    def _tail_keep(s, tag):
        m = min(len(tag) - 1, len(s))
        for k in range(m, 0, -1):
            if s.endswith(tag[:k]):
                return k
        return 0

    @staticmethod
    def _think_html(text):
        """思考内容 → StyledText：转义 + **标题** 加粗（Codex 推理摘要用它分段）。"""
        import re as _re
        h = _esc(text.strip("\n"))
        h = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
        return h.replace("\n", "<br>")

    @classmethod
    def _parse_answer(cls, text):
        """Split markdown into prose/code segments (rendered by ProseBlock / CodeBlock)."""
        import re as _re
        segs = []
        parts = _re.split(r"```", text)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    segs.append({"kind": "prose", "html": cls._md_html(part.strip("\n"))})
            else:
                lines = part.split("\n")
                first = lines[0].strip()
                if first and " " not in first and len(first) <= 16:
                    lang = first
                    code = "\n".join(lines[1:])
                else:
                    lang = ""
                    code = part
                code = code.strip("\n")
                segs.append({"kind": "code", "lang": lang or "code", "code": code,
                             "html": cls._highlight(code, lang)})
        return segs or [{"kind": "prose", "html": cls._md_html(text)}]

    @staticmethod
    def _inline(s):
        import re as _re
        h = _esc(s)
        h = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
        h = _re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", h)
        h = _re.sub(r"`([^`]+)`", r"<span style='font-family:monospace;color:#d4a373'>\1</span>", h)
        h = _re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r"<a href='\2' style='color:#e8e2d4'>\1</a>", h)
        h = _re.sub(r"(?<![\w/])(/(?:obj|out|mat|stage|ch|shop|tasks)/[\w/]+)",
                    r"<a href='\1' style='color:#c9b896;text-decoration:none'>\1</a>", h)
        return h

    @classmethod
    def _table_html(cls, header, body):
        th = "".join("<th align='left' style='color:#ffffff;padding:3px 8px'>%s</th>" % cls._inline(h) for h in header)
        rows = "<tr bgcolor='#1a1a1a'>" + th + "</tr>"
        for r in body:
            rows += "<tr>" + "".join("<td style='color:#e7e4db;padding:3px 8px'>%s</td>" % cls._inline(c) for c in r) + "</tr>"
        return ("<table border='1' width='100%' cellspacing='0' cellpadding='4' "
                "style='border-color:#3a3a3a'>" + rows + "</table>")

    @classmethod
    def _md_html(cls, text):
        import re as _re
        # 数学公式先抽成占位符（避免 _ * 被当成 Markdown），最后换回渲染好的富文本
        from houdini_agent.ui_qml.mathrender import extract_math, restore_math
        text, maths = extract_math(text)
        lines = text.split("\n")
        out = []
        i, n = 0, len(lines)

        def cells(row):
            return [c.strip() for c in row.strip().strip("|").split("|")]

        def is_sep(row):
            return bool(_re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", row)) and "-" in row and "|" in row

        while i < n:
            line = lines[i].rstrip()
            # markdown table: header row + separator row + body rows
            if "|" in line and i + 1 < n and is_sep(lines[i + 1]):
                header = cells(line)
                i += 2
                body = []
                while i < n and lines[i].strip() and "|" in lines[i]:
                    body.append(cells(lines[i]))
                    i += 1
                out.append(cls._table_html(header, body))
                continue
            if not line.strip():
                if out and out[-1] != "":
                    out.append("")
                i += 1
                continue
            s = line.lstrip()
            if s.startswith("### "):
                out.append("<div style='color:#fafafa;font-size:14px'><b>%s</b></div>" % cls._inline(s[4:]))
            elif s.startswith("## "):
                out.append("<div style='color:#fafafa;font-size:15px'><b>%s</b></div>" % cls._inline(s[3:]))
            elif s.startswith("# "):
                out.append("<div style='color:#fafafa;font-size:16px'><b>%s</b></div>" % cls._inline(s[2:]))
            elif _re.match(r"^[-*]\s+", s):
                out.append("&nbsp;•&nbsp;" + cls._inline(_re.sub(r"^[-*]\s+", "", s)))
            elif _re.match(r"^\d+\.\s+", s):
                out.append("&nbsp;" + cls._inline(s))
            else:
                out.append(cls._inline(line))
            i += 1
        return restore_math("<br>".join(out), maths)

    @staticmethod
    def _highlight(code, lang):
        import re as _re
        C = {"com": "#7d786b", "kw": "#ece6d8", "fn": "#d3c4a2",
             "attr": "#e0b083", "num": "#b3c19a", "str": "#cda988"}
        is_py = (lang or "").lower().startswith("py")
        if is_py:
            kw = set("def class return if elif else for while import from as with try except "
                     "finally lambda None True False and or not in is pass break continue global "
                     "yield raise assert del print".split())
            comment = r"#[^\n]*"
        else:
            kw = set("float int vector vector2 vector4 matrix matrix3 string void if else for "
                     "while foreach do return struct function detail point prim vertex chi chf "
                     "chv chs export const".split())
            comment = r"//[^\n]*|/\*[\s\S]*?\*/"
        pat = _re.compile(
            r"(?P<com>%s)"
            r"|(?P<str>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
            r"|(?P<attr>[vfisp24]?@[A-Za-z_]\w*)"
            r"|(?P<num>\b\d+\.?\d*\b)"
            r"|(?P<id>[A-Za-z_]\w*)"
            r"|(?P<ws>[ \t\n]+)"
            r"|(?P<oth>.)" % comment)

        def esc(t):
            return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def sp(c, t):
            return "<span style='color:%s'>%s</span>" % (C[c], esc(t))

        out = []
        for m in pat.finditer(code):
            k = m.lastgroup
            t = m.group()
            if k == "com":
                out.append(sp("com", t))
            elif k == "str":
                out.append(sp("str", t))
            elif k == "attr":
                out.append(sp("attr", t))
            elif k == "num":
                out.append(sp("num", t))
            elif k == "id":
                nxt = code[m.end():m.end() + 1]
                if t in kw:
                    out.append(sp("kw", t))
                elif nxt == "(":
                    out.append(sp("fn", t))
                else:
                    out.append(esc(t))
            elif k == "ws":
                out.append(t.replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;").replace(" ", "&nbsp;").replace("\n", "<br>"))
            else:
                out.append(esc(t))
        return "".join(out)

