# -*- coding: utf-8 -*-
"""把 gen_website.py 产物无损压缩成适合 list2 发布的静态站。

背景：原始产物 data/website 约 67MB，超过 list2 发布脚本 60MB 上限。
地图渲染图是 RGBA 但颜色数很少（≤256），转成 P 调色板可大幅缩小体积。

关键约束：**必须无损**。
- 不能用 Image.quantize(colors=256, method=ADAPTIVE)——它会重新量化改色；
- 必须用『图像里实际出现的 RGB 集合排序后做精确调色板』；
- 转成 P 后回读 RGB 字节，逐字节校验与原图完全一致才写盘。

带 alpha 或颜色数 >256 的图（物品/怪物图标等）原样复制，不转换。

用法：
  python scripts/compress_website.py                       # data/website -> data/website-list2
  python scripts/compress_website.py --src X --dst Y       # 自定义目录
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def compress_png(src: Path, dst: Path) -> tuple[str, int, int]:
    """返回 (状态, 原字节, 新字节)。状态: palette / copy。"""
    raw = src.read_bytes()
    with Image.open(src) as im:
        if im.mode != "RGBA":
            dst.write_bytes(raw)
            return ("copy", len(raw), len(raw))
        rgba = im.convert("RGBA")
        data = rgba.tobytes()  # R,G,B,A 交错，长度 = w*h*4
        # 只压缩全不透明的图（地图）；含 alpha 的图标原样保留
        if any(data[i] != 255 for i in range(3, len(data), 4)):
            dst.write_bytes(raw)
            return ("copy", len(raw), len(raw))
        rgb = bytes(b for i, b in enumerate(data) if i % 4 != 3)
        colors = sorted({rgb[i:i + 3] for i in range(0, len(rgb), 3)})
        if len(colors) > 256:
            dst.write_bytes(raw)
            return ("copy", len(raw), len(raw))
        # 精确调色板：index = colors 顺序，颜色零改动；用 bytes.translate 做向量化映射
        idx_of = {c: i for i, c in enumerate(colors)}
        indices = bytes(idx_of[rgb[i:i + 3]] for i in range(0, len(rgb), 3))
        pal = Image.frombytes("P", rgba.size, indices)
        palette = bytearray(768)
        for i, c in enumerate(colors):
            palette[i * 3:i * 3 + 3] = c
        pal.putpalette(bytes(palette))
        # 回读 RGB 校验逐字节一致（无损铁律）
        back = pal.convert("RGB").tobytes()
        if back != rgb:
            dst.write_bytes(raw)
            return ("copy", len(raw), len(raw))
        pal.save(dst, format="PNG", optimize=True)
    return ("palette", len(raw), dst.stat().st_size)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="data/website")
    ap.add_argument("--dst", default="data/website-list2")
    a = ap.parse_args()
    src = (ROOT / a.src).resolve()
    dst = (ROOT / a.dst).resolve()
    if not (src / "index.html").is_file():
        raise SystemExit(f"源目录不是 wiki 产物（缺 index.html）: {src}")

    dst.mkdir(parents=True, exist_ok=True)
    n_pal = n_copy = n_file = 0
    before = after = 0
    src_files: set[str] = set()
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        src_files.add(rel.as_posix())
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix.lower() == ".png":
            status, b0, b1 = compress_png(f, target)
            if status == "palette":
                n_pal += 1
            else:
                n_copy += 1
        else:
            shutil.copyfile(f, target)
            b0 = b1 = f.stat().st_size
        before += b0
        after += b1
        n_file += 1
        if b1 == 0:
            raise SystemExit(f"空文件: {target}")

    # 清掉目标里源已不存在的孤儿文件（剥离掉的私服页/图）
    removed = []
    for f in sorted(dst.rglob("*")):
        if f.is_file() and f.relative_to(dst).as_posix() not in src_files:
            f.unlink()
            removed.append(f.relative_to(dst).as_posix())
    print(f"文件 {n_file} · 精确调色板无损压缩 {n_pal} 张 · 原样保留 {n_copy} 张")
    print(f"体积 {before/1048576:.1f}MB -> {after/1048576:.1f}MB")
    print(f"清理孤儿文件 {len(removed)} 个" + ("：" + ", ".join(removed[:8]) if removed else ""))


if __name__ == "__main__":
    main()
