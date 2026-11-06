#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""传奇客户端资源体检工具（jootm2 专用，零第三方依赖）。

给一个「热血传奇客户端安装目录」，检查它能不能被本项目的前端解码器使用：
  1. Data/ 下的图库是不是 WIL/WIX 老格式（前端 gfx/wil.mjs 只解这个，不认 WZL/WZX）
  2. 必需的图库是否齐全、WIX 索引头是否合法
  3. 抽查 HANDOFF 里抓帧验证过的关键帧尺寸，用来判断版本帧号是否一致
  4. Map/ 下的 .map 对 MapInfo.txt 登记的地图覆盖率
  5. Wav/ 音效数量

用法：
  python scripts/verify_resources.py D:/Mir2Client
  python scripts/verify_resources.py D:/Mir2Client --json report.json
  python scripts/verify_resources.py --self-test      # 用合成数据自检脚本逻辑

退出码：0=可用 / 1=有警告（可能能用） / 2=硬失败（不能用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------- 常量

# 前端 gfx/wil.mjs 的约定
WIX_COUNT_OFFSET = 44          # imageCount: uint32 LE
WIX_TABLE_OFFSET = 48          # 每图 4 字节偏移表
WIL_HEADER_SIZE = 1080         # 小于此偏移的视为空图
IMAGE_HEADER_SIZE = 8          # width u16 / height u16 / px i16 / py i16

# 缺了会直接黑屏或报错的基础库
CORE_LIBS = [
    "tiles", "smtiles", "objects",       # 地图地砖 / 小地砖 / 对象层
    "hum", "hair", "weapon",             # 人物
    "npc", "items", "stateitem",         # NPC / 背包图标 / 装备图标
    "prguse", "prguse3", "chrsel", "magicon",  # UI
]
# 怪物库（GetMonImg 会按 appr 映射到 mon1~mon21）
MONSTER_LIBS = [f"mon{i}" for i in range(1, 22)]
# 重要但前端有兜底、或只在特定功能里用到的库
OPTIONAL_LIBS = ["effect", "dnitems", "mmap", "prguse2", "magic", "magic2"]

# HANDOFF 中抓帧验证过的关键帧：(库, 索引, 期望宽, 期望高, 说明)
# 宽/高为 None 表示只报告实测值、不参与版本判定。
EXPECTED_FRAMES = [
    ("prguse3", 29, 168, 234, "F10 装备页底图（HANDOFF §503）"),
    ("prguse", 370, 232, 325, "F9 状态窗底图（HANDOFF §264）"),
    ("mon5", 2669, 40, 26, "16 方向箭矢 dir16=6，2609+60（HANDOFF §436）"),
    ("mon18", 821, None, None, "神兽喷火首帧（HANDOFF §459，尺寸随方向变化）"),
]

EXPECTED_WAV_COUNT = 694       # HANDOFF §25 记录的本地音效数，仅作参考

MAPINFO_REL = Path("gamedata/Mir200/Envir/MapInfo.txt")


# ---------------------------------------------------------------- 工具函数

def find_dir(root: Path, name: str) -> Path | None:
    """大小写不敏感地找 root 下的直属子目录。"""
    if not root.is_dir():
        return None
    for p in root.iterdir():
        if p.is_dir() and p.name.lower() == name.lower():
            return p
    return None


def decode_gbk(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("gbk", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def parse_mapinfo(path: Path) -> list[str]:
    """按 WorldData.loadMapInfo 的规则提取 [mapId ...] 里的 mapId。"""
    if not path.is_file():
        return []
    ids = []
    for line in decode_gbk(path).splitlines():
        t = line.strip()
        if not t or t.startswith(";") or not t.startswith("["):
            continue
        close = t.find("]")
        if close < 0:
            continue
        inner = t[1:close].strip()
        parts = re.split(r"\s+", inner, maxsplit=1)
        if parts and parts[0]:
            ids.append(parts[0])
    return ids


def parse_wix(path: Path) -> tuple[int, list[int]] | None:
    """读 WIX，返回 (imageCount, offsets)。格式不符返回 None。"""
    try:
        with path.open("rb") as f:
            f.seek(WIX_COUNT_OFFSET)
            head = f.read(4)
            if len(head) < 4:
                return None
            count = struct.unpack("<I", head)[0]
            if count == 0 or count > 2_000_000:   # 明显不合理的头
                return None
            rest = f.read(count * 4)
    except OSError:
        return None
    if len(rest) < count * 4:
        return None
    offsets = list(struct.unpack(f"<{count}I", rest[: count * 4]))
    return count, offsets


def read_frame_size(wil: Path, offsets: list[int], index: int) -> tuple[int, int] | None:
    """按 WIL 图头读指定索引的宽高（不解码像素数据）。"""
    if index < 0 or index >= len(offsets):
        return None
    off = offsets[index]
    if off < WIL_HEADER_SIZE:
        return None                       # 空图
    try:
        size = wil.stat().st_size
        if off + IMAGE_HEADER_SIZE > size:
            return None
        with wil.open("rb") as f:
            f.seek(off)
            head = f.read(IMAGE_HEADER_SIZE)
        if len(head) < IMAGE_HEADER_SIZE:
            return None
        w, h = struct.unpack("<HH", head[:4])
        return (w, h)
    except OSError:
        return None


# ---------------------------------------------------------------- 体检主体

def verify(client_dir: Path, mapinfo: Path) -> dict:
    report: dict = {
        "client_dir": str(client_dir),
        "format": "unknown",
        "dirs": {},
        "libraries": {},
        "missing": {"core": [], "monster": [], "optional": []},
        "wix_errors": [],
        "frame_checks": [],
        "maps": {},
        "wavs": {},
        "errors": [],   # 硬失败
        "warnings": [], # 警告
    }

    if not client_dir.is_dir():
        report["errors"].append(f"客户端目录不存在：{client_dir}")
        return report

    data_dir = find_dir(client_dir, "Data")
    map_dir = find_dir(client_dir, "Map")
    wav_dir = find_dir(client_dir, "Wav")
    for label, d in (("Data", data_dir), ("Map", map_dir), ("Wav", wav_dir)):
        report["dirs"][label] = str(d) if d else None
        if d is None:
            report["errors"].append(f"缺少 {label}/ 目录（ResourcesIndexGenerator 要求 Data/Map/Wav 三者齐全）")
    if data_dir is None:
        return report

    # --- 图库清点 ---
    by_base: dict[str, dict[str, Path]] = {}
    for p in data_dir.iterdir():
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in (".wil", ".wix", ".wzl", ".wzx"):
            continue
        by_base.setdefault(p.stem.lower(), {})[ext] = p

    legacy = {b: v for b, v in by_base.items() if ".wix" in v and ".wil" in v}
    modern = {b: v for b, v in by_base.items() if ".wzx" in v and ".wzl" in v}
    report["libraries"] = {
        "legacy_wil_wix": sorted(legacy),
        "modern_wzl_wzx": sorted(modern),
        "legacy_count": len(legacy),
        "modern_count": len(modern),
    }
    if modern and not legacy:
        report["format"] = "wzl_wzx"
        report["errors"].append(
            "Data/ 里只有 .wzl/.wzx —— 前端 gfx/wil.mjs 只解 WIL/WIX 老格式，"
            "这套客户端装上去会「resources.json 生成成功但画面全空」。"
            "需要改用 2003–2005 的 1.76/1.80 老端，或先把 WZL 转成 WIL。"
        )
    elif legacy and modern:
        report["format"] = "mixed"
        report["warnings"].append(
            f"WIL/WIX 与 WZL/WZX 混用：{len(modern)} 个现代库会被索引但前端解不了，"
            "建议只保留 .wil/.wix 成对文件。"
        )
    elif legacy:
        report["format"] = "wil_wix"
    else:
        report["errors"].append("Data/ 下没有找到成对的 .wil+.wix 图库")

    # --- 必需库 ---
    for group, libs, key in (
        ("core", CORE_LIBS, "core"),
        ("monster", MONSTER_LIBS, "monster"),
        ("optional", OPTIONAL_LIBS, "optional"),
    ):
        report["missing"][key] = [x for x in libs if x not in legacy]
    if report["missing"]["core"]:
        report["errors"].append(
            "缺失基础图库：" + ", ".join(report["missing"]["core"])
        )
    if report["missing"]["monster"]:
        report["warnings"].append(
            f"缺失 {len(report['missing']['monster'])} 个怪物库："
            + ", ".join(report["missing"]["monster"])
            + "（对应 appr 的怪物会不显示）"
        )
    if report["missing"]["optional"]:
        report["warnings"].append(
            "缺失可选图库：" + ", ".join(report["missing"]["optional"])
        )

    # --- WIX 头合法性 ---
    for base in sorted(legacy):
        wix = legacy[base][".wix"]
        parsed = parse_wix(wix)
        if parsed is None:
            report["wix_errors"].append(f"{wix.name}: WIX 头不合法（偏移 44 的 imageCount 异常）")
            continue
        count, offsets = parsed
        valid = [o for o in offsets if o >= WIL_HEADER_SIZE]
        if not valid:
            report["wix_errors"].append(f"{wix.name}: {count} 张图全部是空图")
    if report["wix_errors"]:
        report["warnings"].append(f"{len(report['wix_errors'])} 个 WIX 索引异常（详见 wix_errors）")

    # --- 关键帧尺寸抽查 ---
    for lib, idx, ew, eh, note in EXPECTED_FRAMES:
        entry = {"lib": lib, "index": idx, "note": note,
                 "expected": [ew, eh] if ew else None, "actual": None, "status": "missing"}
        if lib in legacy:
            parsed = parse_wix(legacy[lib][".wix"])
            if parsed:
                _, offsets = parsed
                size = read_frame_size(legacy[lib][".wil"], offsets, idx)
                if size:
                    entry["actual"] = list(size)
                    if ew is None:
                        entry["status"] = "info"
                    elif size == (ew, eh):
                        entry["status"] = "ok"
                    else:
                        entry["status"] = "mismatch"
                else:
                    entry["status"] = "empty"
        report["frame_checks"].append(entry)
    mismatched = [c for c in report["frame_checks"] if c["status"] == "mismatch"]
    if mismatched:
        report["warnings"].append(
            f"{len(mismatched)} 个关键帧尺寸与 HANDOFF 记录不符 → 图库版本/帧号可能不一致："
            + "; ".join(f"{c['lib']}[{c['index']}] 期望 {c['expected']} 实测 {c['actual']}"
                       for c in mismatched)
        )
    ok_frames = [c for c in report["frame_checks"] if c["status"] in ("ok", "info")]
    report["frame_hits"] = f"{len(ok_frames)}/{len(EXPECTED_FRAMES)}"

    # --- 地图覆盖 ---
    map_files: dict[str, Path] = {}
    if map_dir:
        map_files = {p.stem.lower(): p for p in map_dir.iterdir()
                     if p.is_file() and p.suffix.lower() == ".map"}
    expected_ids = parse_mapinfo(mapinfo)
    missing_maps = [m for m in expected_ids if m.lower() not in map_files] if expected_ids else []
    report["maps"] = {
        "found": len(map_files),
        "expected": len(expected_ids),
        "missing_count": len(missing_maps),
        "missing_sample": missing_maps[:20],
        "mapinfo": str(mapinfo) if mapinfo.is_file() else None,
    }
    if not map_files:
        report["errors"].append("Map/ 下没有 .map 文件，服务端 MapFileReader 无法加载任何地图")
    elif expected_ids and len(missing_maps) > len(expected_ids) * 0.2:
        report["warnings"].append(
            f"地图覆盖率偏低：{len(expected_ids) - len(missing_maps)}/{len(expected_ids)}"
            "（超过 20% 的 MapInfo 地图没有对应 .map）"
        )

    # --- 音效 ---
    wav_count = 0
    if wav_dir:
        wav_count = sum(1 for p in wav_dir.iterdir()
                        if p.is_file() and p.suffix.lower() == ".wav")
    report["wavs"] = {"found": wav_count, "reference": EXPECTED_WAV_COUNT}
    if wav_count == 0:
        report["warnings"].append("Wav/ 下没有 .wav（游戏可运行但无音效）")

    return report


def print_report(r: dict) -> None:
    print("=" * 68)
    print("jootm2 客户端资源体检")
    print("=" * 68)
    print(f"目录   : {r['client_dir']}")
    if not r["dirs"]:
        for e in r["errors"]:
            print("  ✗ " + e)
        print("-" * 68)
        print("结论：不可用")
        return
    for k, v in r["dirs"].items():
        print(f"  {k:<5}: {v or '缺失'}")
    lib = r.get("libraries", {})
    print(f"格式   : {r['format']}  (WIL/WIX {lib.get('legacy_count', 0)} 组, "
          f"WZL/WZX {lib.get('modern_count', 0)} 组)")
    if r["format"] == "wzl_wzx":
        print("         ↑ 前端不支持，需换老端或转换格式")
    print()
    m = r.get("missing", {})
    print(f"基础库缺失 : {', '.join(m.get('core', [])) or '无'}")
    print(f"怪物库缺失 : {len(m.get('monster', []))} 个"
          + (f" ({', '.join(m['monster'][:8])}{'...' if len(m['monster']) > 8 else ''})"
             if m.get("monster") else ""))
    print(f"可选库缺失 : {', '.join(m.get('optional', [])) or '无'}")
    print()
    print(f"关键帧抽查（判断版本帧号是否一致，命中 {r.get('frame_hits', '0/0')}）：")
    for c in r.get("frame_checks", []):
        exp = f"期望 {c['expected'][0]}x{c['expected'][1]}" if c["expected"] else "仅报告"
        act = f"{c['actual'][0]}x{c['actual'][1]}" if c["actual"] else "—"
        mark = {"ok": "OK", "info": "信息", "mismatch": "不符", "empty": "空图",
                "missing": "缺库"}.get(c["status"], c["status"])
        print(f"  [{mark:<3}] {c['lib']}[{c['index']}]  {act:<12} {exp:<16} {c['note']}")
    print()
    mp = r.get("maps", {})
    print(f"地图   : 找到 {mp.get('found', 0)} 个 .map / MapInfo 登记 {mp.get('expected', 0)}，"
          f"缺 {mp.get('missing_count', 0)}")
    if mp.get("missing_sample"):
        print("         缺失样例：" + ", ".join(mp["missing_sample"][:10]))
    print(f"音效   : {r.get('wavs', {}).get('found', 0)} 个 wav（参考值 "
          f"{r.get('wavs', {}).get('reference', 0)}）")
    print()
    if r["errors"]:
        print("硬失败：")
        for e in r["errors"]:
            print("  ✗ " + e)
    if r["warnings"]:
        print("警告：")
        for w in r["warnings"]:
            print("  ! " + w)
    verdict = "不可用" if r["errors"] else ("可用（有警告）" if r["warnings"] else "可用")
    print("-" * 68)
    print(f"结论：{verdict}")


# ---------------------------------------------------------------- 自检

def make_fake_wil_pair(data_dir: Path, base: str, count: int,
                       frames: dict[int, tuple[int, int]]) -> None:
    """按 gfx/wil.mjs 的约定合成一组 WIL/WIX（8 位、无 padding）。"""
    wix = data_dir / f"{base}.wix"
    wil = data_dir / f"{base}.wil"
    blobs: dict[int, bytes] = {}
    for idx, (w, h) in frames.items():
        blobs[idx] = struct.pack("<HHhh", w, h, 0, 0) + bytes(w * h)
    offsets = [0] * count
    cursor = WIL_HEADER_SIZE
    body = bytearray()
    for idx in sorted(blobs):
        offsets[idx] = cursor
        body += blobs[idx]
        cursor += len(blobs[idx])
    header = bytearray(WIX_TABLE_OFFSET + count * 4)
    struct.pack_into("<I", header, WIX_COUNT_OFFSET, count)
    struct.pack_into(f"<{count}I", header, WIX_TABLE_OFFSET, *offsets)
    wix.write_bytes(bytes(header))
    wil.write_bytes(bytes(WIL_HEADER_SIZE) + bytes(body))


def self_test() -> int:
    print("运行自检（合成 WIL/WIX 数据）...")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        client = root / "Mir2Client"
        data = client / "Data"
        (client / "Map").mkdir(parents=True)
        (client / "Wav").mkdir(parents=True)
        data.mkdir(parents=True)
        make_fake_wil_pair(data, "prguse3", 40, {29: (168, 234)})
        make_fake_wil_pair(data, "prguse", 400, {370: (232, 325)})
        make_fake_wil_pair(data, "mon5", 3000, {2669: (40, 26)})
        make_fake_wil_pair(data, "mon18", 1000, {821: (64, 49)})
        for i, name in enumerate(CORE_LIBS):
            if name not in ("prguse3", "prguse"):
                make_fake_wil_pair(data, name, 2, {0: (8, 8)})
        for name in MONSTER_LIBS + OPTIONAL_LIBS:
            if name not in ("mon5", "mon18"):
                make_fake_wil_pair(data, name, 2, {0: (8, 8)})
        for mid in ("0", "0122", "3"):
            (client / "Map" / f"{mid}.map").write_bytes(b"\0" * 52)
        (client / "Wav" / "001.wav").write_bytes(b"RIFF")
        mi = root / "MapInfo.txt"
        mi.write_text("[0\t比奇省]\n[0122\t皇宫]\tFIGHT\n[3\t沙巴克]\n", encoding="gbk")

        r = verify(client, mi)
        print_report(r)

        # 负例：只有 WZL/WZX 的现代客户端必须被判定为不兼容
        modern_client = root / "ModernClient"
        mdata = modern_client / "Data"
        (modern_client / "Map").mkdir(parents=True)
        (modern_client / "Wav").mkdir(parents=True)
        mdata.mkdir(parents=True)
        for name in ("hum", "items", "prguse3"):
            (mdata / f"{name}.wzl").write_bytes(b"\0" * 64)
            (mdata / f"{name}.wzx").write_bytes(b"\0" * 64)
        bad = verify(modern_client, mi)
        print("\n负例（WZL/WZX 客户端）：")
        print(f"  格式={bad['format']}, 硬失败={len(bad['errors'])}")
        print("  " + (bad["errors"][0] if bad["errors"] else "（无）"))

        checks = [
            ("格式识别为 WIL/WIX", r["format"] == "wil_wix"),
            ("无硬失败", not r["errors"]),
            ("基础库无缺失", not r["missing"]["core"]),
            ("怪物库无缺失", not r["missing"]["monster"]),
            ("prguse3[29] = 168x234", r["frame_checks"][0]["status"] == "ok"),
            ("prguse[370] = 232x325", r["frame_checks"][1]["status"] == "ok"),
            ("mon5[2669] = 40x26", r["frame_checks"][2]["status"] == "ok"),
            ("地图覆盖 3/3", r["maps"]["missing_count"] == 0),
            ("WZL-only 被判不兼容", bad["format"] == "wzl_wzx" and bool(bad["errors"])),
            ("完整资源无警告", not r["warnings"]),
        ]
        print("\n自检断言：")
        ok = True
        for label, passed in checks:
            print(f"  {'PASS' if passed else 'FAIL'}  {label}")
            ok &= passed
        return 0 if ok else 2


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="jootm2 客户端资源体检")
    ap.add_argument("client_dir", nargs="?", help="热血传奇客户端安装目录（含 Data/Map/Wav）")
    ap.add_argument("--mapinfo", type=Path, default=None,
                    help=f"MapInfo.txt 路径（默认 {MAPINFO_REL}）")
    ap.add_argument("--json", type=Path, default=None, help="同时把报告写成 JSON")
    ap.add_argument("--self-test", action="store_true", help="用合成数据自检脚本逻辑")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.client_dir:
        ap.error("请给出客户端目录，或用 --self-test 自检")

    script_dir = Path(__file__).resolve().parent
    mapinfo = args.mapinfo or script_dir.parent / MAPINFO_REL
    r = verify(Path(args.client_dir).expanduser(), mapinfo)
    print_report(r)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 报告：{args.json}")
    return 2 if r["errors"] else (1 if r["warnings"] else 0)


if __name__ == "__main__":
    sys.exit(main())
