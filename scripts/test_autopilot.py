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
from pyserver.world import GameMap, Monster  # noqa: E402
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


def spawn(srv, p, dx, dy, hp=1, name="稻草人"):
    w = srv.world
    t = w.monster_templates.get(name) or next(iter(w.monster_templates.values()))
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
        progress_before = (p.level, p.exp, pilot.kills)
        end = advance(srv, 80, start=end + 6000)
        # 升级会让 exp 归零重算，单看 exp 会误判；用 (等级,经验,击杀) 综合判断
        check("恢复后继续打怪", (p.level, p.exp, pilot.kills) > progress_before,
              f"lv/exp/kills {progress_before} → {(p.level, p.exp, pilot.kills)}")
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

        print("\n=== 9. 长距离移动会跑（不是一路走）===")
        clear_monsters(srv, p2)
        p2.hp = p2.max_hp
        srv.autopilot.enable(p2, "balanced", now=2000)
        pilot3 = srv.autopilot.get(p2)
        run_from = (p2.x, p2.y)
        spawn(srv, p2, 8, 0)
        s.messages.clear()
        now = 2000
        stamps = []
        for _ in range(40):
            now += 100
            srv.autopilot.tick(now)
            stamps.append(pilot3.last_move_at)
        end = now
        moves = [m for m in s.messages if m.get("type") == "move"]
        runs = [m for m in moves if m.get("run") is True]
        check("长距离移动发 run=true", len(runs) >= 1,
              f"move={len(moves)} run={len(runs)}")
        check("确实跑到了远处", (p2.x, p2.y) != run_from,
              f"{run_from} → {(p2.x, p2.y)}")
        gaps = sorted({b - a for a, b in zip(stamps, stamps[1:]) if b > a})
        check("跑步节拍快于 540ms 走", gaps and min(gaps) <= 400,
              f"移动间隔={gaps[:4]}")

        print("\n=== 10. 主动走向远处掉落物 ===")
        clear_monsters(srv, p2)
        for gid in list(srv.world.ground_items):
            del srv.world.ground_items[gid]
        base = (p2.x, p2.y)
        lx, ly = base[0] + 5, base[1]
        if not (p2.map.can_walk(lx, ly) and not p2.map.occupied(lx, ly)):
            lx, ly = base[0] + 4, base[1]
        srv.world.add_ground_item(GroundItem(
            srv.world.new_id(), None, 90, lx, ly, 0, p2.map))
        gold_before = p2.gold
        srv.autopilot.enable(p2, "balanced", now=end + 1000)
        end = advance(srv, 80, start=end + 1000, step=100)
        check("主动走过去捡起 4~5 格外的金币", p2.gold > gold_before,
              f"gold {gold_before} → {p2.gold} · pos {base} → {(p2.x, p2.y)}")

        print("\n=== 11. 隔墙不卡住、换目标 ===")
        clear_monsters(srv, p2)
        for gid in list(srv.world.ground_items):
            del srv.world.ground_items[gid]
        srv.autopilot.enable(p2, "balanced", now=end + 1000)
        pilot4 = srv.autopilot.get(p2)
        # 造一整行墙：直接改底层 grid.flags（列优先 x*height+y），寻路热路径
        # 走图级位图 walk_bits（2026-11-25 CPU 优化），旧「patch GameMap.can_walk」
        # 已不被位图感知——数据层造墙才是有效测试。置 None 强制位图重建。
        gm2 = p2.map
        wall_y = p2.y + 1
        orig_flags = bytes(gm2.grid.flags)
        for xx in range(gm2.width):
            gm2.grid.flags[xx * gm2.height + wall_y] = 1
        gm2.walk_bits = None
        try:
            behind = spawn(srv, p2, 0, 3)     # 整行墙之后，完全不可达
            spawn(srv, p2, 4, 0)             # 同侧可达
            s.messages.clear()
            exp_wall = p2.exp
            end = advance(srv, 60, start=end + 1000, step=100)
            down_moves = [m for m in s.messages
                          if m.get("type") == "move" and m.get("dir") in (3, 4, 5)]
            check("墙后目标被拉黑换目标", behind.id in pilot4.unreachable,
                  f"unreachable={list(pilot4.unreachable)[:4]}")
            check("没有反复朝墙撞", len(down_moves) <= 3,
                  f"朝墙方向 move {len(down_moves)} 次")
            check("转而击杀同侧可达怪", p2.exp > exp_wall,
                  f"exp {exp_wall} → {p2.exp}")
        finally:
            gm2.grid.flags[:] = orig_flags
            gm2.walk_bits = None

        print("\n=== 12. 空档期挖肉（safe/balanced 开、efficient 关）===")
        from pyserver.world_extra import CORPSE_MS
        clear_monsters(srv, p2)
        for gid in list(srv.world.ground_items):
            del srv.world.ground_items[gid]
        p2.hp = p2.max_hp
        w2 = srv.world
        # 杀一只鸡留在 4 格外：可挖动物尸体（race 51，leathery 50 → 3~10 刀）。
        # 服务端 _butch 按真实墙钟校验 corpse_until，测试虚拟时钟的 1000 会
        # 被判「尸体已过期」拒收，这里必须用真实 now_ms()。
        from pyserver.world import now_ms as _now_ms
        chicken = spawn(srv, p2, 4, 0, hp=1, name="鸡")
        w2.kill_monster(chicken, _now_ms(), p2)
        check("鸡尸体可挖（butchable/未挖/未变骷髅）",
              not chicken.alive and w2.butchable(chicken)
              and not chicken.bag_looted and not chicken.skeleton
              and chicken.corpse_until > _now_ms())
        srv.autopilot.enable(p2, "balanced", now=end + 1000)
        pilot5 = srv.autopilot.get(p2)
        s.messages.clear()
        bag0, butch_start = len(p2.bag), end + 1000
        # advance 的虚拟时钟远快于真实墙钟，而服务端 _butch 的 600ms 出刀
        # 节流按真实 now_ms() 校验——把 service 模块的 now_ms 打补丁成
        # 「真实起点 + 虚拟偏移」，两边节拍才一致。
        import pyserver.service as _svc
        _orig_now_ms = _svc.now_ms
        _clock = {"v": butch_start}
        _real0 = _now_ms()
        _svc.now_ms = lambda: _real0 + max(0, _clock["v"] - butch_start)
        try:
            for _ in range(140):
                _clock["v"] += 100
                srv.autopilot.tick(_clock["v"])
            end = _clock["v"]
        finally:
            _svc.now_ms = _orig_now_ms
        butch_sends = [m for m in s.messages if m.get("type") == "butch"
                       and m.get("actorId") == p2.id and m.get("meat")]
        check("走到尸体 2 格内出刀挖肉", len(butch_sends) >= 1,
              f"butch 消息 {len(butch_sends)} 条")
        check("挖空后尸体标记 bag_looted", chicken.bag_looted)
        check("挖肉产出进背包（肉/金币）",
              len(p2.bag) > bag0 or p2.gold > gold0,
              f"bag {bag0}→{len(p2.bag)}, gold {gold0}→{p2.gold}")
        check("挖完释放尸体目标锁", pilot5.butch_id == 0,
              f"butch_id={pilot5.butch_id}")
        check("挖肉期间熔断不误停（产出算进展）",
              pilot5.active or "挖肉" not in pilot5.reason,
              pilot5.reason)
        # 效率档：不开挖肉
        chicken2 = spawn(srv, p2, 5, 1, hp=1, name="鸡")
        w2.kill_monster(chicken2, _now_ms(), p2)
        srv.autopilot.enable(p2, "efficient", now=end + 1000)
        pilot6 = srv.autopilot.get(p2)
        s.messages.clear()
        end = advance(srv, 60, start=end + 1000, step=100)
        check("效率档不出刀挖肉",
              not any(m.get("type") == "butch" for m in s.messages)
              and not chicken2.bag_looted,
              f"butch={sum(1 for m in s.messages if m.get('type') == 'butch')}")
        srv.autopilot.disable(p2, "测试结束")
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
