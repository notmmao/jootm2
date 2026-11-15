#!/usr/bin/env python3
"""在线托管 AutoPilot 专项回归（离线、虚拟时钟、零第三方依赖）。

覆盖 docs/AUTOPILOT-PLAN.md 的验收项：
    开启 → 自动寻路/打怪/拾取 → 手动接管 → 再开启 → 死亡停止 → 断线停止
    → 重登不恢复 → 不落库 → 卡住熔断 → 与手动共用的节拍。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_autopilot.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.autopilot import (DEFAULT_PRESET, PRESETS, STUCK_MS,  # noqa: E402
                                WALK_INTERVAL_MS)
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster  # noqa: E402
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
    db_path = Path(tempfile.mkdtemp()) / "autopilot-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return cfg, db, db_path, GameService(cfg, db)


def login(srv, session, name):
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(session)
    assert p is not None, "登录链路失败"
    return p


def clear_monsters(srv, p):
    """把当前图原有 2600+ 只怪清掉，保证测试目标唯一、结果可复现。"""
    w = srv.world
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            w.actors.pop(a.id, None)


def spawn(srv, p, dx, dy, hp=1):
    w = srv.world
    t = w.monster_templates.get("稻草人") or next(iter(w.monster_templates.values()))
    m = Monster(id=w.new_id(), name=t.name, x=p.x + dx, y=p.y + dy,
                hp=hp, max_hp=max(hp, t.hp), level=t.level, exp=t.exp,
                race=t.race, race_img=t.raceImg, appr=t.appr, template=t,
                spawn_x=p.x + dx, spawn_y=p.y + dy, spawn_map=p.map)
    w.add_actor(m, p.map)
    return m


def advance(srv, ticks: int, start: int = 1000, step: int = 100) -> int:
    """只推进 AutoPilot 的虚拟时钟（怪物 AI 不参与，专测托管动作）。"""
    now = start
    for _ in range(ticks):
        now += step
        srv.autopilot.tick(now)
    return now


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    cfg, db, db_path, srv = make_service()
    try:
        s = FakeSession()
        name = "AutoPilotTest"
        p = login(srv, s, name)
        clear_monsters(srv, p)
        p.hp = p.max_hp = max(100, p.max_hp)
        p.mp = p.max_mp = max(100, p.max_mp)
        exp0, gold0 = p.exp, p.gold

        print("\n=== 1. 未开启时不动作 ===")
        m1 = spawn(srv, p, 3, 0)
        advance(srv, 20)
        check("未开启时经验不变", p.exp == exp0)
        check("未开启时坐标不变", (p.x, p.y) == (m1.x - 3, m1.y))

        print("\n=== 2. 开启 + 节拍 + 打怪 + 拾取 ===")
        ok, why = srv.autopilot.enable(p, "balanced", now=1000)
        pilot = srv.autopilot.get(p)
        check("开启成功", ok and pilot is not None and pilot.active, why)
        check("默认档位为均衡", pilot.preset == "balanced")
        # 金币掉在脚下：验证拾取链路
        gi = GroundItem(srv.world.new_id(), None, 50, p.x, p.y, 0, p.map)
        srv.world.add_ground_item(gi)
        srv.autopilot.tick(1100)
        pos_after_1 = (p.x, p.y)
        srv.autopilot.tick(1150)     # 距上一步仅 50ms
        check(f"走路共用 {WALK_INTERVAL_MS}ms 节拍", (p.x, p.y) == pos_after_1,
              f"pos={pos_after_1}")
        end = advance(srv, 120, start=1150)
        check("拾取了地面金币", p.gold > gold0, f"gold {gold0} → {p.gold}")
        check("怪物被击杀", not m1.alive or m1.hp <= 0)
        check("获得经验", p.exp > exp0, f"exp {exp0} → {p.exp}")
        check("击杀计数递增", pilot.kills >= 1, f"kills={pilot.kills}")
        check("金币已从地面移除", gi.id not in srv.world.ground_items)

        print("\n=== 3. 聊天不打断、手动操作立即接管 ===")
        srv.on_message(s, json.dumps({"type": "say", "text": "边聊天边托管"},
                                     ensure_ascii=False))
        check("聊天不暂停托管", pilot.active)
        exp_before_manual = p.exp
        srv.on_message(s, json.dumps({"type": "move", "dir": 2}))
        check("手动 move 立即暂停", not pilot.active, pilot.reason)
        spawn(srv, p, 1, 0)
        advance(srv, 60, start=end)
        check("暂停后不再打怪", p.exp == exp_before_manual,
              f"exp={p.exp}")

        print("\n=== 4. 再点一次才恢复 + 策略档白名单 ===")
        srv._autopilot(s, {"type": "autopilot", "action": "on", "preset": "safe"})
        check("显式再开恢复托管", pilot.active and pilot.preset == "safe")
        exp_before_again = p.exp
        end = advance(srv, 80, start=end + 6000)
        check("恢复后继续打怪", p.exp > exp_before_again,
              f"exp {exp_before_again} → {p.exp}")
        srv.autopilot.enable(p, "不存在的档", now=end)
        check("非法档位回落默认档", pilot.preset == DEFAULT_PRESET)

        print("\n=== 5. REST 只读观察 ===")
        payload = srv.handle_admin("GET", "/api/autopilot", {}, b"")
        data = json.loads(payload or "{}")
        check("REST 返回托管快照", data.get("count") == 1
              and data["pilots"][0]["name"] == p.name
              and len(data.get("presets", [])) == len(PRESETS))

        print("\n=== 5b. 低血自动喝药 ===")
        pot_t = next((t for t in srv.world.item_templates.values()
                      if t.stdMode == 0 and t.acMin > 0 and t.shape != 1), None)
        if pot_t is None:
            check("存在可用的回血药模板", False)
        else:
            pot = srv.world.new_item_instance(pot_t)
            p.bag.append(pot)
            p.hp = max(1, int(p.max_hp * 0.3))
            p.inc_health = 0
            p.last_use_item_at = 0
            bag_before = len(p.bag)
            srv.autopilot.tick(end + 300)
            # stdMode 0 普通药水按原版进回血池，不是立即加 hp；
            # inc_health > 0 与背包少一件共同证明药水被真正消耗。
            check("低血自动使用药水",
                  p.inc_health > 0 and len(p.bag) < bag_before,
                  f"inc_health={p.inc_health} · 药={pot_t.name} · "
                  f"背包 {bag_before}→{len(p.bag)}")
            p.hp = p.max_hp

        print("\n=== 6. 死亡停止 ===")
        p.dead, p.alive = True, False
        srv.autopilot.tick(end + 100)
        check("死亡后实例被清除", srv.autopilot.get(p) is None)
        p.dead, p.alive = False, True

        print("\n=== 7. 断线停止 + 落盘 + 重登不恢复 ===")
        srv.autopilot.enable(p, "balanced", now=end + 200)
        check("断线前托管开启", srv.autopilot.get(p) is not None)
        srv.on_disconnect(s)
        check("on_disconnect 清空实例", srv.autopilot.get(p) is None)
        rec = db.find_character(name)
        check("经验已落盘", rec is not None and rec["exp"] > 0,
              f"db exp={rec['exp'] if rec else '?'}")
        cols = [r[1] for r in db.conn.execute("pragma table_info(characters)")]
        check("characters 表无托管字段",
              not any("autopilot" in c or "pilot" in c for c in cols))
        srv._do_enter(s, name)
        p2 = srv.find_by_session(s)
        check("重登后不自动恢复托管", p2 is not None
              and srv.autopilot.get(p2) is None)

        print("\n=== 8. 卡住熔断 ===")
        clear_monsters(srv, p2)
        p2.hp = p2.max_hp = max(100, p2.max_hp)
        srv.autopilot.enable(p2, "safe", now=1000)
        p2.exp = 0
        srv.autopilot.get(p2).last_exp = 0
        srv.autopilot.get(p2).last_progress_at = 1000
        advance(srv, STUCK_MS // 100 + 20, start=1000)
        check("长时间零经验自动停机", srv.autopilot.get(p2) is None)
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
