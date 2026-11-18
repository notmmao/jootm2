#!/usr/bin/env python3
"""法师回蓝期偷跑 + 濒死回城卷保命回归（离线、虚拟时钟）。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_mage_flee.py
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
from pyserver.world import Monster  # noqa: E402


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


PASS = FAIL = 0


def check(label, cond, detail="") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


def setup(job: int = 1):
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "mage-flee.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    s = FakeSession()
    for msg in ({"type": "register", "username": "MageFlee", "password": "test12345"},
                {"type": "login", "username": "MageFlee", "password": "test12345"},
                {"type": "createCharacter", "name": "MageFlee", "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": "MageFlee"},
                {"type": "noticeOk", "name": "MageFlee"}):
        srv.on_message(s, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(s)
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            srv.world.actors.pop(a.id, None)
    p.map_change_at = 0
    return cfg, db, db_path, srv, s, p


def spawn(srv, p, dx, dy, name="稻草人"):
    w = srv.world
    t = w.monster_templates.get(name) or next(iter(w.monster_templates.values()))
    m = Monster(id=w.new_id(), name=t.name, x=p.x + dx, y=p.y + dy,
                hp=max(1, t.hp), max_hp=max(1, t.hp), level=t.level, exp=t.exp,
                race=t.race, race_img=t.raceImg, appr=t.appr, template=t,
                spawn_x=p.x + dx, spawn_y=p.y + dy, spawn_map=p.map)
    w.add_actor(m, p.map)
    return m


def cleanup(db, db_path) -> None:
    db.close()
    for suffix in ("", "-wal", "-shm"):
        q = Path(str(db_path) + suffix)
        if q.exists():
            q.unlink()


def main() -> int:
    print("=== 1. 没蓝被怪盯上：偷跑而不是站桩/迎面走近 ===")
    cfg, db, db_path, srv, s, p = setup()
    try:
        p.hp = p.max_hp = 300
        p.mp = 0                       # 一招都放不出
        m = spawn(srv, p, 6, 0)        # 施法射程内、贴身距离外
        m.target_id = p.id             # 怪已锁定玩家
        srv.autopilot.enable(p, "balanced", now=1000)
        pilot = srv.autopilot.get(p)
        s.messages.clear()
        x0 = p.x
        srv.autopilot.tick(1000)
        moves = [m for m in s.messages if m.get("type") == "move"]
        check("空蓝被盯上发出跑动", len(moves) == 1 and moves[0].get("run") is True,
              f"moves={moves}")
        check("朝远离怪的方向移动", p.x < x0, f"x {x0} → {p.x}")
        check("状态文案为偷跑", "偷跑" in pilot.status, pilot.status)

        print("\n=== 2. 有蓝时正常输出（不误触发偷跑）===")
        p.mp = p.max_mp = 200
        p.skills[1] = {"level": 1, "train": 0, "key": 0}
        s.messages.clear()
        pilot.last_move_at = 0
        srv.autopilot.tick(1500)
        check("有蓝时不进入偷跑状态", "偷跑" not in pilot.status
              and pilot.active, f"status={pilot.status} active={pilot.active} reason={pilot.reason}")
    finally:
        cleanup(db, db_path)

    print("\n=== 3. 濒死且有回城卷：回城保命后停机 ===")
    cfg, db, db_path, srv, s, p = setup()
    try:
        p.bag.clear()                  # 新号自带药水：清掉才能走到「无药濒死」分支
        p.hp, p.max_hp = 10, 300       # 3% < 均衡档 retreat_hp 28%
        srv.world.give_item(p, "回城卷", 1)
        check("背包有回城卷", any(it.get("name") == "回城卷" for it in p.bag))
        srv.autopilot.enable(p, "balanced", now=1000)
        pilot = srv.autopilot.get(p)
        s.messages.clear()
        srv.autopilot.tick(1000)
        check("用掉回城卷", not any(it.get("name") == "回城卷" for it in p.bag))
        check("停机保命", not pilot.active and "回城" in pilot.reason, pilot.reason)

        print("\n=== 4. 濒死但没卷：维持旧行为（直接停机）===")
        cfg2, db2, db_path2, srv2, s2, p2 = setup()
        try:
            p2.bag.clear()
            p2.hp, p2.max_hp = 10, 300
            srv2.autopilot.enable(p2, "balanced", now=2000)
            pilot2 = srv2.autopilot.get(p2)
            srv2.autopilot.tick(2000)
            check("无卷停机且原因不含回城", not pilot2.active
                  and "回城" not in pilot2.reason, pilot2.reason)
        finally:
            cleanup(db2, db_path2)
    finally:
        cleanup(db, db_path)

    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
