#!/usr/bin/env python3
"""CFLite 启动入口（FastAPI）。

用法（先执行 build.bat 完成构建，它会 pip 安装依赖并编译 judge.exe）：
    python main.py

可选环境变量：
    CFLITE_HOST    监听地址（默认 0.0.0.0）
    CFLITE_PORT    端口（默认 8000）
    CFLITE_PYTHON  可选：若依赖装在另一个 Python 里，指向那个解释器（自动切换）
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 离线依赖副本（build.bat 会尽量 pip 安装；vendor 作为兜底）
_VENDOR = ROOT / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _has_multipart() -> bool:
    for mod in ("python_multipart", "multipart"):
        try:
            __import__(mod)
            return True
        except Exception:
            continue
    return False


def _missing_deps() -> list[str]:
    """返回缺失的必需模块名（YAML 用内置 miniyaml，不需要 pyyaml）。"""
    required = ["fastapi", "uvicorn", "jinja2"]
    missing = []
    for mod in required:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not _has_multipart():
        missing.append("python-multipart")
    return missing


def _bootstrap() -> None:
    missing = _missing_deps()
    if not missing:
        return

    # 允许通过环境变量指定另一个解释器（不硬编码任何机器路径）
    envp = os.environ.get("CFLITE_PYTHON")
    if envp:
        try:
            me = os.path.abspath(sys.executable)
            cand = os.path.abspath(envp)
            if cand != me and pathlib.Path(cand).is_file():
                import subprocess
                rc = subprocess.call([cand, str(ROOT / "main.py"), *sys.argv[1:]])
                sys.exit(rc)
        except OSError:
            pass

    print("[CFLite] 缺少依赖: " + ", ".join(missing))
    print("  请先运行 build.bat（会自动 pip 安装），或手动执行：")
    print("    %s -m pip install -r requirements.txt" % sys.executable)
    print("  若依赖装在另一个 Python 里，可设置环境变量 CFLITE_PYTHON 指向它。")
    sys.exit(1)


def main() -> None:
    _bootstrap()
    import uvicorn

    from backend.app import create_app

    host = os.environ.get("CFLITE_HOST", "0.0.0.0")
    port = int(os.environ.get("CFLITE_PORT", "8000"))
    app = create_app()
    print("CFLite 启动成功，访问 http://127.0.0.1:%d" % port)
    # timeout_graceful_shutdown：SSE 等长连接不会主动断开，限制关服等待时间，避免 Ctrl+C 后卡很久
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
