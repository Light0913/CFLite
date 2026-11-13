"""下载 KaTeX 到 web/static/katex/。

项目已内置 KaTeX（web/static/katex/），本脚本用于在其他机器、或文件缺失时重新获取。
用法：python tools/get_katex.py
"""
import pathlib
import re
import urllib.request

DEST = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "katex"
BASES = [
    "https://cdn.bootcdn.net/ajax/libs/KaTeX/0.16.11/",
    "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/",
    "https://unpkg.com/katex@0.16.11/dist/",
]


def fetch(rel):
    for base in BASES:
        try:
            req = urllib.request.Request(base + rel, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            continue
    return None


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "fonts").mkdir(exist_ok=True)
    for rel, out in [("katex.min.js", "katex.min.js"),
                     ("katex.min.css", "katex.min.css"),
                     ("contrib/auto-render.min.js", "auto-render.min.js")]:
        data = fetch(rel)
        if data is None:
            print("FAIL", rel)
            return 1
        (DEST / out).write_bytes(data)
        print("ok", out, len(data))

    css = (DEST / "katex.min.css").read_text("utf-8")
    fonts = sorted(set(re.findall(r"fonts/([A-Za-z0-9_\-.]*\.(?:woff2|woff|ttf))", css)))
    for fn in fonts:
        data = fetch("fonts/" + fn)
        if data is None:
            print("FAIL font", fn)
            continue
        (DEST / "fonts" / fn).write_bytes(data)
    print("fonts:", len(fonts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
