"""CFLite backend package."""
import pathlib
import sys

# 确保 vendor/ 在任何 backend 子模块（尤其是 fastapi/jinja2）导入前就在 sys.path 上
_ROOT = pathlib.Path(__file__).resolve().parent.parent
_VENDOR = _ROOT / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
