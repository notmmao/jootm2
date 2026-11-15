#!/usr/bin/env python3
"""背包容量与购买拦截回归（离线）。

对齐原版依据：
- MAXBAGITEM = 46（Common/Grobal2.pas:70，注释「包含快捷栏上的6个物品」）。
- 商店购买 TMerchant.ClientBuyItem（ObjNpc.pas:1939-1969）：
  m_ItemList.Count >= MAXBAGITEM → 拒绝（ObjNpc.pas:4219）。
- AddItemBag（ClFunc.pas:173）普通物品从格 6 开始放：格 0..5 = 快捷栏。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_bag_capacity.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402

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
    db_path = Path(tempfile.mkdtemp()) / "bag-capacity-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def login(srv, name):
    sess = FakeSession()
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
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


def find_shop_npc(srv, p):
    """找当前地图上有货单的 NPC。"""
    w = srv.world
    for npc in w.npcs.values():
        if npc.map == p.map and w.goods(npc):
            return npc
    return None


def main() -> None:
    srv = make_service()
    w = srv.world

    print("\n[1] 服务端 BAG_SIZE = 46（原版 MAXBAGITEM，Common/Grobal2.pas:70）")
    from pyserver.util import BAG_SIZE
    check("BAG_SIZE == 46", BAG_SIZE == 46)

    print("\n[2] 背包满时购买被拒绝且不扣金币（ObjNpc.pas:4219）")
    p, sess = login(srv, "BAG1")
    npc = find_shop_npc(srv, p)
    check("当前地图能找到有货单的商店 NPC", npc is not None)
    if npc is None:
        print("  （跳过后续购买用例）")
        print(f"\n结果: {PASS} passed, {FAIL} failed")
        return
    goods = w.goods(npc)
    g = goods[0]
    # 直接把背包塞满（绕过 give_item 的检查，模拟任意途径堆满）
    t = w.item_templates.get(g["index"])
    from pyserver.world import BAG_SIZE as WBAG
    p.bag.clear()
    for _ in range(WBAG):
        p.bag.append(w.new_item_instance(t))
    p.gold = 100000
    sess.drain()
    send(srv, sess, "buyItem", npcId=npc.id, index=g["index"], count=1)
    check("满包购买被拒（提示背包已满）", "背包已满" in last_system(sess),
          f"提示={last_system(sess)!r}")
    check("满包购买不扣金币", p.gold == 100000)
    check("满包购买 bag 不增长", len(p.bag) == WBAG)

    print("\n[3] 背包差 1 格时可以买 1 个、买 2 个被拒")
    p.bag.pop()
    p.gold = 100000
    sess.drain()
    send(srv, sess, "buyItem", npcId=npc.id, index=g["index"], count=1)
    check("剩 1 格能买 1 个", any(int(i.get("index", 0)) == g["index"]
                                  for i in p.bag))
    sess.drain()
    send(srv, sess, "buyItem", npcId=npc.id, index=g["index"], count=2)
    check("满后再买 2 个被拒", "背包已满" in last_system(sess))

    print("\n[4] 前端网格 40 格 = 原版背包窗口物品区（不含快捷栏 6 格）")
    # 原版 DItemBag 背景图含顶部快捷栏 6 格共显示 46（FState.pas:4621 注释）
    src = Path(ROOT, "frontend/scene/PlayScene.mjs")
    text = src.read_text(encoding="utf-8")
    check("前端背包窗口截取前 40 格显示", "slice(0, 40)" in text)

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
