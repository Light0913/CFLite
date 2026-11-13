"""CFLite 全局路径与配置。

项目根 = 本文件所在目录的上一级（即 CFLite/ 目录本身）。
MinGW-w64 编译器按以下顺序探测：环境变量 MINGW_HOME -> D:\\mingw64 -> C:\\Program Files\\MinGW64 -> PATH。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

# 让 `python main.py` 直接运行时也能 import backend / vendor
ROOT = pathlib.Path(__file__).resolve().parent.parent

_VENDOR = ROOT / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _gcc_version(gxx: pathlib.Path) -> str:
    try:
        import subprocess
        out = subprocess.run(
            [str(gxx), "--version"], capture_output=True, text=True, timeout=20
        ).stdout
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def detect_mingw() -> pathlib.Path:
    """返回 MinGW-w64 根目录（内含 bin/g++.exe）。

    探测顺序：环境变量 MINGW_HOME -> 常见安装目录 -> PATH 中的 g++。
    """
    candidates: list[str] = []
    env = os.environ.get("MINGW_HOME")
    if env:
        candidates.append(env)
    candidates += [
        r"D:\mingw64",
        r"C:\mingw64",
        r"C:\Program Files\MinGW64",
        r"C:\Program Files\mingw64",
        r"C:\Program Files (x86)\mingw64",
        r"C:\msys64\mingw64",
        r"C:\msys64\ucrt64",
        r"C:\w64devkit",
    ]
    for c in candidates:
        p = pathlib.Path(c)
        if (p / "bin" / "g++.exe").is_file():
            return p.resolve()
    g = shutil.which("g++")
    if g:
        # 例如 C:\...\MinGW64\bin\g++.exe -> C:\...\MinGW64
        return pathlib.Path(g).resolve().parent.parent
    raise RuntimeError(
        "未找到 MinGW-w64（g++.exe）。请安装 winlibs UCRT 包（例如解压到 D:\\mingw64），"
        "或设置环境变量 MINGW_HOME 指向其根目录（需包含 bin\\g++.exe）。"
    )


MINGW_HOME = detect_mingw()
GXX = MINGW_HOME / "bin" / "g++.exe"
GCC_VERSION_LINE = _gcc_version(GXX)

# 目录表
DIRS = {
    "root": ROOT,
    "bin": ROOT / "bin",
    "judge_src": ROOT / "judge",
    "build": ROOT / "build",
    "temp": ROOT / "temp",
    "problems": ROOT / "problems",
    "contests": ROOT / "contests",
    "submissions": ROOT / "submissions",
    "hacks": ROOT / "hacks",
    "data": ROOT / "data",
    "web": ROOT / "web",
    "templates": ROOT / "web" / "templates",
    "static": ROOT / "web" / "static",
    "tools": ROOT / "tools",
}

USERS_FILE = ROOT / "users.json"
DB_FILE = ROOT / "data" / "cflite.db"
JUDGE_EXE = ROOT / "bin" / "judge.exe"

CPU_CORES = max(1, os.cpu_count() or 4)

# 输出上限（字节），超过判 OLE
OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
# checker_comment 截断长度
CHECKER_COMMENT_LIMIT = 4096


def ensure_dirs() -> None:
    for key in ("bin", "build", "temp", "problems", "contests", "submissions", "hacks", "data"):
        DIRS[key].mkdir(parents=True, exist_ok=True)
    for sub in ("checkers", "interactors", "validators", "solutions"):
        (DIRS["build"] / sub).mkdir(parents=True, exist_ok=True)
    (ROOT / "web" / "templates").mkdir(parents=True, exist_ok=True)
    (ROOT / "web" / "static").mkdir(parents=True, exist_ok=True)


def long_path(p) -> str:
    """Windows 超长路径前缀。"""
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + s
    return s
