#!/usr/bin/env python3
"""死亡惩罚掉落 + 丢金币的离线回归（纯标准库）。

对齐原版依据（2026-11-23 复核，旧注释的 M2Share 配置变量名/行号系臆造，已按真实实现更正）：
- ``TCreature.Die`` 掉落段（ObjBase.pas:2728-2760）：
  背包 ``ScatterBagItems``（:2385）每件 ``Random(3)=0`` 掉落，红名
  ``PKLevel>=2``（:2398，pk_point>=200）boDropall 全掉；
  装备 ``DropUseItems``（:2512）被怪杀才掉（调用闸门 :2749-2753，被玩家杀
  不调用），每槽 ``Random(ran)=0``，``ran = 15 if PKLevel>2 else 30``
  （:2547-2548）——装备加速掉阈值 pk_point>=300 **高于**背包全掉的 200；
  金币玩家死亡分支无 ScatterGolds（仅怪 :2743）不掉。散落半径 dropwide=2（:2397）。
- ``TPlayObject.ClientDropGold``（ObjBase.pas:16187）：``nGold >= m_nGold``
  拒绝（至少留 1）；``DropGoldDown``（:2301）半径 3 落地、同格合并。

用法：
    python scripts/test_death_drop.py
"""
from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import DEEP_RED_PK_POINT, RED_NAME_PK_POINT  # noqa: E402
from pyserver.world import Monster, Player, now_ms  # noqa: E402

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


class Session:
    def __init__(self):
        self.messages: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def types(self) -> list[str]:
        return [m.get("type", "?") for m in self.messages]

    def drain(self) -> None:
        self.messages.clear()

    def close(self) -> None:
        pass


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-death-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    tag = str(int(time.time()) % 100000)
    name = "X" + tag
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    w = service.world
    gm = p.map or w.default_map
    p.x = min(max(20, p.x), gm.width - 20)
    p.y = min(max(20, p.y), gm.height - 20)
    return w, p, sess, service


def add_template(w, **kw):
    base = next(iter(w.item_templates.values()))
    idx = 910000 + len(w.item_templates)
    t = replace(base, index=idx, **kw)
    w.item_templates[idx] = t
    return t


def bag_item(t, mi: int) -> dict:
    return {"makeIndex": mi, "index": t.index, "durability": 1000,
            "durabilityMax": 1000, "lucky": 0}


def mon_template(w, **kw):
    base = next(iter(w.monster_templates.values()))
    return replace(base, name="死亡测试怪", race=80, appr=20, level=3, hp=1000,
                   ac=0, mac=0, dc=1, maxDc=2, speed=0, hit=0,
                   walkSpeed=800, attackSpeed=1000, **kw)


def ground_gold(w, p):
    return [gi for gi in w.ground_items.values() if gi.map is p.map and gi.is_gold()]


def test_bag_scatter(w, p, sess):
    print("\n[1] 背包散落：Random(3)=0 掉 1/3，红名全掉")
    t = add_template(w, stdMode=0, shape=0, price=10)
    p.bag = [bag_item(t, 700001 + i) for i in range(30)]
    p.pk_point = 0
    p.gold = 1000
    sess.drain()
    w.kill_player(p, None)          # 毒死/未知 → 按被怪杀语义
    n_ground = sum(1 for gi in w.ground_items.values() if gi.map is p.map
                   and gi.item and 700001 <= gi.item.get("makeIndex", 0) <= 700030)
    check("死亡置 dead/alive 标志", p.dead and not p.alive)
    check("背包 30 件按 1/3 散落（10±波动，有掉落）", n_ground >= 5,
          f"ground={n_ground}, bag_left={len(p.bag)}")
    check("掉落后背包 + 地面 = 原件数", len(p.bag) + n_ground == 30,
          f"{len(p.bag)}+{n_ground}")
    check("金币默认不掉（boDieDropGold=False）", len(ground_gold(w, p)) == 0,
          f"gold_items={len(ground_gold(w, p))}")
    check("掉落下发 bagUpdate", any(m.get("type") == "bagUpdate"
                                    for m in sess.messages),
          f"types={sess.types()}")


def test_red_scatter_all(w, p, sess, service):
    print("\n[2] 红名 PKLevel>=2：背包全掉")
    # 重登复活
    for msg in ({"type": "logout"}, {"type": "enterGame", "name": p.name},
                {"type": "noticeOk", "name": p.name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p2 = service.find_by_session(sess)
    assert p2 is not None
    t = add_template(w, stdMode=0, shape=0, price=10)
    p2.bag = [bag_item(t, 700101 + i) for i in range(10)]
    p2.pk_point = RED_NAME_PK_POINT
    p2.x, p2.y = p.x, p.y
    p2.gold = 500
    sess.drain()
    w.kill_player(p2, None)
    n_ground = sum(1 for gi in w.ground_items.values() if gi.map is p2.map
                   and gi.item and 700101 <= gi.item.get("makeIndex", 0) <= 700110)
    check("红名背包 10 件全掉", len(p2.bag) == 0 and n_ground == 10,
          f"bag={len(p2.bag)}, ground={n_ground}")
    return p2


def test_equip_drop_by_monster(w, p2, sess, service):
    print("\n[3] 身上装备：被怪杀 Random(30)=0 逐槽掉，被玩家杀不掉")
    for msg in ({"type": "logout"}, {"type": "enterGame", "name": p2.name},
                {"type": "noticeOk", "name": p2.name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p3 = service.find_by_session(sess)
    assert p3 is not None
    p3.x, p3.y = p2.x, p2.y
    wpn_t = add_template(w, stdMode=5, shape=1, duraMax=3000, dcMin=2, dcMax=5)
    wpn = {"makeIndex": 700201, "index": wpn_t.index, "durability": 3000,
           "durabilityMax": 3000, "lucky": 0}
    p3.equipment[1] = wpn
    p3.bag = []
    p3.pk_point = 0
    # 被玩家杀：boKillByHumanDropUseItem=False → 装备不掉
    sess.drain()
    w.kill_player(p3, "player")     # 任意非 Player 对象即可触发被怪杀分支，
    # 上面先测被玩家杀语义：直接调 _death_drops 模拟
    # —— 为可控断言，直接测 _death_drops 两分支
    p3.dead = False
    p3.alive = True
    p3.equipment[1] = wpn
    sess.drain()
    from pyserver.world import Player as P
    fake = P(id=-99, name="killer", job=0)
    w._death_drops(p3, fake)
    check("被玩家杀装备不掉（boKillByHumanDropUseItem=False）",
          p3.equipment[1] is wpn, f"equip1={'还在' if p3.equipment[1] else '没了'}")
    # 被怪杀：冻 Random(30)=0 → 必掉
    real_randrange = random.randrange
    random.randrange = lambda n: 0  # noqa: E731
    try:
        w._death_drops(p3, None)
    finally:
        random.randrange = real_randrange
    check("被怪杀 Random(30)=0 → 装备掉落", p3.equipment[1] is None)
    n_ground = sum(1 for gi in w.ground_items.values()
                   if gi.map is p3.map and not gi.is_gold()
                   and gi.item and gi.item.get("makeIndex") == 700201)
    check("掉落物在地面", n_ground == 1, f"found={n_ground}")


def _equip_randrange_divisor(w, p3, pk_point):
    """空背包 + 单件装备，记录被怪杀掉落时对装备槽用的 randrange 除数。"""
    from pyserver.world import Player as P
    p3.bag = []
    p3.pk_point = pk_point
    p3.dead = False
    p3.alive = True
    wpn_t = add_template(w, stdMode=5, shape=1, duraMax=3000, dcMin=2, dcMax=5)
    p3.equipment[1] = {"makeIndex": 700300 + pk_point, "index": wpn_t.index,
                       "durability": 3000, "durabilityMax": 3000, "lucky": 0}
    seen: list[int] = []
    real = random.randrange
    random.randrange = lambda n: (seen.append(n), 1)[1]  # 返回非 0 → 不掉，只记录除数
    try:
        w._death_drops(p3, None)
    finally:
        random.randrange = real
    return seen


def test_equip_drop_red_tiers(w, p3, sess, service):
    print("\n[3b] 装备掉落除数按红名分档：200~299→30、>=300→15")
    for msg in ({"type": "logout"}, {"type": "enterGame", "name": p3.name},
                {"type": "noticeOk", "name": p3.name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    pp = service.find_by_session(sess)
    assert pp is not None
    pp.x, pp.y = p3.x, p3.y
    # 白名：除数 30
    d_white = _equip_randrange_divisor(w, pp, 0)
    check("白名装备 randrange(30)", 30 in d_white, f"divisors={d_white}")
    # 红名 200~299：仍是 30（旧实现误用 15，掉率翻倍）
    d_red = _equip_randrange_divisor(w, pp, RED_NAME_PK_POINT)
    check("红名 200 装备 randrange(30)（非 15）", 30 in d_red and 15 not in d_red,
          f"divisors={d_red}")
    # 深红名 >=300：降到 15
    d_deep = _equip_randrange_divisor(w, pp, DEEP_RED_PK_POINT)
    check("深红名 300 装备 randrange(15)", 15 in d_deep, f"divisors={d_deep}")


def test_drop_gold(w, p, sess, service):
    print("\n[4] 丢金币：ClientDropGold 语义")
    for msg in ({"type": "logout"}, {"type": "enterGame", "name": p.name},
                {"type": "noticeOk", "name": p.name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p4 = service.find_by_session(sess)
    assert p4 is not None
    gm = p.map or w.default_map
    p4.x = min(max(20, p4.x), gm.width - 20)
    p4.y = min(max(20, p4.y), gm.height - 20)
    p4.gold = 100
    sess.drain()
    check("丢 0 金币拒绝", not w.drop_gold(p4, 0))
    check("丢 >= 全部拒绝（原版 nGold >= m_nGold Exit）",
          not w.drop_gold(p4, 100) and not w.drop_gold(p4, 101))
    check("超过持有拒绝后金币不变", p4.gold == 100)
    check("丢 30 成功", w.drop_gold(p4, 30))
    check("金币扣减 100→70", p4.gold == 70, f"gold={p4.gold}")
    g = ground_gold(w, p4)
    check("地面出现 30 金币堆", len(g) == 1 and g[0].gold == 30,
          f"piles={[(gi.gold) for gi in g]}")
    sess.drain()
    # 再丢 20：GetDropPosition 专挑空格 → 通常另起一堆；同格才合并（<=2000）
    check("再丢 20 成功", w.drop_gold(p4, 20))
    g = ground_gold(w, p4)
    total = sum(gi.gold for gi in g)
    check("地面金币总量 50（1~2 堆）", total == 50 and 1 <= len(g) <= 2,
          f"piles={[(gi.gold) for gi in g]}")
    check("下发 stats（GoldChanged）", any(m.get("type") == "stats"
                                          for m in sess.messages),
          f"types={sess.types()}")
    # handler 链路
    sess.drain()
    service._drop_gold(sess, {"type": "dropGold", "gold": 10})
    g = ground_gold(w, p4)
    check("dropGold handler 全链路（50→40，地面总 60）",
          p4.gold == 40 and sum(gi.gold for gi in g) == 60,
          f"gold={p4.gold}, ground={[(gi.gold) for gi in g]}")


def make_player(service, sess, name):
    """在**独立 session** 上注册登录（复用同一 session 会被服务端拒绝重登）。"""
    s = Session()
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(s, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(s)
    assert p is not None and p.name == name, f"{name} 登录失败"
    return p, s


def test_pickup_rules(w, p, sess, service):
    print("\n[5] 拾取：同格才能捡 + 2 分钟归属（怪物掉落归击杀者，同队可捡）")
    from pyserver.world_extra import FLOOR_ITEM_OWNERSHIP_MS, GroundItem
    b, b_sess = make_player(service, sess, "B" + str(int(time.time()) % 100000))
    t = add_template(w, stdMode=0, shape=0, price=10)
    gm = p.map or w.default_map

    # ① 非同格拒绝（原版 GetItem(m_nCurrX, m_nCurrY)）：无归属物品，
    #    站相邻格 → 静默失败；站上物品格 → 捡到
    gi = GroundItem(w.new_id(), bag_item(t, 700301), 0, b.x + 1, b.y,
                    now_ms(), gm, -1)
    w.add_ground_item(gi)
    b_sess.drain()
    service._pickup(b_sess, {"type": "pickup", "itemId": gi.id})
    check("非同格静默拒绝", gi.id in w.ground_items
          and not any(m.get("type") == "bagUpdate" for m in b_sess.messages),
          f"types={b_sess.types()}")
    b.x, b.y = gi.x, gi.y        # 站到物品格上
    b_sess.drain()
    service._pickup(b_sess, {"type": "pickup", "itemId": gi.id})
    check("站上物品格 → 捡到", gi.id not in w.ground_items,
          f"types={b_sess.types()}")

    # ② 别人 2 分钟内不能捡（g_sCanotPickUpItem）
    gi2 = GroundItem(w.new_id(), bag_item(t, 700302), 0, b.x, b.y,
                     now_ms(), gm, p.id)
    w.add_ground_item(gi2)
    b_sess.drain()
    service._pickup(b_sess, {"type": "pickup", "itemId": gi2.id})
    check("他人捡归属内物品被拒 + 提示",
          any(m.get("type") == "systemMessage"
              and "在一定时间以内无法捡起此物品" in m.get("text", "")
              for m in b_sess.messages) and gi2.id in w.ground_items,
          f"types={b_sess.types()}")

    # ③ 同队成员可捡（原版 IsOfGroup）
    w.create_party(p.id, b.id)
    service._pickup(b_sess, {"type": "pickup", "itemId": gi2.id})
    check("同队成员可捡", gi2.id not in w.ground_items,
          f"types={b_sess.types()}")
    w.leave_party(p.id)
    w.leave_party(b.id)

    # ④ 超过 2 分钟归属失效，谁都能捡
    gi3 = GroundItem(w.new_id(), bag_item(t, 700303), 0, b.x, b.y,
                     now_ms() - FLOOR_ITEM_OWNERSHIP_MS - 1, gm, p.id)
    w.add_ground_item(gi3)
    service._pickup(b_sess, {"type": "pickup", "itemId": gi3.id})
    check("超时后他人可捡（OfBaseObject := nil）",
          gi3.id not in w.ground_items)

    # ⑤ 玩家死亡散落无归属：别人站上去直接可捡
    p.pk_point = 0
    p.bag = [bag_item(t, 700304)]
    p.dead, p.alive = False, True
    sess.drain()
    real_randrange = random.randrange
    random.randrange = lambda n: 0  # noqa: E731   # 必掉
    try:
        w.kill_player(p, None)
    finally:
        random.randrange = real_randrange
    death_items = [gi for gi in w.ground_items.values()
                   if gi.map is gm and gi.item
                   and gi.item.get("makeIndex") == 700304]
    check("死亡散落物 owner=-1（原版 ScatterBagItems(nil)）",
          len(death_items) == 1 and death_items[0].owner_id == -1,
          f"owner={death_items[0].owner_id if death_items else '?'}")
    if death_items:
        b.x, b.y = death_items[0].x, death_items[0].y
        service._pickup(b_sess, {"type": "pickup", "itemId": death_items[0].id})
        check("他人可立即捡死亡散落物",
              death_items[0].id not in w.ground_items)


def main():
    w, p, sess, service = build()
    test_bag_scatter(w, p, sess)
    p2 = test_red_scatter_all(w, p, sess, service)
    test_equip_drop_by_monster(w, p2, sess, service)
    test_equip_drop_red_tiers(w, p2, sess, service)
    test_drop_gold(w, p, sess, service)
    # 重登链路会创建新 Player 对象（旧 id 已从 map.players 移除），
    # 归属测试必须用当前 session 绑定的最新对象。
    p = service.find_by_session(sess)
    assert p is not None
    test_pickup_rules(w, p, sess, service)
    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
