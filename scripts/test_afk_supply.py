#!/usr/bin/env python3
"""离线修炼补给闭环专项回归（docs/OFFLINE-AFK-PLAN.md §3.4 v2）。

覆盖：
    门邻接表求解（合成图 + 真实 MapInfo 门数据）
    → 满包触发补给状态机
    → 卖肉金币到账
    → 买药到账（数量按剩余修炼时长粗估）
    → 回到锚点
    → 真实过门（0 ↔ 0102 门点往返）
    → 金币不足连续 2 次补给失败熔断
    → 无可达补给点熔断
    → afk.supplyEnabled=0 回退 v1 行为（在线托管语义不变）

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_afk_supply.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.afk import (SUPPLY_BUY, SUPPLY_GOTO_ANCHOR, SUPPLY_GOTO_TOWN,  # noqa: E402
                          SUPPLY_RETRY_MS, SUPPLY_SELL, OfflinePilot,
                          NullSession, count_potions, supply_gate_route)
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import BAG_SIZE, Monster  # noqa: E402
from pyserver.world_extra import GroundItem  # noqa: E402


class FakeSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.account_id = 0
        self.account_name = ""
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True


def make_service():
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "afk-supply-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return cfg, db, db_path, GameService(cfg, db)


def login(srv, session, name, job=0):
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(session)
    assert p is not None, "登录链路失败"
    return p


def clear_threats(srv, p):
    """清掉当前图的怪与地面物品，保证补给路线不被战斗/占格打断。"""
    w = srv.world
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            w.actors.pop(a.id, None)
    for gid in [g for g, gi in w.ground_items.items() if gi.map is p.map]:
        del w.ground_items[gid]


def place(srv, p, x, y, mapname=None):
    """把角色放到 mapname 图的 (x,y)（含邻居搜索，找到可站格为止）。"""
    w = srv.world
    gm = w.get_or_load_map(mapname or (p.map.name if p.map else "0"))
    if gm is None:
        return False
    for dx in (0, 1, -1, 2, -2):
        for dy in (0, 1, -1, 2, -2):
            nx, ny = x + dx, y + dy
            if gm.inside(nx, ny) and gm.can_walk(nx, ny) \
                    and not gm.occupied(nx, ny):
                return w.change_map(p, gm.name, nx, ny) is not None
    return False


def make_offline(srv, p, minutes=60):
    """绕过 promote 的地图门禁（测试锚点在比奇城内），直接构造离线代理。"""
    p.session = NullSession(1, "test")
    p.session.player_name = p.name
    p.afk_offline = True
    pilot = OfflinePilot(srv.afk, p.id, preset="balanced", started_at=1000,
                         exp_start=p.exp, level_start=p.level,
                         max_exp_start=p.max_exp, gold_start=p.gold,
                         last_exp=p.exp, last_bag=len(p.bag),
                         last_gold=p.gold, last_progress_at=1000)
    pilot.remaining = minutes
    srv.afk._pilots[p.id] = pilot
    return pilot


def drive(pilot, now: int, ticks: int, step: int = 100,
          stop_when=None, player=None) -> int:
    """虚拟时钟驱动 pilot.tick；stop_when() 返回 True 提前收工。

    ``player`` 传入时每 tick 清掉换图短锁（map_change_at 是真实时钟，
    测试虚拟时钟跑得远快于真实 350ms，不清会永久卡在门点）。
    """
    for _ in range(ticks):
        now += step
        pilot.tick(now)
        if player is not None:
            player.map_change_at = 0
        if not pilot.active:
            break
        if stop_when is not None and stop_when():
            break
    return now


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    cfg, db, db_path, srv = make_service()
    w = srv.world
    try:
        print("\n=== 1. 门邻接表求解（合成图）===")

        class G:
            def __init__(self, src, sx, sy, dst):
                self.src_map, self.src_x, self.src_y, self.dst_map = src, sx, sy, dst

        gates = {
            "A": [G("A", 1, 1, "B")],
            "B": [G("B", 2, 2, "C"), G("B", 5, 5, "D")],
            "C": [G("C", 3, 3, "A")],
        }
        r = supply_gate_route(gates, "A", "C")
        check("两段门按序求解", r == [("A", 1, 1, "B"), ("B", 2, 2, "C")], str(r))
        r = supply_gate_route(gates, "A", "D")
        check("多出口选到正确分支", r == [("A", 1, 1, "B"), ("B", 5, 5, "D")], str(r))
        check("同图返回空路线", supply_gate_route(gates, "A", "A") == [])
        check("无出口图返回 None", supply_gate_route(gates, "D", "A") is None)

        print("\n=== 2. 真实 MapInfo 门链路 ===")
        r = supply_gate_route(w.gates_by_src, "3", "0")
        check("矿区→比奇 两段门可达",
              r is not None and len(r) == 2 and r[0][0] == "3" and r[-1][3] == "0",
              str(r))
        check("比奇↔肉店(0102) 双向门",
              supply_gate_route(w.gates_by_src, "0", "0102") is not None
              and supply_gate_route(w.gates_by_src, "0102", "0") is not None)

        print("\n=== 3. 买药量估算与货源 ===")
        s = FakeSession()
        p = login(srv, s, "SupplyProbe", job=1)     # 法师
        p.level = 15
        pilot_probe = OfflinePilot(srv.afk, p.id, preset="balanced")
        pilot_probe.remaining = 60
        check("60 分钟 → 12 瓶", pilot_probe._supply_potion_target() == 12)
        pilot_probe.remaining = 0
        check("零时长下限 4 瓶", pilot_probe._supply_potion_target() == 4,
              str(pilot_probe._supply_potion_target()))
        pilot_probe.remaining = 600
        check("长时长上限 24 瓶", pilot_probe._supply_potion_target() == 24)
        pilot_probe.remaining = 60
        clear_threats(srv, p)
        place(srv, p, 320, 280)
        hp_offers = pilot_probe._supply_potion_offers(srv, w, p, want_mp=False)
        mp_offers = pilot_probe._supply_potion_offers(srv, w, p, want_mp=True)
        check("比奇城有血药货源", any(o[2].name.startswith("金创药") for o in hp_offers),
              str([o[2].name for o in hp_offers][:3]))
        check("比奇城有蓝药货源（法师用）",
              any(o[2].name.startswith("魔法药") for o in mp_offers),
              str([o[2].name for o in mp_offers][:3]))

        print("\n=== 4. 满包触发补给：卖肉到账 → 买药到账 → 回锚点 ===")
        s2 = FakeSession()
        p2 = login(srv, s2, "SupplyA", job=0)
        p2.level = 15
        p2.hp = p2.max_hp = max(200, p2.max_hp)
        p2.gold = 5000
        clear_threats(srv, p2)
        check("放到屠夫(313,271)旁", place(srv, p2, 314, 272), f"pos=({p2.x},{p2.y})")
        meat_t = w.item_template_by_name("肉")
        check("存在肉模板（StdMode 40）", meat_t is not None and meat_t.stdMode == 40)
        fill = BAG_SIZE - 2 - len(p2.bag)
        assert w.give_item(p2, "肉", fill)
        check("背包已填到触发线", len(p2.bag) >= BAG_SIZE - 4,
              f"bag={len(p2.bag)}")
        pilot = make_offline(srv, p2, minutes=60)
        supply_started = [False]

        def supply_round_done():
            if pilot.supply_state:
                supply_started[0] = True
            return supply_started[0] and pilot.supply_state == ""

        now = drive(pilot, 20_000, 2000, stop_when=supply_round_done)
        seen_supply = bool(pilot.anchor_map)
        check("满包触发补给状态机", seen_supply,
              f"anchor={pilot.anchor_map} state={pilot.supply_state}")
        check("补给完成回到待机", pilot.active and pilot.supply_state == "",
              f"active={pilot.active} state={pilot.supply_state} reason={pilot.reason}")
        hp_have, _ = count_potions(w, p2)
        check("买药到账（≥4 瓶血药）", hp_have >= 4, f"hp_potions={hp_have}")
        check("肉已卖空",
              not any(t is not None and t.stdMode == 40
                      for it in p2.bag
                      for t in [w.item_templates.get(int(it.get("index", 0)))]),
              f"bag={len(p2.bag)}")
        check("卖肉收入超过买药支出（金币净增）", p2.gold > 5000,
              f"gold {5000} → {p2.gold}")
        check("回锚点（同图且 1 格内）",
              p2.map.name == pilot.anchor_map
              and max(abs(p2.x - pilot.anchor_x), abs(p2.y - pilot.anchor_y)) <= 1,
              f"pos=({p2.x},{p2.y}) anchor=({pilot.anchor_x},{pilot.anchor_y})")
        check("无补给失败计数", pilot.supply_fails == 0)

        print("\n=== 5. 真实过门：锚点跨到肉店房间(0102)再回比奇 ===")
        # 用同一代理直接驱动 GOTO_ANCHOR：锚点设为 0102 房间内
        pilot.supply_state = SUPPLY_GOTO_ANCHOR
        pilot.supply_since = now
        pilot.anchor_map = "0102"
        pilot.anchor_x, pilot.anchor_y = 8, 6
        now = drive(pilot, now, 300, player=p2,
                    stop_when=lambda: p2.map.name == "0102"
                    and pilot.supply_state == "")
        check("踩门 0→0102 换图成功", p2.map.name == "0102",
              f"map={p2.map.name} pos=({p2.x},{p2.y})")
        check("补给完成并停在房间锚点", pilot.supply_state == "" and pilot.active)
        # 再驱动 GOTO_TOWN：从 0102 经回程门回比奇
        pilot.supply_state = SUPPLY_GOTO_TOWN
        pilot.supply_since = now
        now = drive(pilot, now, 300, player=p2,
                    stop_when=lambda: p2.map.name == "0"
                    and pilot.supply_state == SUPPLY_SELL)
        check("踩回程门 0102→0 回到比奇", p2.map.name == "0",
              f"map={p2.map.name} pos=({p2.x},{p2.y})")
        check("回到比奇后进入卖货状态", pilot.supply_state == SUPPLY_SELL,
              f"state={pilot.supply_state}")
        pilot.supply_state = ""                     # 手动收工，别让它继续卖

        print("\n=== 6. 金币不足：连续 2 次补给失败熔断 ===")
        s3 = FakeSession()
        p3 = login(srv, s3, "SupplyB", job=0)
        p3.level = 15
        p3.hp = p3.max_hp = max(200, p3.max_hp)
        p3.gold = 0
        p3.bag.clear()                              # 无货可卖、无药可喝
        clear_threats(srv, p3)
        check("p3 放到屠夫旁", place(srv, p3, 314, 272), f"pos=({p3.x},{p3.y})")
        pilot3 = make_offline(srv, p3, minutes=60)
        now = drive(pilot3, 20_000, 3000)
        check("两次失败后熔断停机", not pilot3.active,
              f"active={pilot3.active} fails={pilot3.supply_fails}")
        check("报告写明金币不足", "金币不足" in pilot3.reason, pilot3.reason)

        print("\n=== 7. 无可达补给点：图链路不通直接熔断 ===")
        s4 = FakeSession()
        p4 = login(srv, s4, "SupplyC", job=0)
        p4.level = 15
        p4.hp = p4.max_hp = max(200, p4.max_hp)
        p4.gold = 1000
        clear_threats(srv, p4)
        check("p4 放到矿区门点附近",
              place(srv, p4, 274, 750, mapname="3"), f"map={p4.map.name} pos=({p4.x},{p4.y})")
        assert w.give_item(p4, "肉", BAG_SIZE - 1 - len(p4.bag))
        saved_gates = w.gates_by_src
        w.gates_by_src = {}
        try:
            pilot4 = make_offline(srv, p4, minutes=60)
            now = drive(pilot4, 20_000, 600)
            check("链路不通立即熔断", not pilot4.active, pilot4.reason)
            check("报告写明无可达补给点", "无可达补给点" in pilot4.reason,
                  pilot4.reason)
        finally:
            w.gates_by_src = saved_gates

        print("\n=== 8. afk.supplyEnabled=0 回退 v1（背包满停机）===")
        s5 = FakeSession()
        p5 = login(srv, s5, "SupplyD", job=0)
        p5.level = 15
        p5.hp = p5.max_hp = max(200, p5.max_hp)
        p5.gold = 1000
        clear_threats(srv, p5)
        check("p5 放到屠夫旁", place(srv, p5, 314, 272), f"pos=({p5.x},{p5.y})")
        assert w.give_item(p5, "肉", BAG_SIZE - len(p5.bag))   # 填满到 BAG_SIZE
        saved_cfg = srv.config
        srv.config = dataclasses.replace(cfg, afk_supply_enabled=0)
        try:
            pilot5 = make_offline(srv, p5, minutes=60)
            now = drive(pilot5, 20_000, 100)
            check("关闭补给后背包满走 v1 停机", not pilot5.active, pilot5.reason)
            check("停机原因为背包满（非补给）", "背包已满" in pilot5.reason,
                  pilot5.reason)
        finally:
            srv.config = saved_cfg
    finally:
        db.close()
        for suffix in ("", "-wal", "-shm"):
            q = Path(str(db_path) + suffix)
            if q.exists():
                q.unlink()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n结论: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
