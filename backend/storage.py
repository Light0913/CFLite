"""存储层：提交按题目归档、Hack 输入归档、CRLF→LF 规范化。"""
from __future__ import annotations

import json
import pathlib
import zipfile

from . import config


def submission_dir(problem_id, submission_id) -> pathlib.Path:
    d = config.DIRS["submissions"] / str(problem_id) / f"sub-{submission_id:06d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_submission_source(problem_id: int, submission_id: int,
                           filename: str, content: bytes) -> pathlib.Path:
    d = submission_dir(problem_id, submission_id)
    p = d / filename
    p.write_bytes(content)
    return p


def write_meta(d: pathlib.Path, meta: dict) -> None:
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def read_meta(d: pathlib.Path) -> dict:
    p = d / "meta.json"
    return json.loads(p.read_text("utf-8")) if p.is_file() else {}


def normalize_newlines(content: bytes) -> bytes:
    """CRLF / 孤立 CR → LF（顺序不能反）。"""
    if b"\r" in content:
        content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return content


def hack_dir(problem_id: int) -> pathlib.Path:
    d = config.DIRS["hacks"] / str(problem_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_hack_input(problem_id: int, hack_id: int, content: bytes, meta: dict) -> pathlib.Path:
    d = hack_dir(problem_id)
    content = normalize_newlines(content)
    p = d / f"hack_{hack_id:06d}.in"
    p.write_bytes(content)
    (d / f"hack_{hack_id:06d}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- submit_answer zip 解压 ----------
MAX_ZIP_SIZE = 100 * 1024 * 1024
MAX_FILES = 100
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024


def extract_answer_zip(zip_path: pathlib.Path, dest: pathlib.Path) -> list[pathlib.Path]:
    """安全解压 submit_answer 的 .zip：拒绝路径穿越、符号链接、超限。"""
    if zip_path.stat().st_size > MAX_ZIP_SIZE:
        raise ValueError("zip 文件超过 100MB")
    dest.mkdir(parents=True, exist_ok=True)
    out_files: list[pathlib.Path] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("zip 内文件超过 100 个")
        for info in infos:
            name = info.filename
            # 拒绝路径穿越 / 绝对路径 / 目录
            if name.startswith(("/", "\\")) or ".." in name.split("/") or ".." in name.split("\\"):
                raise ValueError("非法路径：%s" % name)
            base = pathlib.PurePosixPath(name).name
            if not base or info.is_dir():
                continue
            if info.file_size + total > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("解压总量超过 200MB")
            # 符号链接（external_attr 高位）
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("不允许符号链接：%s" % name)
            data = zf.read(info)
            total += len(data)
            p = dest / base
            p.write_bytes(data)
            out_files.append(p)
    return out_files
