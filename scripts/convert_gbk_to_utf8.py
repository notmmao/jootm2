"""一键把游戏配置文本从 GBK 转成 UTF-8（保持原样可读，幂等）。

背景：``gamedata/Mir200/Envir`` 下的 MapInfo / MonGen / MerChant / NPC 脚本 /
``MonItems`` 都是老版 Mir200 的 GBK 文本；服务端 ``read_lines`` 现在按内容自动
识别编码（UTF-8 优先、回落 GBK），所以两种编码都能直接跑。这个脚本用于「统一
成 UTF-8」，方便用现代编辑器（VS Code / Excel / git diff）直接改中文。

用法（默认只预览，不动文件）::

    D:/Python310/python.exe scripts/convert_gbk_to_utf8.py
    D:/Python310/python.exe scripts/convert_gbk_to_utf8.py --apply
    D:/Python310/python.exe scripts/convert_gbk_to_utf8.py --apply --backup-dir gamedata/.gbk-backup
    D:/Python310/python.exe scripts/convert_gbk_to_utf8.py --check      # CI：有 GBK 就退出 1

判定规则：
- 带 BOM 的 UTF-8 → 已转换，跳过（不重复写）。
- 严格 UTF-8 解码成功 → 跳过（纯 ASCII 也算 UTF-8，无需处理）。
- UTF-8 解码失败、GBK 解码成功 → 需要转换。
- 两种都失败（二进制等）→ 报错并跳过，绝不写入。

转换只做「GBK 字节 → UTF-8 字节」，不碰换行（``\\r\\n`` 原样保留），也不改内容。
"""
from __future__ import annotations

import argparse
import codecs
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ROOTS = ("gamedata",)
DEFAULT_EXTS = (".txt", ".ini", ".cfg", ".lst", ".def")
SKIP_DIRS = {".git", "__pycache__", ".gbk-backup", "node_modules"}

GBK = "gbk"


def classify(raw: bytes) -> str:
    """返回 ``utf8`` / ``utf8-bom`` / ``gbk`` / ``unknown``。"""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf8-bom"
    if not raw:
        return "utf8"
    try:
        raw.decode("utf-8")
        return "utf8"
    except UnicodeDecodeError:
        pass
    try:
        raw.decode(GBK)
        return "gbk"
    except UnicodeDecodeError:
        return "unknown"


def iter_files(roots: list[Path], exts: tuple[str, ...]):
    for root in roots:
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                p = Path(dirpath) / name
                if p.suffix.lower() in exts:
                    yield p


def rel_to_root(path: Path) -> Path:
    """项目内文件用相对路径（备份目录保持原结构），项目外只取文件名。"""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return Path(path.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 GBK 配置文本转成 UTF-8（幂等）")
    ap.add_argument("paths", nargs="*", help="要扫描的目录/文件（默认 gamedata）")
    ap.add_argument("--ext", action="append", default=None,
                    help="扩展名（可多次，默认 .txt/.ini/.cfg/.lst/.def）")
    ap.add_argument("--apply", action="store_true", help="实际写文件（默认只预览）")
    ap.add_argument("--check", action="store_true",
                    help="只检查：仍存在 GBK 文件时退出码 1（适合 CI/提交前）")
    ap.add_argument("--backup-dir", default=None,
                    help="转换前把原文件备份到该目录（默认 gamedata/.gbk-backup/<时间戳>）")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = ap.parse_args(argv)

    exts = tuple(e if e.startswith(".") else "." + e for e in (args.ext or DEFAULT_EXTS))
    roots = [Path(p) for p in (args.paths or DEFAULT_ROOTS)]
    roots = [p if p.is_absolute() else ROOT / p for p in roots]
    if any(not p.exists() for p in roots):
        for p in roots:
            if not p.exists():
                print(f"[错误] 路径不存在：{p}", file=sys.stderr)
        return 2

    backup_dir = None
    if args.apply:
        backup_dir = Path(args.backup_dir) if args.backup_dir else (
            ROOT / "gamedata" / ".gbk-backup" / time.strftime("%Y%m%d-%H%M%S"))
        if not backup_dir.is_absolute():
            backup_dir = ROOT / backup_dir

    stats = {"gbk": 0, "utf8": 0, "utf8-bom": 0, "unknown": 0, "converted": 0}
    converted: list[Path] = []
    bom_files: list[Path] = []
    unknown_files: list[Path] = []

    for path in iter_files(roots, exts):
        try:
            raw = path.read_bytes()
        except OSError as e:
            print(f"[错误] 读不了 {path}: {e}", file=sys.stderr)
            stats["unknown"] += 1
            continue
        kind = classify(raw)
        stats[kind] += 1
        if kind == "gbk":
            if not args.quiet:
                print(f"[GBK ] {rel_to_root(path)}")
            converted.append(path)
            if args.apply:
                text = raw.decode(GBK)
                data = text.encode("utf-8")
                assert backup_dir is not None
                dst = backup_dir / rel_to_root(path)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
                path.write_bytes(data)
                stats["converted"] += 1
        elif kind == "utf8-bom":
            bom_files.append(path)
        elif kind == "unknown":
            unknown_files.append(path)
            print(f"[跳过] 既不是 UTF-8 也不是 GBK：{rel_to_root(path)}",
                  file=sys.stderr)

    print()
    print(f"扫描文件：{sum(stats.values())} 个（{', '.join(str(r) for r in roots)}）")
    print(f"  GBK   ：{stats['gbk']}")
    print(f"  UTF-8 ：{stats['utf8']}"
          + (f"（其中 {stats['utf8-bom']} 个带 BOM，read_lines 已兼容）" if bom_files else ""))
    print(f"  其他  ：{stats['unknown']}")
    if args.check:
        if stats["gbk"]:
            print(f"[检查失败] 仍有 {stats['gbk']} 个 GBK 文件，请先运行 --apply")
            return 1
        print("[检查通过] 全部是 UTF-8")
        return 0
    if not args.apply:
        if converted:
            print(f"\n预览模式：加 --apply 才会真正转换 {len(converted)} 个文件")
        else:
            print("\n无需转换（全部已是 UTF-8）")
        return 0
    print(f"\n已转换 {stats['converted']} 个文件 → UTF-8")
    if stats["converted"]:
        print(f"原文件备份在：{backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
