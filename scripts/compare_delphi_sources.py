#!/usr/bin/env python3
"""对比两套 Mir2 Delphi 源码（GEE/火炬 vs GameOfMir）的目录与单元文件差异。

默认：
  火炬 = D:/work/me/game/传奇/火炬源码+配套服务端/Source
  GameOfMir = D:/work/me/game/传奇/MIR2/GameOfMir

运行（必须用 D:/Python310/python.exe）：
  python scripts/compare_delphi_sources.py --json data/delphi_source_diff.json

只读，不改动任一源码树。GBK/GB18030 自动解码；同名单元按字节哈希 + 规范化行相似度比较。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path

DEFAULT_TORCH = Path(r"D:\work\me\game\传奇\火炬源码+配套服务端\Source")
DEFAULT_GAM = Path(r"D:\work\me\game\传奇\MIR2\GameOfMir")

# 火炬 Source 子目录 -> GameOfMir 对应子目录（按优先级）
PAIRS = {
    "Client": ("MirClient", "Client"),
    "M2Server": ("M2Server",),
    "Common": ("Common",),
    "DBsrv200": ("DBServer",),
    "LoginGate": ("LoginGate",),
    "Logsrv": ("LoginSrv", "LogDataServer"),
    "RunGate": ("RunGate",),
    "SelGate": ("SelGate",),
}


def decode(raw: bytes) -> str:
    for enc in ("gb18030", "gbk", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def norm_lines(path: Path) -> list[str]:
    lines = []
    for line in decode(path.read_bytes()).splitlines():
        s = line.strip().lower()
        if s:
            lines.append(s)
    return lines


def collect(root: Path, sub: str) -> dict[str, Path]:
    d = root / sub
    if not d.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(d.rglob("*.pas")):
        out.setdefault(p.name.lower(), p)
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_set(ta: dict[str, Path], ga: dict[str, Path]) -> dict:
    common = sorted(set(ta) & set(ga))
    rows = []
    for name in common:
        t, g = ta[name], ga[name]
        th, gh = sha(t), sha(g)
        tl, gl = norm_lines(t), norm_lines(g)
        ratio = difflib.SequenceMatcher(None, tl, gl, autojunk=False).ratio()
        rows.append({
            "unit": name,
            "torch": str(t),
            "gameofmir": str(g),
            "identical": th == gh,
            "torch_lines": len(tl),
            "gam_lines": len(gl),
            "similarity": round(ratio, 3),
        })
    return {
        "torch_only": sorted(set(ta) - set(ga)),
        "gam_only": sorted(set(ga) - set(ta)),
        "common": rows,
        "counts": {"torch": len(ta), "gam": len(ga), "common": len(common)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch", default=str(DEFAULT_TORCH))
    ap.add_argument("--gam", default=str(DEFAULT_GAM))
    ap.add_argument("--json", default=None, help="输出 JSON 路径")
    args = ap.parse_args()

    torch_root, gam_root = Path(args.torch), Path(args.gam)
    result: dict = {"torch_root": str(torch_root), "gam_root": str(gam_root), "pairs": {}}
    for tsub, gsubs in PAIRS.items():
        ta = collect(torch_root, tsub)
        ga: dict[str, Path] = {}
        for gsub in gsubs:
            for k, v in collect(gam_root, gsub).items():
                ga.setdefault(k, v)
        if not ta and not ga:
            continue
        result["pairs"][tsub] = compare_set(ta, ga)

    # 全量统计
    all_t = [p for p in torch_root.rglob("*.pas")]
    all_g = [p for p in gam_root.rglob("*.pas")]
    result["totals"] = {"torch_pas": len(all_t), "gam_pas": len(all_g)}

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"written: {out}")
    else:
        print(text)

    for sub, data in result["pairs"].items():
        c = data["counts"]
        ident = sum(1 for r in data["common"] if r["identical"])
        near = sum(1 for r in data["common"] if r["similarity"] >= 0.9 and not r["identical"])
        print(f"{sub:10s} torch={c['torch']:3d} gam={c['gam']:3d} common={c['common']:3d} "
              f"identical={ident:3d} near>=0.9={near:3d} torch_only={len(data['torch_only']):2d} gam_only={len(data['gam_only']):2d}")
    print(f"TOTAL pas: torch={result['totals']['torch_pas']} gam={result['totals']['gam_pas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
