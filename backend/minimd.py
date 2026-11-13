"""极简 Markdown → HTML 渲染器（纯标准库，无第三方依赖）。

支持：标题、段落、围栏代码块、行内代码、粗体、斜体、链接、无序/有序列表、
引用块、分隔线。数学公式 `$...$` 原样保留（无 KaTeX，遵循"无 CDN"降级）。
"""
from __future__ import annotations

import html as _html
import re


def render(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        # 围栏代码块 ```lang ... ```
        m = re.match(r"^\s*```\s*([\w+-]*)", line)
        if m:
            i += 1
            code_lines = []
            while i < n and not re.match(r"^\s*```\s*$", lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1
            out.append('<pre class="md-code"><code>%s</code></pre>'
                       % _html.escape("\n".join(code_lines)))
            continue

        if line.strip() == "":
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lv = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lv, _inline(m.group(2)), lv))
            i += 1
            continue

        # 分隔线
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 引用
        if re.match(r"^\s*>\s?", line):
            quote = []
            while i < n and re.match(r"^\s*>\s?", lines[i]):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline("\n".join(quote)))
            continue

        # 无序列表
        if re.match(r"^\s*[-*+]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % _inline(it) for it in items) + "</ul>")
            continue

        # 有序列表
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % _inline(it) for it in items) + "</ol>")
            continue

        # 段落
        para = [line]
        i += 1
        while i < n and lines[i].strip() != "" and not re.match(
                r"^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>|```|[-*_]{3,}\s*$)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % _inline("\n".join(para)))

    return "\n".join(out)


def _inline(text: str) -> str:
    text = _html.escape(text)
    # 保护数学公式 $...$ / $$...$$，交给前端 KaTeX 渲染，避免被粗体/斜体/链接正则破坏
    stash = []

    def _stash(m):
        stash.append(m.group(0))
        return "\x00M%d\x00" % (len(stash) - 1)

    text = re.sub(r"\$\$[^$]+\$\$|\$[^$]+\$", _stash, text)
    # 行内代码
    text = re.sub(r"`([^`]+)`", lambda m: "<code>%s</code>" % m.group(1), text)
    # 粗体
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # 斜体
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", text)
    # 链接
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    # 恢复数学公式
    text = re.sub("\x00M(\\d+)\x00", lambda m: stash[int(m.group(1))], text)
    # 换行
    text = text.replace("\n", "<br>")
    return text


# ---------------- 轻量 LaTeX 数学 ----------------
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
}
_SYMBOLS = {
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝",
    "infty": "∞", "sum": "Σ", "prod": "∏", "int": "∫", "partial": "∂",
    "nabla": "∇", "sqrt": "√", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "leftrightarrow": "↔",
    "cdots": "⋯", "ldots": "…", "dots": "…", "circ": "∘", "degree": "°",
    "in": "∈", "notin": "∉", "subset": "⊂", "supset": "⊃", "cup": "∪",
    "cap": "∩", "forall": "∀", "exists": "∃", "angle": "∠",
}


def render_math(latex: str) -> str:
    """把常见 LaTeX 数学转成 HTML（输入已做 HTML 转义）。"""
    s = latex
    # 去掉 \left \right
    s = re.sub(r"\\(?:left|right)\s*([.()\[\]{}|])", r"\1", s)
    s = re.sub(r"\\(?:left|right)", "", s)

    def _cmd(m):
        name = m.group(1)
        if name in _GREEK:
            return _GREEK[name]
        if name in _SYMBOLS:
            return _SYMBOLS[name]
        if name in ("text", "mathrm", "mathbf", "mathit", "operatorname"):
            return ""
        return m.group(0)  # 未知命令保持原样

    s = re.sub(r"\\([a-zA-Z]+)", _cmd, s)
    # 转义字符 \{ \} \_ \% \& \# \$
    s = re.sub(r"\\([_%&#{}$])", r"\1", s)
    # 上标 ^{...} / ^x
    s = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
    s = re.sub(r"\^([A-Za-z0-9])", r"<sup>\1</sup>", s)
    # 下标 _{...} / _x
    s = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", s)
    # 去掉残留花括号
    s = s.replace("{", "").replace("}", "")
    return s
