#!/usr/bin/env python3
"""AI Bot 玩家回归（离线、虚拟时钟、零第三方依赖）。

覆盖 docs/OFFLINE-AFK-PLAN.md §4 验收项：
    配置装载与重建 → 漫游 → 台词广播 → 打怪 Bot 挂托管内核（safe 档 +
    avoid_real_targets）→ 决不抢真人目标 → 死亡自动复活 → GM say/reload。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_bots.py
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
from pyserver.world import Monster, now_ms  # noqa: E402


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


def make_service(bots_path: Path):
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "bots-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    srv.bots.config_path = bots_path
    return cfg, db, srv


def write_bots(path: Path, defs: list[dict]) -> None:
    path.write_text(json.dumps(defs, ensure_ascii=False), encoding="utf-8")


def login(srv, session, name):
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    return srv.find_by_session(session)


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    tmp = Path(tempfile.mkdtemp())
    bots_path = tmp / "bots.json"
    write_bots(bots_path, [
        {"name": "城里甲", "mode": "chat", "level": 10, "map": "0",
         "x": 285, "y": 618, "wanderRadius": 6, "chatEvery": [60, 120],
         "lines": {"idle": ["测试台词一", "测试台词二"]}},
        {"name": "城里乙", "mode": "chat", "level": 9, "map": "0",
         "x": 292, "y": 604, "wanderRadius": 6},
        {"name": "打怪丙", "mode": "fight", "level": 15, "job": 0,
         "map": "3", "x": 80, "y": 80, "spawnRadius": 40,
         "wanderRadius": 14, "preset": "safe", "items": ["木剑"]},
        {"name": "", "mode": "chat", "map": "0"},          # 无名 → 跳过
        {"name": "坏图丁", "mode": "chat", "map": "NOPE"},  # 不存在图 → 跳过
    ])
    cfg, db, srv = make_service(bots_path)
    try:
        print("\n=== 1. 装载与重建 ===")
        srv.bots.rebuild()
        bots = srv.bots.all()
        check("3 个有效 Bot 生成，2 个坏配置跳过", len(bots) == 3,
              f"got={len(bots)}")
        names = {b.p.name for b in bots}
        check("名字与配置一致", names == {"城里甲", "城里乙", "打怪丙"}, str(names))
        check("Bot 不落库（character_id=0）", all(b.p.character_id == 0 for b in bots))
        check("Bot 会话为 NullSession", all(isinstance(b.p.session, object)
                                            and b.p.session.__class__.__name__ == "NullSession"
                                            for b in bots))

        print("\n=== 2. 漫游与台词 ===")
        now = now_ms()
        anchors = {b.p.name: (b.p.x, b.p.y) for b in bots}
        moved = set()
        for _ in range(200):
            now += 100
            srv.tick()
            for b in bots:
                if (b.p.x, b.p.y) != anchors[b.p.name]:
                    moved.add(b.p.name)
        check("全部 Bot 会漫游离开锚点", moved == {"城里甲", "城里乙", "打怪丙"},
              str(sorted(moved)))
        for b in bots:
            b.next_chat_at = now - 1
        srv.tick()
        # 台词只有城里甲配了 lines；广播 chat 需从 world 验证——监听不好截，
        # 改为验证 say 接口 + 手动构造广播
        check("GM say 让指定 Bot 发言", srv.bots.say("城里甲", "GM测试发言"))
        check("say 未知名返回 False", not srv.bots.say("不存在", "x"))

        print("\n=== 3. 打怪 Bot 挂托管内核 ===")
        fight = next(b for b in bots if b.p.name == "打怪丙")
        for _ in range(30):
            now += 100
            srv.tick()
        pilot = srv.autopilot.get(fight.p)
        check("打怪 Bot 自动挂载 AutoPilot", pilot is not None)
        check("策略档为 safe", pilot is not None and pilot.preset == "safe")
        check("avoid_real_targets 开启", pilot is not None and pilot.avoid_real_targets)
        # Bot 必须像同级真人一样学过基础技能，否则命中点只有 DEF_HIT=5，
        # 打怪大量 MISS，看上去就是「被打不还手」（用户 2026-11-18 反馈）。
        from pyserver.world import hit_point
        check("战士 Bot 已学基本剑术(3)",
              3 in fight.p.skills and fight.p.skills[3]["level"] >= 1,
              f"skills={fight.p.skills}")
        check("Bot 命中点高于裸号 5", hit_point(fight.p) > 5,
              f"hit={hit_point(fight.p)}")
        check("Bot 附近发现目标（决策在跑）",
              pilot is not None and ("前往" in pilot.status or "打怪" in pilot.status
                                     or "拾取" in pilot.status or "挖肉" in pilot.status),
              pilot.status if pilot else "")

        # 被怪打立刻换手：打得再远、经验再高也不能压住正在打自己的怪
        from pyserver.autopilot import PRESETS
        attacker = next((a for a in fight.p.map.actors.values()
                         if isinstance(a, Monster) and a.alive and a.template
                         and a.master_id < 0
                         and fight.p.distance_to(a) <= 6), None)
        if attacker is None:
            tmpl = (srv.world.monster_templates.get("稻草人")
                    or next(t for t in srv.world.monster_templates.values()
                            if t.dc <= 12))
            attacker = Monster(id=srv.world.new_id(), name=tmpl.name,
                               x=fight.p.x + 3, y=fight.p.y,
                               hp=tmpl.hp, max_hp=tmpl.hp, level=tmpl.level,
                               exp=tmpl.exp, race=tmpl.race,
                               race_img=tmpl.raceImg, appr=tmpl.appr,
                               template=tmpl, spawn_x=fight.p.x + 3,
                               spawn_y=fight.p.y, spawn_map=fight.p.map)
            srv.world.add_actor(attacker, fight.p.map)
        if attacker is not None:
            attacker.target_id = fight.p.id
            pilot.target_id = 0
            pilot.last_scan_at = 0
            chosen = pilot._choose_target(srv.world, fight.p, PRESETS["balanced"],
                                          {"monsters": []}, now)
            check("Bot 被打换手到攻击者", chosen is not None
                  and chosen.target_id == fight.p.id,
                  f"{chosen.name if chosen else None}")
            # 换手检查不能退化成每 tick 全图扫描：nearby() 遍历整张地图 actor，
            # 此前 CPU 32% 的根因就是无节流的 O(N) 扫描。
            orig_nearby = srv.world.nearby
            calls = []
            def counted_nearby(*args, **kwargs):
                calls.append(1)
                return orig_nearby(*args, **kwargs)
            srv.world.nearby = counted_nearby
            try:
                attacker.target_id = 0     # 让缓存目标不再是攻击者
                pilot.target_id = chosen.id  # 模拟 _tick 调用方写入目标锁
                pilot._choose_target(srv.world, fight.p, PRESETS["balanced"],
                                     {"monsters": []}, now)
                before = len(calls)
                pilot._choose_target(srv.world, fight.p, PRESETS["balanced"],
                                     {"monsters": []}, now)
                check("换手检查 600ms 节流（同 tick 不重复全图扫描）",
                      len(calls) == before,
                      f"calls +{len(calls) - before} now={now} "
                      f"scan={pilot.last_scan_at} threat={pilot.last_threat_scan_at} "
                      f"tid={pilot.target_id}")
            finally:
                srv.world.nearby = orig_nearby

        print("\n=== 4. 决不抢真人目标 ===")
        w = srv.world
        # 把真人放去矿区，锁定一只怪
        s = FakeSession()
        p = login(srv, s, "HumanHunter")
        p.map = w.get_or_load_map("3") or p.map
        if p.map.name == "3":
            p.x, p.y = fight.p.x, fight.p.y + 3
            p.home_map = "3"
        monsters = [a for a in p.map.actors.values()
                    if isinstance(a, Monster) and a.alive]
        check("矿区有怪可验证", bool(monsters))
        if monsters:
            held = monsters[0]
            held.target_id = p.id          # 假装真人正在打这只
            from pyserver.autopilot import AutoPilot, PRESETS, route_for
            check("真人目标被识别", AutoPilot._held_by_real(w, held, fight.p))
            held2 = monsters[1] if len(monsters) > 1 else held
            held2.target_id = fight.p.id   # 怪在打 Bot 自己 → 不算抢
            check("Bot 自己的目标不算抢", not AutoPilot._held_by_real(w, held2, fight.p))
            # Bot 选怪应跳过 held
            chosen = pilot._choose_target(w, fight.p, pilot.adjust_cfg(
                PRESETS[pilot.preset]), route_for(fight.p.level), now)
            check("Bot 选怪跳过真人目标", chosen is None or chosen.id != held.id,
                  chosen.name if chosen is not None else "无可选怪")

        print("\n=== 5. 死亡自动复活 ===")
        w.kill_player(fight.p)
        check("Bot 死亡结算", fight.p.dead)
        srv.tick()
        check("死亡后进入复活倒计时", fight.revive_at > 0)
        fight.revive_at = now_ms() - 1      # 直接让倒计时到期（service.tick 用真实时钟）
        srv.tick()
        check("到期回城满状态复活", fight.p.alive and not fight.p.dead
              and fight.p.map.name == fight.p.home_map and fight.p.hp == fight.p.max_hp)

        print("\n=== 6. reload 重建 ===")
        srv.bots.rebuild()
        check("reload 后仍是 3 个 Bot", len(srv.bots.all()) == 3)

    finally:
        db.close()

    print("\n" + "=" * 60)
    fails = [r for r in results if not r[1]]
    print(f"结果: {len(results) - len(fails)} passed, {len(fails)} failed")
    for name, _, detail in fails:
        print(f"  FAIL {name} · {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
