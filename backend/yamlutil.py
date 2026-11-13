"""YAML 读写封装：使用内置 miniyaml（纯标准库），保证任意机器可用、无编译依赖。"""
from __future__ import annotations

import os
import pathlib

from . import miniyaml


def load(path: pathlib.Path):
    """读取 YAML，返回 dict/list/标量。"""
    return miniyaml.load(path.read_text("utf-8"))


def dump_rt(data, path: pathlib.Path) -> None:
    """写回 YAML（原子替换）。"""
    text = miniyaml.dump(data)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump(data) -> str:
    return miniyaml.dump(data)
