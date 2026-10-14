# -*- coding: utf-8 -*-
"""LaTeX → Qt 富文本转换（纯 Python，CI 可跑）。"""
import re

from houdini_agent.ui_qml.mathrender import extract_math, latex_to_html, restore_math


def plain(h):
    """去掉样式标签，只保留 <sup>/<sub>/<br>/<table> 之外的文字，便于断言。"""
    h = re.sub(r"<sup>(.*?)</sup>", r"^[\1]", h)
    h = re.sub(r"<sub>(.*?)</sub>", r"_[\1]", h)
    h = h.replace("<br>", "\n")
    return re.sub(r"<[^>]+>", "", h)


def test_sum_frac_and_inequalities():
    out = plain(latex_to_html(r"e=\sum_{n=0}^{\infty}\frac1{n!}>\frac{1957}{720}>2.718", True))
    assert out == "e=∑_[n=0]^[∞]1/n!&gt;1957/720&gt;2.718"


def test_frac_parenthesizes_compound_parts():
    assert plain(latex_to_html(r"\frac{\sin x}{x}")) == "(sin x)/x"     # 函数名后是细空格
    assert plain(latex_to_html(r"\frac{x^2}{2}")) == "x^[2]/2"


def test_sqrt_symbols_and_mod():
    assert plain(latex_to_html(r"\sqrt{2}=\frac pq")) == "√2=p/q"
    assert plain(latex_to_html(r"\sqrt{x+1}")) == "√(x+1)"
    assert plain(latex_to_html(r"p\equiv 3\pmod 4")) == "p≡3\u2002(mod 4)"
    assert plain(latex_to_html(r"\alpha+\beta\le\gamma\times\infty")) == "α+β≤γ×∞"
    assert plain(latex_to_html(r"\mathbb{R}\to\mathbb{N}")) == "ℝ→ℕ"


def test_aligned_keeps_rows_and_boxed_gets_border_in_display():
    out = latex_to_html(r"\begin{aligned} a &>1\\ &>2 \end{aligned}", True)
    assert plain(out).count("\n") == 1
    boxed = latex_to_html(r"\boxed{e^e>15}", True)
    assert "<table border='1'" in boxed and "15" in boxed
    assert "<b>" in latex_to_html(r"\boxed{x}", False)          # 行内不画表格


def test_left_right_and_unknown_commands_degrade_gracefully():
    assert plain(latex_to_html(r"\left( x \right)")) == "(x)"
    assert plain(latex_to_html(r"\foo{y}")) == "fooy"
    assert plain(latex_to_html("a<b & c")) == "a&lt;b\u2002 c"      # & \u5bf9\u9f50\u7b26 \u2192 en \u7a7a\u683c


def test_extract_display_inline_and_dollar_rules():
    text = ("由于 \\(e-2>0.718\\)，所以\n\\[\ne^2>7.38.\n\\]\n"
            "路径 $HIP/geo 和 $F 不应被当成公式；但 $x^2+1$ 应该。代码 `\\(x\\)` 保持原样。")
    t, maths = extract_math(text)
    assert len(maths) == 3
    assert "$HIP/geo" in t and "$F" in t                  # Houdini 变量不动
    assert "`\\(x\\)`" in t                               # 行内代码不动
    assert "\\(" not in t.replace("`\\(x\\)`", "")


def test_display_math_has_no_extra_blank_lines_around_it():
    t, maths = extract_math("首先，\n\n\\[ a+b \\]\n\n因此")
    html = "<br>".join(t.split("\n"))                      # 模拟 _md_html 把每行拼成 <br>
    out = restore_math(html, maths)
    assert "<br><div" not in out and "</div><br>" not in out
    assert out.startswith("首先，") and out.endswith("因此")


def test_dollar_without_math_markers_is_left_alone():
    t, maths = extract_math("价格是 $5 到 $10")
    assert maths == [] and t == "价格是 $5 到 $10"
