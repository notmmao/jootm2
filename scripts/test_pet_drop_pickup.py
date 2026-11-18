#!/usr/bin/env python3
"""道士召唤物击杀掉落 → 主人拾取的回归（纯标准库）。

原版依据（火炬蓝本 M2Server/ObjBase.pas）：
- ``Die`` :2733-2744：``ehiter := ExpHiter; if ExpHiter.Master <> nil then
  ehiter := ExpHiter.Master; ScatterBagItems(ehiter)`` —— 宝宝击杀掉落归**主人**，
  不归宝宝本身，也不归旁人。
- ``PickUp`` :6805-6846：``canpickup`` = 掉落无归属 / 归属自己；``cangrouppickup``
  = 队友；超时 ``ANTI_MUKJA_DELAY`` 清归属后谁都能捡。
- ``CM_PICKUP`` :12117：服务端要求 ``CX = msg.lParam2`` 且 ``CY = msg.lParam3``
  —— 必须站在物品格上。

用法：
    python scripts/test_pet_drop_pickup.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pyserver.world import Monster, now_ms  # noqa: E402
from pyserver.world_extra import DropEntry, FLOOR_ITEM_OWNERSHIP_MS  # noqa: E402
import test_death_drop as T  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


class Session:
    def __init__(self):
        self.messages: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def drain(self) -> None:
        self.messages.clear()


def make_player(service, name):
    s = Session()
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 2},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(s, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(s)
    assert p is not None and p.name == name, f"{name} 登录失败"
    return p, s


def main() -> int:
    w, p, sess, service = T.build()
    tag = str(int(time.time()) % 100000)
    other, other_s = make_player(service, "O" + tag)

    base = next(iter(w.monster_templates.values()))
    mt = replace(base, name="宝宝掉落测试怪", race=80, appr=20, level=2, hp=50,
                 ac=0, mac=0, dc=1, maxDc=2, speed=0, hit=0,
                 walkSpeed=800, attackSpeed=1000)
    item_t = T.add_template(w, stdMode=0, shape=0, price=100)
    w.drop_tables[mt.name] = [DropEntry(0, 1, item_t.name, 1)]

    pet_t = replace(base, name="神兽", race=113, appr=170, level=7, hp=200,
                    ac=0, mac=0, dc=10, maxDc=15, speed=0, hit=0,
                    walkSpeed=800, attackSpeed=1000)
    pet = Monster(id=w.new_id(), name="神兽", x=p.x, y=p.y, dir=4,
                  hp=200, max_hp=200, level=7, race=113, template=pet_t,
                  master_id=p.id, slave_make_level=3,
                  spawn_x=p.x, spawn_y=p.y)
    w.add_actor(pet, p.map)
    target = Monster(id=w.new_id(), name=mt.name, x=p.x + 1, y=p.y, dir=4,
                     hp=1, max_hp=50, level=2, race=80, template=mt,
                     spawn_x=p.x + 1, spawn_y=p.y, exp=10)
    w.add_actor(target, p.map)

    print("\n[1] 宝宝补刀：掉落归属应是主人（ObjBase.pas:2733-2744）")
    p.target_id = target.id
    w._summon_ai(pet, now_ms())
    gis = [gi for gi in w.ground_items.values()
           if gi.map is p.map and gi.item is not None]
    check("宝宝击杀已结算", not target.alive)
    check("地面出现掉落", len(gis) >= 1, f"n={len(gis)}")
    gi = gis[0]
    check("owner_id = 主人玩家 id（不是宝宝、不是 -1）",
          gi.owner_id == p.id and gi.owner_id != pet.id,
          f"owner={gi.owner_id}, master={p.id}, pet={pet.id}")

    print("\n[2] 主人拾取（handler 全链路）")
    # 先站到别的格：服务端要求同格，非同格静默拒绝
    p.x, p.y = gi.x + 1, gi.y
    other_s.drain()
    service._pickup(sess, {"type": "pickup", "itemId": gi.id})
    check("非同格拒绝，物品仍在", gi.id in w.ground_items)
    p.x, p.y = gi.x, gi.y
    before = len(p.bag)
    sess.drain()
    service._pickup(sess, {"type": "pickup", "itemId": gi.id})
    check("主人站上物品格 → 捡到", gi.id not in w.ground_items
          and len(p.bag) == before + 1, f"bag {before}->{len(p.bag)}")
    check("下发 bagUpdate + systemMessage",
          any(m.get("type") == "bagUpdate" for m in sess.messages))

    print("\n[3] 旁人 2 分钟内不能捡；超时后谁都能捡")
    gi2 = [g for g in w.ground_items.values() if g.map is p.map and g.item][0] \
        if any(g.map is p.map and g.item for g in w.ground_items.values()) else None
    if gi2 is None:
        # 重新掷一次掉落
        w.drop_loot(target, p)
        gi2 = [g for g in w.ground_items.values() if g.map is p.map and g.item][0]
    gi2.owner_id = p.id
    gi2.created_at = now_ms()
    other.x, other.y = gi2.x, gi2.y
    other_s.drain()
    service._pickup(other_s, {"type": "pickup", "itemId": gi2.id})
    check("旁人被拒 + 原版提示", gi2.id in w.ground_items
          and any(m.get("type") == "systemMessage" and "无法捡起" in m.get("text", "")
                  for m in other_s.messages))
    gi2.created_at = now_ms() - FLOOR_ITEM_OWNERSHIP_MS - 1
    other_s.drain()
    service._pickup(other_s, {"type": "pickup", "itemId": gi2.id})
    check("超时归属清空 → 旁人可捡", gi2.id not in w.ground_items)

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
