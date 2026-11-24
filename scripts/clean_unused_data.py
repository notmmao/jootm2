# -*- coding: utf-8 -*-
"""清理「没有任何获取途径」的物品与「从不刷新」的怪物。

判定口径（任一命中即保留）：

物品（StdItems.csv）
  1. 掉落  —— Surviving 怪物的 MonItems/<怪名>.txt 掉落行
  2. 商店  —— Market_Def/*.txt [goods] 段
  3. 脚本  —— Envir 各 txt 的 `give <物品名>` 命令行、MakeItem.txt 行首
  4. 代码  —— pyserver/**.py 与 scripts/seed_template_chars.py 的字符串常量
              （ast 提取，注释与 docstring 不算——防「火炬」这类项目名误伤）
  5. 库存  —— data/jootm2.db characters.items/storage 里现有实例的 index

怪物（Monster.csv）
  1. MonGen.txt 第 4 列刷怪名
  2. GuardList.txt / Castle/*.txt 提到的名字（卫士、城门等体系怪）
  3. 代码字符串常量（召唤系：变异骷髅/神兽；autopilot AVOID_NAMES 等）

两遍法：先算存活怪物，物品引用只统计**存活怪物**的 MonItems——被清怪物
独有的掉落物跟着一起清。

产物：
  gamedata/mud2/DB/StdItems-NONE.csv    与 StdItems.csv 同列同序的备份
  gamedata/mud2/DB/Monster-NONE.csv     同上
  docs/CLEAN_UNUSED_REPORT.md           清理清单（按类别）
  **Idx 不重编号**：玩家库存按 index 引用，剩余行的 Idx 必须保持原值。

用法：
  py -3.10 scripts/clean_unused_data.py             # dry-run 报告
  py -3.10 scripts/clean_unused_data.py --apply     # 落盘（先自动备份 .bak）
  py -3.10 scripts/clean_unused_data.py --restore   # 从 NONE.csv 原样恢复
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "gamedata" / "mud2" / "DB"
ENV = ROOT / "gamedata" / "Mir200" / "Envir"
STD = DB_DIR / "StdItems.csv"
MON = DB_DIR / "Monster.csv"
STD_NONE = DB_DIR / "StdItems-NONE.csv"
MON_NONE = DB_DIR / "Monster-NONE.csv"
REPORT = ROOT / "docs" / "CLEAN_UNUSED_REPORT.md"
GAME_DB = ROOT / "data" / "jootm2.db"

sys.path.insert(0, str(ROOT))
from pyserver.gamedata import read_lines  # noqa: E402

CODE_FILES = sorted((ROOT / "pyserver").glob("*.py")) + [
    ROOT / "scripts" / "seed_template_chars.py",
]


def code_string_constants() -> set[str]:
    """提取代码里的字符串字面量（跳过 docstring），用于「代码按名字造物品/怪」引用。"""
    out: set[str] = set()
    for fp in CODE_FILES:
        if not fp.exists():
            continue
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        # 收集每个模块/类/函数的 docstring 节点 id，排除之
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                out.add(node.value)
    return out


def mon_gen_names() -> set[str]:
    names = set()
    for ln in read_lines(ENV / "MonGen.txt"):
        s = ln.strip()
        if not s or s.startswith(";"):
            continue
        t = s.split()
        if len(t) >= 6:
            names.add(t[3])
    return names


def envir_referenced_items() -> dict[str, set[str]]:
    """返回 {引用源类别: 物品名集合}。MonItems 部分由调用方按存活怪物过滤。"""
    shop, give, make = set(), set(), set()
    for fp in (ENV / "Market_Def").glob("*.txt"):
        in_g = False
        for ln in read_lines(fp):
            s = ln.strip().lower()
            if s.startswith("[goods]"):
                in_g = True
                continue
            if s.startswith("["):
                in_g = False
                continue
            if in_g and s and not s.startswith(";"):
                shop.add(s.split()[0])
    for sub in ("Npc_Def", "MapQuest_Def", "Market_Def", "Startup"):
        d = ENV / sub
        if not d.exists():
            continue
        for fp in d.rglob("*.txt"):
            for ln in read_lines(fp):
                s = ln.strip()
                if re.match(r"^give\s", s, re.I):
                    t = s.split()
                    if len(t) >= 2:
                        give.add(t[1])
    mk = ENV / "MakeItem.txt"
    if mk.exists():
        for ln in read_lines(mk):
            s = ln.strip()
            if s and not s.startswith(("[", ";")):
                make.add(s.split()[0])
    return {"shop": shop, "give": give, "make": make}


def mon_items_by_monster() -> dict[str, set[str]]:
    """{怪物名: 该怪掉落表里出现的物品名集合}（掉落表文件存在即收录，后按存活过滤）。"""
    out: dict[str, set[str]] = {}
    for fp in (ENV / "MonItems").glob("*.txt"):
        items = set()
        for ln in read_lines(fp):
            m = re.match(r"^\s*\d+/\d+\s+(\S+)", ln)
            if m:
                items.add(m.group(1))
        if items:
            out[fp.stem] = items
    return out


def guard_castle_names() -> set[str]:
    names = set()
    gl = ENV / "GuardList.txt"
    if gl.exists():
        for ln in read_lines(gl):
            s = ln.strip()
            if s and not s.startswith(";"):
                t = s.split()
                if t:
                    names.add(t[0])
    for fp in (ENV / "Castle").glob("*.txt"):
        for ln in read_lines(fp):
            # Castle 文本是 key=value，值里含怪物名（护卫/弓箭手等）
            s = ln.strip()
            m = re.match(r"^[^=]+=\s*([\u4e00-\u9fff\w]+)\s*$", s)
            if m:
                names.add(m.group(1))
    return names


def inventory_indices() -> set[int]:
    """玩家库存（items/storage JSON）引用的 StdItems Idx 集合。缺库返回空。"""
    if not GAME_DB.exists():
        return set()
    out: set[int] = set()
    try:
        conn = sqlite3.connect(f"file:{GAME_DB}?mode=ro", uri=True)
        import json as _json
        for (items, storage) in conn.execute("SELECT items, storage FROM characters"):
            for blob in (items, storage):
                if not blob:
                    continue
                try:
                    j = _json.loads(blob)
                except ValueError:
                    continue
                lst = j if isinstance(j, list) else []
                for it in lst:
                    if isinstance(it, dict) and "index" in it:
                        try:
                            out.add(int(it["index"]))
                        except (TypeError, ValueError):
                            pass
        conn.close()
    except sqlite3.Error:
        return set()
    return out


def load_rows(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        rows = [r for r in rdr if r and any(x.strip() for x in r)]
    return header, rows


def dump_rows(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正删除并写 NONE.csv")
    ap.add_argument("--restore", action="store_true", help="从 NONE.csv 恢复")
    args = ap.parse_args()

    if args.restore:
        for src, dst in ((STD_NONE, STD), (MON_NONE, MON)):
            if not src.exists():
                print(f"跳过 {src.name}（不存在）")
                continue
            hdr, rows = load_rows(src)
            mhdr, mrows = load_rows(dst)
            merged = mrows + rows
            merged.sort(key=lambda r: int(r[0]) if dst is STD and r[0].lstrip("-").isdigit() else 0)
            dump_rows(dst, mhdr, merged)
            print(f"恢复 {dst.name}: +{len(rows)} 行 → 共 {len(merged)} 行")
        return 0

    mon_hdr, mon_rows = load_rows(MON)
    std_hdr, std_rows = load_rows(STD)

    # ---- 第一遍：怪物去留 ----
    mg = mon_gen_names()
    gc = guard_castle_names()
    csc = code_string_constants()
    keep_mon, kill_mon = [], []
    for r in mon_rows:
        name = r[0]
        if name in mg:
            keep_mon.append((r, "MonGen 刷新"))
        elif name in gc:
            keep_mon.append((r, "守卫/城堡体系"))
        elif name in csc:
            keep_mon.append((r, "代码召唤/硬编码"))
        else:
            kill_mon.append(r)
    alive = {r[0] for r, _ in keep_mon}

    # ---- 第二遍：物品去留（MonItems 只看存活怪） ----
    refs = envir_referenced_items()
    mi = mon_items_by_monster()
    drop_live: set[str] = set()
    drop_dead_only: set[str] = set()
    for mname, items in mi.items():
        if mname in alive:
            drop_live |= items
        else:
            drop_dead_only |= items
    used_items = drop_live | refs["shop"] | refs["give"] | refs["make"] | csc
    inv_idx = inventory_indices()

    kill_std, std_reason = [], {}
    for r in std_rows:
        name, idx = r[0] and r[1] or "", int(r[0]) if r[0].isdigit() else -1
        if name in used_items:
            continue
        if idx in inv_idx:
            std_reason[name] = "玩家库存持有"
            continue
        if name in drop_dead_only:
            std_reason[name] = "仅被「不刷新的怪」掉落"
        kill_std.append(r)

    # 重名行（导入时 INSERT 后行覆盖前行）：主表已有同名保留行时，重复行一并清出
    seen = set()
    dup_rows = []
    for r in list(kill_std):
        seen.add(r[1])
    extra = []
    kept_names = {r[1] for r in std_rows if r[1] not in seen}
    for r in std_rows:
        if r[1] in kept_names and any(x[1] == r[1] for x in kill_std) and r not in kill_std:
            extra.append(r)

    print(f"怪物: 共 {len(mon_rows)} → 保留 {len(keep_mon)}, 清理 {len(kill_mon)}")
    print(f"物品: 共 {len(std_rows)} → 保留 {len(std_rows) - len(kill_std)}, 清理 {len(kill_std)}")
    print(f"玩家库存占用 idx: {len(inv_idx)} 个（自动保护）")
    print("\n== 清理怪物 ==")
    for r in kill_mon:
        print(f"  {r[0]}  (race {r[1] if len(r) > 1 else '?'}, lv {r[3] if len(r) > 3 else '?'})")
    print("\n== 清理物品 ==")
    for r in kill_std:
        why = std_reason.get(r[1], "无任何获取途径")
        print(f"  [{r[2]}] {r[1]}  —— {why}")
    if extra:
        print("\n== 重名被覆盖行（主表保留首行）==")
        for r in extra:
            print(f"  {r[1]} idx={r[0]}")

    # ---- 报告 ----
    rep = ["# 无获取途径数据清理报告", "",
           f"- 物品 {len(std_rows)} → 清 {len(kill_std)}（StdItems-NONE.csv），"
           f"怪物 {len(mon_rows)} → 清 {len(kill_mon)}（Monster-NONE.csv）",
           "- 保留判据：存活怪 MonItems 掉落 / Market_Def goods / give / MakeItem /"
           " 代码字符串常量 / 玩家库存 idx",
           "- 恢复：`py -3.10 scripts/clean_unused_data.py --restore`", "",
           "## 清理的怪物（从不刷新、无召唤引用）", ""]
    rep += [f"- {r[0]}" for r in kill_mon]
    rep += ["", "## 清理的物品（无任何获取途径）", ""]
    for r in kill_std:
        rep.append(f"- [{r[2]}] {r[1]} —— {std_reason.get(r[1], '无任何获取途径')}")
    REPORT.write_text("\n".join(rep) + "\n", encoding="utf-8")
    print(f"\n报告已写 {REPORT.relative_to(ROOT)}")

    if not args.apply:
        print("\n(dry-run) 加 --apply 落盘")
        return 0

    kill_idx = {r[0] for r in kill_std} | {r[0] for r in extra}
    kill_mon_names = {r[0] for r in kill_mon} | {r[0] for r in extra if False}
    # 备份主表
    for p in (STD, MON):
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
    # NONE 文件（保留原始行序与原 header 格式）
    std_none = [r for r in std_rows if r[0] in kill_idx]
    mon_none = [r for r in mon_rows if r[0] in kill_mon_names]
    dump_rows(STD_NONE, std_hdr, std_none)
    dump_rows(MON_NONE, mon_hdr, mon_none)
    dump_rows(STD, std_hdr, [r for r in std_rows if r[0] not in kill_idx])
    dump_rows(MON, mon_hdr, [r for r in mon_rows if r[0] not in kill_mon_names])
    print(f"已写 {STD_NONE.name}({len(std_none)}行) / {MON_NONE.name}({len(mon_none)}行)，主表已瘦身")
    return 0


if __name__ == "__main__":
    sys.exit(main())
