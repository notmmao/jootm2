#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比两套传奇客户端资源：1.76 原始端 vs resources(1.80 提取端)。

产出 JSON 明细到 data/client_diff_176_180.json，并打印摘要。
只做只读分析，不修改任何资源文件。

用法（务必用 Python 3.10）：
    D:/Python310/python.exe scripts/compare_client_versions.py
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "client_original" / "传奇1.76客户端" / "传奇私服1.76客户"   # 1.76
B = ROOT / "resources"                                                # 1.80 提取
OUT = ROOT / "data" / "client_diff_176_180.json"

WIX_COUNT_OFFSET = 44
WIX_TABLE_OFFSET = 48
WIL_HEADER_SIZE = 1080
IMAGE_HEADER_SIZE = 8


def md5_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def collect(d: Path) -> dict[str, dict]:
    """按小写文件名收集一层文件（含大小、扩展名、mtime）。"""
    out: dict[str, dict] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        st = p.stat()
        out[p.name.lower()] = {
            "name": p.name,
            "size": st.st_size,
            "ext": p.suffix.lower(),
            "mtime": int(st.st_mtime),
            "path": str(p.relative_to(ROOT)).replace("\\", "/"),
        }
    return out


def parse_wix(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            f.seek(WIX_COUNT_OFFSET)
            head = f.read(4)
            if len(head) < 4:
                return None
            count = struct.unpack("<I", head)[0]
            if count == 0 or count > 2_000_000:
                return None
            table = f.read(count * 4)
        if len(table) < count * 4:
            return None
        offsets = struct.unpack(f"<{count}I", table[: count * 4])
        valid = [o for o in offsets if o >= WIL_HEADER_SIZE]
        return {
            "count": count,
            "valid": len(valid),
            "empty": count - len(valid),
            "max_offset": max(offsets) if offsets else 0,
            "table_md5": hashlib.md5(table[: count * 4]).hexdigest(),
            "offsets": list(offsets),
        }
    except OSError:
        return None


def wav_info(path: Path) -> dict:
    info: dict = {"ok": False}
    try:
        with wave.open(str(path), "rb") as w:
            info = {
                "ok": True,
                "channels": w.getnchannels(),
                "rate": w.getframerate(),
                "width": w.getsampwidth(),
                "frames": w.getnframes(),
                "seconds": round(w.getnframes() / w.getframerate(), 3) if w.getframerate() else 0,
            }
    except Exception as e:  # 非 PCM / 损坏头
        info["error"] = type(e).__name__
    return info


def map_info(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            head = f.read(52)
        if len(head) < 4:
            return {"ok": False}
        w, h = struct.unpack("<HH", head[:4])
        size = path.stat().st_size
        stride = (size - 52) / (w * h) if w and h else 0
        return {
            "ok": True,
            "width": w,
            "height": h,
            "stride": round(stride, 3),
            "has_extra": bool(w and h and abs(stride - 12) > 0.001 and abs(stride - 14) > 0.001),
        }
    except (OSError, struct.error):
        return {"ok": False}


def compare_group(label: str, a: dict, b: dict) -> dict:
    common = sorted(set(a) & set(b))
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    same, size_diff, content_diff = [], [], []
    for k in common:
        x, y = a[k], b[k]
        if x["size"] != y["size"]:
            size_diff.append(k)
            continue
        x["md5"] = md5_of(ROOT / x["path"])
        y["md5"] = md5_of(ROOT / y["path"])
        if x["md5"] == y["md5"]:
            same.append(k)
        else:
            content_diff.append(k)
    # 独有文件也算 md5（便于后续识别「改名/换名复用」）
    for k in only_a + only_b:
        rec = a.get(k) or b.get(k)
        rec["md5"] = md5_of(ROOT / rec["path"])
    return {
        "label": label,
        "count_a": len(a),
        "count_b": len(b),
        "common": len(common),
        "same": same,
        "size_diff": size_diff,
        "content_diff": content_diff,
        "only_a": only_a,
        "only_b": only_b,
    }


def main() -> int:
    report: dict = {
        "a_dir": str(A.relative_to(ROOT)).replace("\\", "/"),
        "b_dir": str(B.relative_to(ROOT)).replace("\\", "/"),
        "groups": {},
        "libraries": {},
        "wav": {},
        "map": {},
        "misc": {},
    }

    files: dict[str, tuple[dict, dict]] = {}
    for label in ("Data", "Map", "Wav"):
        a, b = collect(A / label), collect(B / label)
        files[label] = (a, b)
        report["groups"][label] = compare_group(label, a, b)
        g = report["groups"][label]
        print(f"[{label}] 1.76={len(a)} 1.80={len(b)} 同名={g['common']} "
              f"完全相同={len(g['same'])} 大小不同={len(g['size_diff'])} "
              f"内容不同={len(g['content_diff'])} 1.76独有={len(g['only_a'])} "
              f"1.80独有={len(g['only_b'])}")

    # ---------------- 图库（WIL/WIX）结构 ----------------
    def libs_of(rec: dict) -> dict:
        out: dict[str, dict] = {}
        for k, v in rec.items():
            if v["ext"] not in (".wil", ".wix"):
                continue
            base = k.split(".", 1)[0]
            out.setdefault(base, {})[v["ext"]] = v
        return out

    la, lb = libs_of(files["Data"][0]), libs_of(files["Data"][1])
    lib_rows = []
    for base in sorted(set(la) | set(lb)):
        row: dict = {"lib": base}
        for side, src in (("a", la.get(base, {})), ("b", lb.get(base, {}))):
            entry: dict = {"has_wil": ".wil" in src, "has_wix": ".wix" in src}
            if ".wil" in src:
                entry["wil_size"] = src[".wil"]["size"]
                entry["wil_md5"] = src[".wil"].get("md5", "")
            if ".wix" in src:
                p = ROOT / src[".wix"]["path"]
                parsed = parse_wix(p)
                entry["wix_size"] = src[".wix"]["size"]
                if parsed:
                    entry.update({
                        "frames": parsed["count"],
                        "valid_frames": parsed["valid"],
                        "empty_frames": parsed["empty"],
                        "table_md5": parsed["table_md5"],
                        "max_offset": parsed["max_offset"],
                    })
            row[side] = entry
        lib_rows.append(row)
    report["libraries"] = lib_rows

    # ---------------- WAV 头信息 + 跨名内容复用 ----------------
    wa, wb = files["Wav"]
    wav_rows = {}
    for side, rec in (("a", wa), ("b", wb)):
        info = {}
        for k, v in rec.items():
            if v["ext"] == ".wav":
                info[k] = wav_info(ROOT / v["path"])
        wav_rows[side] = info
    report["wav"]["headers"] = wav_rows
    # md5 -> 名字（用于发现「换名但内容相同」的音效）
    for side, rec in (("a", wa), ("b", wb)):
        by_md5: dict[str, list[str]] = {}
        for k, v in rec.items():
            by_md5.setdefault(v.get("md5", ""), []).append(k)
        report["wav"][f"{side}_by_md5"] = {h: names for h, names in by_md5.items()}

    # ---------------- MAP 头信息 ----------------
    ma, mb = files["Map"]
    map_rows = {}
    for side, rec in (("a", ma), ("b", mb)):
        info = {}
        for k, v in rec.items():
            if v["ext"] == ".map":
                info[k] = map_info(ROOT / v["path"])
        map_rows[side] = info
    report["map"]["headers"] = map_rows

    # ---------------- 后处理产物 ----------------
    audio = collect(B / "Audio")
    report["misc"] = {
        "audio_mp3_count": len(audio),
        "audio_mp3_bytes": sum(v["size"] for v in audio.values()),
        "resources_json_bytes": (B / "resources.json").stat().st_size,
        "root_files_176": [v["name"] for v in collect(Path(str(A))).values()],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n明细已写出：{OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
