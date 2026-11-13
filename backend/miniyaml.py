"""极简 YAML 子集解析 / 生成（纯标准库，无第三方依赖）。

覆盖 CFLite 使用的格式：嵌套 dict、list、list-of-inline-dict、flow dict/list、标量、注释。
标量类型：str / int / float / bool / null。日期时间按字符串处理（不强制转 datetime）。
"""
from __future__ import annotations

import re

_BOOL_TRUE = {"true", "yes", "on"}
_BOOL_FALSE = {"false", "no", "off"}
_NULLS = {"null", "~", ""}


# ---------------- 解析 ----------------
def _strip_comment(line: str) -> str:
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        else:
            if ch in ('"', "'"):
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
    return "".join(out).rstrip()


def _split_flow(s: str):
    parts, cur, quote = [], [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        else:
            if ch in ('"', "'"):
                quote = ch
                cur.append(ch)
            elif ch == ",":
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts]


def _parse_key(k: str):
    k = k.strip()
    if len(k) >= 2 and k[0] == k[-1] and k[0] in ('"', "'"):
        return k[1:-1]
    if re.fullmatch(r"-?\d+", k):
        return int(k)
    return k


def _parse_scalar(s: str):
    s = s.strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    low = s.lower()
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    if low in _NULLS:
        return None
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x) for x in _split_flow(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        d = {}
        for part in _split_flow(inner):
            if ":" in part:
                k, v = part.split(":", 1)
                d[_parse_key(k)] = _parse_scalar(v)
        return d
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


def load(text: str):
    lines = []
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip()))
    if not lines:
        return None
    val, _ = _parse_block(lines, 0, lines[0][0])
    return val


def _parse_block(lines, i, indent):
    if i >= len(lines):
        return None, i
    if lines[i][1].startswith("- "):
        return _parse_list(lines, i, indent)
    return _parse_dict(lines, i, indent)


def _parse_dict(lines, i, indent):
    d = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or content.startswith("- "):
            break
        m = re.match(r"^([^:]+):(.*)$", content)
        if not m:
            i += 1
            continue
        key = _parse_key(m.group(1))
        rest = m.group(2).strip()
        if rest == "":
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                val, i = _parse_block(lines, i, lines[i][0])
                d[key] = val
            else:
                d[key] = None
        else:
            d[key] = _parse_scalar(rest)
            i += 1
    return d, i


def _parse_list(lines, i, indent):
    lst = []
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or not content.startswith("- "):
            break
        item = content[2:].strip()
        if item == "":
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                val, i = _parse_block(lines, i, lines[i][0])
                lst.append(val)
            else:
                lst.append(None)
        elif item.startswith("{") or item.startswith("["):
            lst.append(_parse_scalar(item))
            i += 1
        else:
            m = re.match(r"^([^:]+):(.*)$", item)
            if m and item[0] not in ('"', "'"):
                d = {}
                key = _parse_key(m.group(1))
                d[key] = _parse_scalar(m.group(2).strip())
                i += 1
                while i < len(lines) and lines[i][0] > indent:
                    sub = lines[i]
                    sm = re.match(r"^([^:]+):(.*)$", sub[1])
                    if sm:
                        d[_parse_key(sm.group(1))] = _parse_scalar(sm.group(2).strip())
                    i += 1
                lst.append(d)
            else:
                lst.append(_parse_scalar(item))
                i += 1
    return lst, i


# ---------------- 生成 ----------------
def dump(data) -> str:
    lines = []
    _dump_value(data, lines, 0)
    return "\n".join(lines) + "\n"


def _dump_value(v, lines, indent):
    pad = "  " * indent
    if isinstance(v, dict):
        for k, val in v.items():
            kstr = _dump_key(k)
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{pad}{kstr}:")
                _dump_value(val, lines, indent + 1)
            elif isinstance(val, dict):
                lines.append(f"{pad}{kstr}: {{}}")
            elif isinstance(val, list):
                lines.append(f"{pad}{kstr}: []")
            else:
                lines.append(f"{pad}{kstr}: {_dump_scalar(val)}")
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                if item and all(not isinstance(x, (dict, list)) for x in item.values()):
                    inner = ", ".join(f"{_dump_key(k)}: {_dump_scalar(val)}" for k, val in item.items())
                    lines.append(f"{pad}- {{{inner}}}")
                else:
                    lines.append(f"{pad}-")
                    _dump_value(item, lines, indent + 1)
            elif isinstance(item, list):
                lines.append(f"{pad}-")
                _dump_value(item, lines, indent + 1)
            else:
                lines.append(f"{pad}- {_dump_scalar(item)}")
    else:
        lines.append(f"{pad}{_dump_scalar(v)}")


def _dump_key(k):
    if isinstance(k, bool):
        return "true" if k else "false"
    if isinstance(k, int):
        return str(k)
    k = str(k)
    if _need_quote(k, is_key=True):
        return '"' + k.replace('"', '\\"') + '"'
    return k


def _dump_scalar(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _need_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _need_quote(s: str, is_key: bool = False) -> bool:
    if s == "" or s != s.strip():
        return True
    if s.lower() in {"true", "false", "yes", "no", "null", "~", "on", "off"}:
        return True
    for c in ':#{}[],&*!|>\'"%@`':
        if c in s:
            return True
    if not is_key and re.fullmatch(r"-?\d+(\.\d+)?", s):
        return True  # 值里的数字字符串需加引号，避免被解析成数字
    return False
