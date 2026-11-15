# -*- coding: utf-8 -*-
"""对比 current 主树与 176 基线树，生成「疑似私服魔改」清单。

基线：``data/gamedata/176``（scripts/build_176_envir.py 用 ruchu8 站点数据
重构的 1.76 原版口径）。判定只看可复现的结构差异，不猜意图：

掉落（MonItems）
  G1 金币异常   —— 金币期望值（概率×数量）比 176 高 ≥2 倍且差额 ≥100
  G2 基线从未掉落 —— current 某怪掉的物品，176 全树没有任何怪掉过
  G2b 176 名称笔误 —— 176 同一物品写错名（物品库查无此名），不算 current 魔改
  G3 同怪新增/暴增 —— 176 同怪没有该物品，或掉率高出 ≥5 倍，且物品不便宜
  G4 低阶怪掉高阶物 —— 物品 NeedLevel ≥20 且怪物等级 ≤ 物品等级-10
  另附：MonItems 坏行（物品名不在物品库，永远不掉）、基线缺失统计

NPC 出售（Market_Def / [goods]）
  N1 高等级商品 —— 商店出售 NeedLevel ≥20 的物品
  N2 新增商品   —— 同名 NPC 在 176 不卖，且 Lv ≥10 或售价 ≥3000
  N3 私服独有 NPC —— current 有、176 没有同名 NPC

用法：
  python scripts/compare_mods.py                 # 生成 docs/MODS-REPORT.md
  python scripts/compare_mods.py --out x.md
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.gamedata import read_lines  # noqa: E402
from pyserver.npc_script import NpcScriptEngine, load_script  # noqa: E402
from pyserver.world_extra import load_npc_spawns  # noqa: E402

MAIN = ROOT / "gamedata"
OLD = ROOT / "data" / "gamedata" / "176"
GOLD = "金币"


# ---------------------------------------------------------------- 基础数据

def load_csv(tree: Path, name: str) -> list[dict]:
    with open(tree / "mud2" / "DB" / f"{name}.csv", encoding="utf-8-sig",
              newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(f)]


def num(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def load_items(tree: Path) -> dict[str, dict]:
    return {r["Name"]: r for r in load_csv(tree, "StdItems") if r.get("Name")}


def load_monster_lvl(tree: Path) -> dict[str, int]:
    # 主树列名是 Level（旧注释里的 Lvl 是 REDM2 库字段名）
    return {r["Name"]: num(r.get("Level")) for r in load_csv(tree, "Monster")
            if r.get("Name")}


def parse_rate(s: str) -> float | None:
    if "/" not in s:
        return None
    a, b = s.split("/", 1)
    n, m = num(a), num(b)
    return n / m if n > 0 and m > 0 else None


def load_drops(tree: Path) -> dict[str, list[tuple[float, str, int]]]:
    """怪物名 → [(概率, 物品, 数量)]；同名文件带尾空格也要归一。

    同一物品写多行时按最大概率合并（源数据里有重复行，不合并会刷屏）。
    """
    out: dict[str, list] = {}
    d = tree / "Mir200" / "Envir" / "MonItems"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.txt")):
        per: dict[str, tuple[float, int]] = {}
        for ln in read_lines(p):
            t = ln.strip()
            if not t or t.startswith(";"):
                continue
            parts = t.split()
            if len(parts) < 2:
                continue
            rate = parse_rate(parts[0])
            if rate is None:
                continue
            cnt = num(parts[2], 1) if len(parts) > 2 else 1
            if parts[1] not in per or rate > per[parts[1]][0]:
                per[parts[1]] = (rate, cnt)
        if per:
            out[p.stem.strip()] = [(rate, item, cnt)
                                   for item, (rate, cnt) in per.items()]
    return out


def bad_drop_names(drops: dict, items: dict) -> collections.Counter:
    """引用了物品库里不存在名字的掉落行（服务端查不到模板，永远不掉）。"""
    c = collections.Counter()
    for rows in drops.values():
        for _rate, item, _cnt in rows:
            if item != GOLD and item not in items:
                c[item] += 1
    return c


def spawn_maps(tree: Path) -> dict[str, set[str]]:
    """怪物名 → 出现在哪些地图（MonGen）。"""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for ln in read_lines(tree / "Mir200" / "Envir" / "MonGen.txt"):
        t = ln.strip()
        if not t or t.startswith(";"):
            continue
        p = t.split()
        if len(p) >= 4:
            out[p[3]].add(p[0])
    return out


# ---------------------------------------------------------------- NPC 商品

def load_goods(tree: Path) -> dict[tuple[str, str], dict]:
    """(地图, NPC 名) → {"script":…, "items":[(名, 价, NeedLv)]}。"""
    envir = tree / "Mir200" / "Envir"
    items = load_items(tree)

    def tpl(name: str):
        r = items.get(name)
        return (SimpleNamespace(index=num(r["Idx"]), name=name,
                                price=num(r["Price"])) if r else None)

    world = SimpleNamespace(config=SimpleNamespace(envir_dir=envir),
                            item_template_by_name=tpl)
    engine = NpcScriptEngine(world)
    out: dict[tuple[str, str], dict] = {}
    for s in load_npc_spawns(envir):
        if not s["merchant"]:
            continue
        key = (s["map_name"], s["name"].strip())
        if key in out:
            continue
        script = load_script(envir / "Market_Def", s["script_name"], s["map_name"])
        goods = engine.goods(SimpleNamespace(script=script)) if script else []
        out[key] = {"spawn": s, "items": [(g["name"], g["price"]) for g in goods]}
    return out


# ---------------------------------------------------------------- 判定

@dataclass
class Row:
    category: str
    where: str
    detail: str


def gold_expected(rows, monster: str) -> float:
    return sum(rate * cnt for rate, item, cnt in rows if item == GOLD)


def compare_drops(main: dict, old: dict, items: dict, lvl: dict,
                  maps: dict[str, set[str]],
                  typo_map: dict[str, str]) -> list[Row]:
    out: list[Row] = []
    old_any = collections.Counter(item for rows in old.values()
                                  for _r, item, _c in rows)
    typo_targets = {v: k for k, v in typo_map.items()}

    def where_of(mon: str) -> str:
        ms = sorted(maps.get(mon, []))
        if not ms:
            return f"{mon}（未刷）"
        s = "/".join(ms[:4]) + (f" 等{len(ms)}图" if len(ms) > 4 else "")
        return f"{mon}（{s}）"

    for mon in sorted(main):
        a = main[mon]
        b = old.get(mon, [])
        where = where_of(mon)
        ga, gb = gold_expected(a, mon), gold_expected(b, mon)
        if ga and (gb == 0 and ga >= 200 or gb and ga >= 2 * gb and ga - gb >= 100):
            fmt = lambda rows: "、".join(f"{rate:.0e}×{cnt}" for rate, it, cnt in rows
                                         if it == GOLD) or "无"
            out.append(Row("G1 金币异常", where,
                           f"current 期望 {ga:.0f} 金（{fmt(a)}） vs 176 {gb:.0f} 金"
                           f"（{fmt(b)}），{ga / gb:.1f}×" if gb else
                           f"current 期望 {ga:.0f} 金（{fmt(a)}），"
                           + ("176 无该怪掉落文件" if mon not in old
                              else "176 该怪不掉金币")))
        bmap = {it: rate for rate, it, _c in b}
        for rate, item, _cnt in a:
            if item == GOLD or item not in items:
                continue                       # 坏行单独统计
            it = items[item]
            price = num(it.get("Price"))
            need = num(it.get("NeedLevel"))
            if item not in old_any:
                typo = typo_targets.get(item)
                if typo:
                    out.append(Row("G2b 176 名称笔误", where,
                                   f"current {item}（{rate:.3g}）"
                                   f"；176 写的是 {typo}（物品库无此名）"))
                else:
                    out.append(Row("G2 基线从未掉落", where,
                                   f"{item}（{rate:.3g}，价 {price}，Lv{need}）"))
                continue
            if rate <= 0 or rate > 0.2:          # 只报高频，避免刷屏
                pass
            old_rate = bmap.get(item)
            if old_rate is None:
                if rate >= 0.02 and (price >= 500 or need >= 15):
                    out.append(Row("G3 同怪新增", where,
                                   f"{item}（{rate:.3g}，价 {price}，Lv{need}）；"
                                   + ("176 无该怪掉落文件" if mon not in old
                                      else "176 该怪不掉")))
            elif rate >= 5 * old_rate and rate >= 0.01 and (price >= 500 or need >= 10):
                out.append(Row("G3 掉率暴涨", where,
                               f"{item}（{rate:.3g} vs 176 {old_rate:.3g}，"
                               f"{rate / old_rate:.0f}×，价 {price}）"))
            if (need >= 20 and lvl.get(mon, 0) and need - lvl[mon] >= 10
                    and item not in bmap):        # 176 同怪也掉的算基线
                out.append(Row("G4 低阶怪掉高阶物", where,
                               f"{item}（Lv{need}，怪 Lv{lvl[mon]}，{rate:.3g}）"))
    return out


def compare_goods(main: dict, old: dict, items: dict) -> list[Row]:
    out: list[Row] = []
    for (mid, name), g in sorted(main.items()):
        old_items = {n for n, _p in old.get((mid, name), {}).get("items", [])}
        for item, price in g["items"]:
            it = items.get(item, {})
            need = num(it.get("NeedLevel"))
            where = f"{name}@{mid}"
            if need >= 20:
                b = "176 也卖" if item in old_items else "176 不卖"
                out.append(Row("N1 高等级商品", where,
                               f"{item}（Lv{need}，{price} 金；{b}）"))
            elif item not in old_items and (need >= 10 or price >= 3000):
                out.append(Row("N2 新增商品", where,
                               f"{item}（Lv{need}，{price} 金）"))
    return out


def extra_npcs(main: dict, old: dict) -> list[Row]:
    old_names = {n for _m, n in old}
    out = []
    for (mid, name) in sorted(main):
        if name not in old_names:
            out.append(Row("N3 176 无同名 NPC（需人工判断）", f"{name}@{mid}",
                           "176 无同名 NPC"))
    return out


# ---------------------------------------------------------------- 报告

def fmt_table(rows: list[Row], limit: int | None = None) -> list[str]:
    lines = ["| 位置 | 详情 |", "|---|---|"]
    shown = rows if limit is None else rows[:limit]
    lines += [f"| {r.where} | {r.detail} |" for r in shown]
    if limit is not None and len(rows) > limit:
        lines.append(f"| … | 其余 {len(rows) - limit} 条省略 |")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/MODS-REPORT.md")
    ap.add_argument("--limit", type=int, default=400,
                    help="每类最多列多少行（默认 400，0=不限制）")
    a = ap.parse_args()
    limit = a.limit or None

    items = load_items(MAIN)
    old_items = load_items(OLD)
    lvl = load_monster_lvl(MAIN)
    drops_a, drops_b = load_drops(MAIN), load_drops(OLD)
    maps = spawn_maps(MAIN)
    goods_a, goods_b = load_goods(MAIN), load_goods(OLD)

    # 176 掉落里引用了物品库不存在名字的行：若与某个现有物品名高度相似，
    # 视为基线笔误，不算 current 魔改（例：食人树的果实 → 食人树果实）。
    old_typo_names = {item for rows in drops_b.values()
                      for _r, item, _c in rows if item not in old_items}
    typo_map: dict[str, str] = {}
    for name in old_typo_names:
        close = difflib.get_close_matches(name, items.keys(), n=1, cutoff=0.85)
        if close:
            typo_map[name] = close[0]
    bad_a = bad_drop_names(drops_a, items)
    bad_b = bad_drop_names(drops_b, old_items)

    drop_rows = compare_drops(drops_a, drops_b, items, lvl, maps, typo_map)
    goods_rows = compare_goods(goods_a, goods_b, items)
    npc_rows = extra_npcs(goods_a, goods_b)

    cats: dict[str, list[Row]] = collections.defaultdict(list)
    for r in drop_rows + goods_rows + npc_rows:
        cats[r.category].append(r)

    order = ["G1 金币异常", "G2 基线从未掉落", "G2b 176 名称笔误",
             "G3 同怪新增", "G3 掉率暴涨", "G4 低阶怪掉高阶物",
             "N1 高等级商品", "N2 新增商品", "N3 176 无同名 NPC（需人工判断）"]
    cats["G3 同怪新增"].sort(key=lambda r: r.detail)
    counts = {k: len(cats.get(k, [])) for k in order}
    missing = [(m, len({i for _r, i, _c in b} - {i for _r, i, _c in drops_a.get(m, [])}))
               for m, b in drops_b.items()]
    missing = sorted(((m, n) for m, n in missing if n >= 5), key=lambda x: -x[1])

    total_rows = sum(len(drops_a[m]) for m in drops_a)
    old_rows = sum(len(v) for v in drops_b.values())
    lines = [
        "# 疑似私服魔改清单（current vs 176 基线）",
        "",
        "> 由 `scripts/compare_mods.py` 生成，可随时重跑；判定规则见脚本 docstring。",
        "> 基线 = `data/gamedata/176`（ruchu8 原版 1.76 口径）；current = 主树（178 私服数据）。",
        "",
        "## 数据规模",
        "",
        f"- MonItems 行数：current {total_rows} vs 176 {old_rows}",
        f"- 怪物掉落文件：current {len(drops_a)} vs 176 {len(drops_b)}",
        f"- 商人：current {len(goods_a)} vs 176 {len(goods_b)}",
        "",
        "## 汇总",
        "",
        "| 类别 | 条数 |",
        "|---|---|",
    ]
    lines += [f"| {k} | {counts[k]} |" for k in order]
    lines += ["", "## 分类明细", ""]
    for k in order:
        rows = cats.get(k, [])
        lines += [f"### {k}（{len(rows)}）", ""]
        if rows:
            lines += fmt_table(rows, limit)
        else:
            lines.append("（无）")
        lines.append("")
    lines += ["## 数据质量：MonItems 坏行（物品名不在物品库，服务端永远不掉）", "",
              f"- current：{sum(bad_a.values())} 行 / {len(bad_a)} 个名字",
              f"- 176：{sum(bad_b.values())} 行 / {len(bad_b)} 个名字", ""]
    if bad_a:
        lines += ["| current 坏名字 | 出现次数 | 判定 |", "|---|---|---|"]
        lines += [f"| {n} | {c} | "
                  + ("current 物品库缺定义（176 有）" if n in old_items
                     else "两套物品库都没有") + " |"
                  for n, c in bad_a.most_common(limit)]
        lines.append("")
    if bad_b:
        lines += ["| 176 坏名字 | 出现次数 |", "|---|---|"]
        lines += [f"| {n} | {c} |" for n, c in bad_b.most_common(limit)]
        lines.append("")
    lines += ["## 基线掉落缺失（176 有、current 该怪没有的物品数，≥5 才列）", "",
              "| 怪物 | 缺失物品数 |", "|---|---|"]
    lines += [f"| {m} | {n} |" for m, n in missing[:limit or None]]
    if limit and len(missing) > limit:
        lines.append(f"| … | 其余 {len(missing) - limit} 个怪物省略 |")

    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[mods] {out}")
    print("[mods] " + ", ".join(f"{k}={counts[k]}" for k in order))
    print(f"[mods] 缺失基线掉落怪物 {len(missing)} 个")


if __name__ == "__main__":
    main()