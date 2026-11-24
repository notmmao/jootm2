#!/usr/bin/env python3
"""法师风筝判定回归（离线，零第三方依赖）。

用户 2026-11-25：「法师离线或者托管被怪物近身要学会风筝」。
核心修复：`_kite_worthy` 旧口径只看模板数值（maxDc≥12 / hp≥200），
沃玛/祖玛系弱兵怪够不着阈值 → 贴身站桩对砍。新口径追加：怪正在
锁定我（target_id == 我的 player_id）一律值得撤——挨打本身即证据。

用法（务必 3.10）：
    D:/Python310/python.exe scripts/test_mage_kite.py
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.autopilot import AutoPilot  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def main() -> int:
    cfg = load()
    db = Database(Path(tempfile.mkdtemp()) / "kite-test.db")
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    w = srv.world
    base_t = next(iter(w.monster_templates.values()))
    weak = replace(base_t, name="弱兵", maxDc=5, hp=30)   # 双阈值都够不着
    strong = replace(base_t, name="强怪", maxDc=20, hp=999)

    class FakeTarget:
        pass

    pilot = AutoPilot.__new__(AutoPilot)
    pilot.player_id = 777

    t1 = FakeTarget()
    t1.template = weak
    t1.target_id = -1
    t2 = FakeTarget()
    t2.template = weak
    t2.target_id = 777          # 正在锁定我
    t3 = FakeTarget()
    t3.template = strong
    t3.target_id = 123          # 强怪但锁的是别人
    t4 = FakeTarget()
    t4.template = None
    t4.target_id = -1

    print("\n[1] _kite_worthy 新口径")
    check("弱兵且没锁定我：不值得撤（别对鸡稻草人乱跑）",
          not AutoPilot._kite_worthy(pilot, w, t1))
    check("弱兵但锁定我：值得撤（挨打即证据）",
          AutoPilot._kite_worthy(pilot, w, t2))
    check("强怪模板：无条件值得撤",
          AutoPilot._kite_worthy(pilot, w, t3))
    check("无模板怪：不撤", not AutoPilot._kite_worthy(pilot, w, t4))

    print("\n[2] _flee_threat：没蓝偷跑能认出锁定我的弱兵")
    p = Monster(id=w.new_id(), name=weak.name, x=50, y=50, hp=30, max_hp=30,
                level=1, exp=0, race=weak.race, race_img=weak.raceImg,
                appr=weak.appr, template=weak, spawn_x=50, spawn_y=50,
                spawn_map=None)
    p.target_id = 777
    gm = w.default_map
    p.map = gm
    p.spawn_map = gm
    w.add_actor(p, gm)

    class Stub:
        pass
    stub = Stub()
    stub.map = gm
    stub.id = 777
    stub.x, stub.y = 52, 51
    stub.distance_to = lambda o: max(abs(stub.x - o.x), abs(stub.y - o.y))
    threat = AutoPilot._flee_threat(pilot, w, stub, 1_000)
    check("锁定我的怪被识别为威胁", threat is p if hasattr(threat, "id") else threat is not None,
          f"threat={getattr(threat, 'name', threat)}")
    p.target_id = -1
    w.actors.pop(p.id, None)
    db.close()

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
