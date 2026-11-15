# -*- coding: utf-8 -*-
"""WIL/WIX 帧提取为 PNG（frontend/gfx/wil.mjs 的纯标准库 Python 移植）。

仅供静态站点生成使用（scripts/gen_website.py），不参与游戏运行时。
WIL = 8 位索引色（共享 256 色调色板）或 16 位 RGB565；WIX = 帧偏移表。
"""
from __future__ import annotations

import base64
import collections
import re
import struct
import zlib
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / 'frontend' / 'gfx'


def load_palette() -> bytes:
    """从 frontend/gfx/palette.mjs 读 256 色 RGBA 调色板（单一数据源）。"""
    text = (FRONTEND / 'palette.mjs').read_text(encoding='utf-8')
    b64 = re.search(r'_b64\s*=\s*"([^"]+)"', text).group(1)
    raw = base64.b64decode(b64)
    assert len(raw) == 1024, f'调色板长度 {len(raw)} != 1024'
    return raw


class WIL:
    def __init__(self, data_dir: Path, name: str):
        self.wil = (data_dir / f'{name}.wil').read_bytes()
        wix = (data_dir / f'{name}.WIX')
        if not wix.exists():
            wix = data_dir / f'{name}.wix'
        b = wix.read_bytes()
        self.count = struct.unpack_from('<I', b, 44)[0]
        self.offsets = [struct.unpack_from('<I', b, 48 + i * 4)[0] for i in range(self.count)]
        self.palette = load_palette()
        self._pal_table: dict[int, str] | None = None

    def _next_off(self, off: int) -> int:
        cand = [o for o in self.offsets if o > off]
        return min(cand) if cand else len(self.wil)

    def frame_rgba(self, index: int) -> tuple[int, int, bytes] | None:
        """返回 (w, h, rgba_bytes)；空帧/1x1 占位返回 None。"""
        if not (0 <= index < self.count):
            return None
        off = self.offsets[index]
        if off < 1080:
            return None
        width, height, _px, _py = struct.unpack_from('<HHhh', self.wil, off)
        if width <= 0 or height <= 0 or height == 1:
            return None
        p = off + 8
        next_off = self._next_off(off)
        bits = 16 if next_off - off >= 8 + width * height * 2 else 8
        need = width * height * (2 if bits == 16 else 1)
        if p + need > len(self.wil):
            return None
        rgba = bytearray(width * height * 4)
        pal = self.palette
        if bits == 8:
            pixel_len = next_off - off - 8
            row_pad = ((4 - width % 4) % 4)
            padded = pixel_len != width * height and pixel_len == (width + row_pad) * height
            if self._pal_table is None:
                # 索引色 → 4 字节 RGBA 的字符映射，整行一次 C 层 translate
                self._pal_table = {i: chr(pal[i * 4]) + chr(pal[i * 4 + 1])
                                   + chr(pal[i * 4 + 2]) + chr(pal[i * 4 + 3])
                                   for i in range(256)}
            table = self._pal_table
            stride = width + row_pad if padded else width
            for h in range(height):
                start = p + h * stride
                line = self.wil[start:start + width]
                o = (height - 1 - h) * width * 4
                rgba[o:o + width * 4] = (line.decode('latin-1').translate(table)
                                         .encode('latin-1'))
        else:
            for h in range(height - 1, -1, -1):
                for w in range(width):
                    v = self.wil[p] | (self.wil[p + 1] << 8)
                    p += 2
                    r, g, bl = (v & 0xF800) >> 8, (v & 0x7E0) >> 3, (v & 0x1F) << 3
                    i = (w + h * width) << 2
                    rgba[i:i + 4] = bytes((r, g, bl, 0 if (r or g or bl) == 0 else 255))
        return width, height, bytes(rgba)

    def frame_png(self, index: int) -> tuple[bytes, int, int] | None:
        """返回 (png_bytes, w, h)；空帧/1x1 占位返回 None。"""
        r = self.frame_rgba(index)
        if r is None:
            return None
        return _png(r[0], r[1], r[2]), r[0], r[1]

    def frame_avg(self, index: int) -> tuple[int, int, int, int] | None:
        """整帧平均色（按调色板 Alpha 加权）。8 位帧用 Counter 快速统计。"""
        if not (0 <= index < self.count):
            return None
        off = self.offsets[index]
        if off < 1080:
            return None
        width, height, _px, _py = struct.unpack_from('<HHhh', self.wil, off)
        if width <= 0 or height <= 0 or height == 1:
            return None
        p = off + 8
        next_off = self._next_off(off)
        bits = 16 if next_off - off >= 8 + width * height * 2 else 8
        pixel_len = next_off - off - 8
        need = width * height * (2 if bits == 16 else 1)
        if p + need > len(self.wil):
            return None
        pal = self.palette
        if bits == 8:
            row_pad = ((4 - width % 4) % 4)
            padded = pixel_len != width * height and pixel_len == (width + row_pad) * height
            if padded:   # 行尾对齐帧：逐行统计
                sr = sg = sb = sa = tot = 0
                for h in range(height):
                    row = self.wil[p + h * (width + row_pad):p + h * (width + row_pad) + width]
                    for ci_b, cnt in collections.Counter(row).items():
                        ci = ci_b << 2
                        a = pal[ci + 3]
                        sr += pal[ci] * a * cnt
                        sg += pal[ci + 1] * a * cnt
                        sb += pal[ci + 2] * a * cnt
                        sa += a * cnt
                        tot += cnt
            else:
                row = self.wil[p:p + min(need, len(self.wil) - p)]
                sr = sg = sb = sa = tot = 0
                for ci_b, cnt in collections.Counter(row).items():
                    ci = ci_b << 2
                    a = pal[ci + 3]
                    sr += pal[ci] * a * cnt
                    sg += pal[ci + 1] * a * cnt
                    sb += pal[ci + 2] * a * cnt
                    sa += a * cnt
                    tot += cnt
        else:
            sr = sg = sb = sa = tot = 0
            for k in range(p, p + need, 2):
                v = self.wil[k] | (self.wil[k + 1] << 8)
                r, g, bl = (v & 0xF800) >> 8, (v & 0x7E0) >> 3, (v & 0x1F) << 3
                a = 0 if (r or g or bl) == 0 else 255
                sr += r * a
                sg += g * a
                sb += bl * a
                sa += a
                tot += 1
        if not tot or not sa:
            return None
        return (sr // sa, sg // sa, sb // sa, min(255, sa // tot))


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(width: int, height: int, rgba: bytes) -> bytes:
    raw = b''.join(b'\x00' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(raw, 9))
            + _chunk(b'IEND', b''))