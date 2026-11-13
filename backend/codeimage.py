"""把源码渲染成 PNG 图片（Windows GDI，纯 ctypes，无第三方依赖）。

用途：比赛期间选手锁题后查看他人代码时，返回图片而非文本，防止直接复制。
管理员 / 本人查看时仍返回可复制文本（由调用方决定）。
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_HDC = wintypes.HANDLE
_HBITMAP = wintypes.HANDLE
_HGDIOBJ = wintypes.HANDLE
_HFONT = wintypes.HANDLE

_BI_RGB = 0
_DIB_RGB_COLORS = 0
_TRANSPARENT = 1
_FW_NORMAL = 400
_DEFAULT_CHARSET = 1
_FIXED_PITCH = 1


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_gdi32.CreateCompatibleDC.restype = _HDC
_gdi32.CreateCompatibleDC.argtypes = [_HDC]
_gdi32.CreateDIBSection.restype = _HBITMAP
_gdi32.CreateDIBSection.argtypes = [_HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
                                     ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.SelectObject.restype = _HGDIOBJ
_gdi32.SelectObject.argtypes = [_HDC, _HGDIOBJ]
_gdi32.CreateFontW.restype = _HFONT
_gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPCWSTR]
_gdi32.TextOutW.argtypes = [_HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
_gdi32.SetTextColor.argtypes = [_HDC, wintypes.COLORREF]
_gdi32.SetBkMode.argtypes = [_HDC, ctypes.c_int]
_gdi32.DeleteObject.argtypes = [_HGDIOBJ]
_gdi32.DeleteDC.argtypes = [_HDC]


def _rgb(r, g, b):
    return r | (g << 8) | (b << 16)


def _png(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        raw += rgb[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def render_png(text: str, font_size: int = 15, max_lines: int = 1200, max_chars: int = 250) -> bytes | None:
    """把文本渲染成 PNG 字节。失败返回 None。"""
    try:
        return _render(text, font_size, max_lines, max_chars)
    except Exception:
        return None


def _render(text, font_size, max_lines, max_chars):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["... (truncated)"]
    lines = [ln[:max_chars] for ln in lines]

    char_w = max(1, int(font_size * 0.6))
    line_h = int(font_size * 1.45)
    pad = 12
    max_len = max((len(ln) for ln in lines), default=1)
    width = max(1, max_len * char_w + pad * 2)
    height = max(1, len(lines) * line_h + pad * 2)

    hdc = _gdi32.CreateCompatibleDC(None)
    if not hdc:
        raise OSError("CreateCompatibleDC failed")
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = _BI_RGB
    bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    if not hbmp:
        _gdi32.DeleteDC(hdc)
        raise OSError("CreateDIBSection failed")
    old_bmp = _gdi32.SelectObject(hdc, hbmp)

    nbytes = width * height * 4
    buf = (ctypes.c_ubyte * nbytes).from_address(bits.value)
    ctypes.memset(buf, 0xFF, nbytes)  # 白色背景（BGRA 全 0xFF）

    hfont = _gdi32.CreateFontW(-font_size, 0, 0, 0, _FW_NORMAL, 0, 0, 0, _DEFAULT_CHARSET,
                               0, 0, 0, _FIXED_PITCH, "Consolas")
    old_font = _gdi32.SelectObject(hdc, hfont) if hfont else None
    _gdi32.SetBkMode(hdc, _TRANSPARENT)
    _gdi32.SetTextColor(hdc, _rgb(20, 20, 20))
    for i, ln in enumerate(lines):
        _gdi32.TextOutW(hdc, pad, pad + i * line_h, ln, len(ln))

    raw_bgra = bytes(buf)
    r = raw_bgra[2::4]
    g = raw_bgra[1::4]
    b = raw_bgra[0::4]
    rgb = bytearray(width * height * 3)
    rgb[0::3] = r
    rgb[1::3] = g
    rgb[2::3] = b

    if old_font:
        _gdi32.SelectObject(hdc, old_font)
    if hfont:
        _gdi32.DeleteObject(hfont)
    _gdi32.SelectObject(hdc, old_bmp)
    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc)

    return _png(width, height, bytes(rgb))
