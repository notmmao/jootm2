#!/usr/bin/env python3
"""护身符耗尽行为回归（离线）。

对齐原版依据：
- CheckAmulet（Magic.pas:81-131）：只查已装备的 U_ARMRINGL / U_BUJUK 两槽
  （StdMode=25，护身符 Shape=5 / 毒药 Shape<=2），Dura/100 >= 张数才可用。
  **不会从背包自动补戴**。
- UseAmulet（Magic.pas:134-144）：``Dura > nCount*100`` 才扣减并发
  RM_DURACHANGE；否则 Dura=0 + SendDelItems 整件消失 + 槽清空。
  边界：剩余耐久恰好等于本次消耗（如剩 100 用 1 张）→ `100 > 100` 为
  False → 整件删除（不会留 0 耐久的空符挂着）。
- 耗尽后背包里还有符 → 下次施法 CheckAmulet 找不到 → 施法失败
  （boSpellFail=True），需要玩家手动把符戴回装备槽。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_amulet_depletion.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataclasses import replace  # noqa: E402

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import U_BUJUK  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


class FakeSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = False

    def drain(self) -> None:
        self.messages.clear()


def make_service():
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "amulet-depletion-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def login(srv, name):
    sess = FakeSession()
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 2},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(sess)
    assert p is not None, "登录链路失败"
    return p, sess


def send(srv, sess, type_: str, **fields) -> None:
    msg = {"type": type_}
    msg.update(fields)
    srv.on_message(sess, json.dumps(msg, ensure_ascii=False))


def last_system(sess) -> str:
    for m in reversed(sess.messages):
        if m.get("type") == "systemMessage":
            return m.get("text", "")
    return ""


def amulet_item(w, tmpl_index: int, dura: int) -> dict:
    it = w.new_item_instance(w.item_templates[tmpl_index])
    it["durability"] = dura
    return it


def main() -> None:
    srv = make_service()
    w = srv.world

    p, sess = login(srv, "AMU1")
    # 造护身符模板（StdMode=25 Shape=5，1 张 = 100 耐久）
    base = next(iter(w.item_templates.values()))
    idx = 910000
    w.item_templates[idx] = replace(base, index=idx, stdMode=25, shape=5,
                                    duraMax=2000, name="测试护身符")
    # 灵魂火符（13，CheckAmulet(1,1)：每次 1 张）
    p.skills[13] = {"level": 0, "train": 0, "key": 0}
    p.mp = max(p.mp, 200)

    # 装备槽 U_BUJUK 放剩 200 耐久（2 张）的符；背包再放一捆 500 耐久的
    p.equipment[U_BUJUK] = amulet_item(w, idx, 200)
    bag_amulet = amulet_item(w, idx, 500)
    p.bag.append(bag_amulet)

    def cast():
        p.skill_cooldown.clear()
        p.mp = max(p.mp, 200)
        sess.drain()
        send(srv, sess, "castMagic", magicId=13,
             targetX=p.x + 2, targetY=p.y, targetId=0)

    print("\n[1] 施法消耗：200 → 100（1 张），装备仍在")
    cast()
    eq = p.equipment[U_BUJUK]
    check("装备槽符耐久 200→100", eq is not None and int(eq["durability"]) == 100,
          f"durability={eq['durability'] if eq else None}")
    check("背包符未被动过", bag_amulet in p.bag
          and int(bag_amulet["durability"]) == 500)

    print("\n[2] 剩 100 再用 1 张：`100 > 100` 为 False → 整件消失（Magic.pas:136-143）")
    cast()
    check("装备槽符已删除", p.equipment[U_BUJUK] is None)
    check("背包符仍在（未自动补戴）", bag_amulet in p.bag)
    check("背包符耐久不变", int(bag_amulet["durability"]) == 500)

    print("\n[3] 耗尽后施法失败：只查装备槽，不翻背包（CheckAmulet Magic.pas:81-131）")
    cast()
    check("提示「没有护身符。」", "没有护身符" in last_system(sess),
          f"提示={last_system(sess)!r}")
    check("背包符依然原封不动", bag_amulet in p.bag
          and int(bag_amulet["durability"]) == 500)

    print("\n[4] 手动 takeOnItem 重戴背包符后恢复施法")
    sess.drain()
    send(srv, sess, "takeOnItem", makeIndex=int(bag_amulet["makeIndex"]),
         slot=U_BUJUK)
    check("背包符戴回装备槽", p.equipment[U_BUJUK] is bag_amulet)
    cast()
    eq = p.equipment[U_BUJUK]
    check("重戴后施法正常消耗 500→400",
          eq is not None and int(eq["durability"]) == 400,
          f"durability={eq['durability'] if eq else None}")

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
