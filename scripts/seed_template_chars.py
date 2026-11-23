#!/usr/bin/env python3
"""预置各等级模板测试角色（Python 3.10，幂等可重复执行）。

往默认库 data/jootm2.db 建 9 个专用账号（每个恰好 2 个角色——服务端
MAX_CHARS_PER_ACCOUNT=2，一个账号塞多角色会撑爆选角界面），覆盖
1/7/17/25/35/40 级 × 三职业共 18 个模板角色，登录即可体验各阶段属性/装备/技能。

用法:
    D:/Python310/python.exe scripts/seed_template_chars.py            # 写入默认库
    D:/Python310/python.exe scripts/seed_template_chars.py --check   # 只检查不写
    D:/Python310/python.exe scripts/seed_template_chars.py --db PATH # 指定库

说明：
  - 幂等：已存在的同名账号/角色跳过（不覆盖玩家后续改动）。
  - HP/MP 按 BASE+成长估算；实际属性在进入游戏时由 Player.recompute 按装备重算。
  - 装备按等级给对应档位的武器/衣服/头盔 + 药水，名字从 StdItems.csv 动态查，
    查不到的跳过（不同版本道具名可能不同）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load as load_config  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.templates import dumps  # noqa: E402
from pyserver.util import U_DRESS, U_WEAPON, U_HELMET, U_NECKLACE, U_RINGL  # noqa: E402
from pyserver.world import BASE_HP, BASE_MP, HP_GROWTH, MP_GROWTH, GameWorld  # noqa: E402

PASSWORD = "template123"
LEVELS = (1, 7, 17, 25, 35, 40)
JOBS = (("战士", 0), ("法师", 1), ("道士", 2))
MAX_CHARS = 2   # 与 service.MAX_CHARS_PER_ACCOUNT 对齐：一个账号最多 2 角色


def account_of(index: int) -> str:
    """18 个角色按展平顺序每 2 个归一个账号：tpl1 … tpl9。"""
    return f"tpl{index // MAX_CHARS + 1}"

# 各等级段的装备（名字按 StdItems.csv 实际存在与否动态过滤）
GEAR_BY_TIER = {
    "starter": ["木剑", "布衣(男)", "蜡烛"],
    "mid": ["凌风", "战神盔甲(男)", "青铜头盔", "金创药(小量)"],
    "high": ["裁决之杖", "重盔甲(男)", "铁头盔", "力量戒指", "金创药(中量)"],
}


def tier(level: int) -> str:
    if level <= 7:
        return "starter"
    if level <= 25:
        return "mid"
    return "high"


def starter_magic(job: int) -> int:
    return 3 if job == 0 else (1 if job == 1 else 2)


def extra_magics(job: int, level: int) -> list[int]:
    """按等级追加几个代表技能（只给 id，train=0）。"""
    if job == 0:
        return [m for lim, m in ((7, 4), (25, 9), (35, 13)) if level >= lim]
    if job == 1:
        return [m for lim, m in ((7, 2), (17, 5), (29, 10), (35, 18)) if level >= lim]
    return [m for lim, m in ((7, 8), (25, 11), (35, 14)) if level >= lim]


def make_rec(world, account_id: int, name: str, sex: int, job: int, level: int) -> dict:
    hp = min(65535, BASE_HP[job] + (level - 1) * HP_GROWTH)
    mp = min(65535, BASE_MP[job] + (level - 1) * MP_GROWTH)
    items = []
    names = GEAR_BY_TIER[tier(level)]
    for idx, item_name in enumerate(names):
        t = world.item_template_by_name(item_name)
        if t is None:
            continue
        outfit = item_name.startswith("布衣") or "盔甲" in item_name
        weapon = item_name in ("木剑", "凌风", "裁决之杖")
        helmet = "头盔" in item_name
        ring = "戒指" in item_name
        slot = U_DRESS if outfit else U_WEAPON if weapon else U_HELMET if helmet else U_RINGL if ring else -1
        items.append({
            "makeIndex": world.next_item_make_index(), "index": t.index,
            "durability": t.duraMax, "durabilityMax": t.duraMax,
            "lucky": 0, "slot": slot,
        })
    # 至少给几瓶药，方便直接打怪体验
    drug = world.item_template_by_name("金创药(小量)")
    if drug is not None and not any(i["index"] == drug.index for i in items):
        for _ in range(6):
            items.append({"makeIndex": world.next_item_make_index(), "index": drug.index,
                          "durability": drug.duraMax, "durabilityMax": drug.duraMax,
                          "lucky": 0, "slot": -1})
    skills = {str(starter_magic(job)): {"level": 0, "train": 0}}
    for m in extra_magics(job, level):
        skills[str(m)] = {"level": 0, "train": 0}
    return {
        "account_id": account_id, "name": name, "sex": sex, "hair": 1,
        "job": job, "level": level, "exp": 0,
        "map_name": world.default_map.name, "x": world.start_x, "y": world.start_y,
        "dir": 4, "hp": hp, "mp": mp, "gold": 5000 + level * 1000,
        "home_map": world.default_map.name, "home_x": world.start_x, "home_y": world.start_y,
        "items": items, "storage": [], "quick_slots": [0] * 6,
        "skills": dumps(skills), "created_at": int(time.time() * 1000),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不写入")
    ap.add_argument("--db", default="", help="指定 db 路径（默认 config 的 db）")
    args = ap.parse_args()

    cfg = load_config()
    db_path = Path(args.db) if args.db else cfg.db_file
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    world = GameWorld(cfg, db)

    print(f"库: {db_path}")
    print(f"账号: tpl1..tpl9 / {PASSWORD}（每账号 {MAX_CHARS} 角色，共 "
          f"{len(LEVELS) * len(JOBS)} 个模板角色）")

    # 旧方案（单账号 template 塞 18 角色）残留提示
    legacy = db.find_account("template")
    if legacy is not None:
        lc = db.list_characters(legacy.id)
        if lc:
            print(f"注意：旧账号 template 下还有 {len(lc)} 个角色（超过 "
                  f"{MAX_CHARS} 个上限的旧方案残留），建议手工清理。")

    # 展平 18 个 (level, job) 组合，两两分组到 tpl1..tpl9
    combos = [(level, job_name, job) for level in LEVELS for job_name, job in JOBS]
    acc_cache: dict[str, int] = {}
    existing_names: set[str] = set()
    made = skipped = 0
    for i, (level, job_name, job) in enumerate(combos):
        acc_name = account_of(i)
        name = f"模板{job_name}{level}"
        if acc_name not in acc_cache:
            if args.check:
                acc = db.find_account(acc_name)
                acc_cache[acc_name] = acc.id if acc else 0
                existing_names = ({c["name"] for c in db.list_characters(acc.id)}
                                  if acc else set())
            else:
                acc = db.find_account(acc_name) or db.register(acc_name, PASSWORD)
                acc_cache[acc_name] = acc.id
                existing_names = {c["name"] for c in db.list_characters(acc.id)}
        if name in existing_names:
            print(f"  跳过（已存在）: {acc_name}/{name}")
            skipped += 1
            continue
        rec = make_rec(world, acc_cache[acc_name], name, 0, job, level)
        if not args.check:
            db.insert_character(rec)
            # 赠送离线修炼时长，方便直接试离线挂机
            db.ensure_afk_row(db.find_character(name)["id"], gift_minutes=240)
            existing_names.add(name)
        print(f"  {'[check] ' if args.check else '✓ '}{acc_name}/{name}  Lv{level} {job_name}  "
              f"HP{rec['hp']} MP{rec['mp']} 装备{len(rec['items'])}件")
        made += 1

    db.conn.close()
    print(f"\n完成: 新建 {made}，跳过 {skipped}"
          + ("（--check 未写入）" if args.check else ""))
    if not args.check:
        print("生效：重启服务端后，登录 tpl1..tpl9 / template123 即可看到对应角色。")


if __name__ == "__main__":
    main()
