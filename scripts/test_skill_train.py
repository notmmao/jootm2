#!/usr/bin/env python3
"""剑术/精神力战法修炼点回归（纯标准库，离线）。

原版依据（权威=火炬 ObjBase.pas）：
- :4243-4297 扫描 MagicList 时，战士的基本剑术（3）与道士的精神力战法（4）
  **共用同一个 PSwordSkill 指针**（case 4 在 case 3 之后，两者都学则 4 覆盖 3）；
- :5463-5471 物理命中且目标 RaceServer>=RC_ANIMAL 时对该指针
  ``TrainSkill(1+Random(3))``——所以道士平A涨的是精神力战法（2026-11-23 用户
  反馈「精神力战法不涨修炼值」：旧实现只写死技能 3）。
- 前置：btLevel<3 且 NeedLevel[btLevel] <= 人物等级（:5464-5465）。

用法：
    python scripts/test_skill_train.py
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


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-train-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    tag = str(int(time.time()) % 100000)
    name = "T" + tag
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
    p.level = 30
    return w, p, sess, service


def add_mon_template(w, **kw):
    fields = dict(name="修炼测试怪", race=80, appr=20, level=3, hp=100000,
                  ac=0, mac=0, dc=1, maxDc=2, speed=0, hit=0,
                  walkSpeed=800, attackSpeed=1000)
    fields.update(kw)
    base = next(iter(w.monster_templates.values()))
    return replace(base, **fields)


def spawn_mon(w, p, t) -> Monster:
    m = Monster(id=w.new_id(), name=t.name, x=p.x + 1, y=p.y, dir=4,
                hp=t.hp, max_hp=t.hp, level=t.level, race=t.race,
                template=t, spawn_x=p.x + 1, spawn_y=p.y)
    w.add_actor(m, p.map)
    return m


def give_weapon(w, p):
    base = next(iter(w.item_templates.values()))
    idx = 950000 + len(w.item_templates)
    t = replace(base, index=idx, stdMode=5, shape=1, duraMax=60000,
                dcMin=5, dcMax=10)
    w.item_templates[idx] = t
    p.equipment[1] = {"makeIndex": 999001, "index": idx, "durability": 60000,
                      "durabilityMax": 60000, "lucky": 0, "slot": -1}
    p.recompute(w)


def hit(w, p, service, m, frozen_val=8):
    """冻结随机命中一次（randint 恒 frozen_val → 伤害>0、加点恒 frozen_val）。"""
    real = (random.randint, random.randrange)
    random.randint = lambda a, b: frozen_val   # noqa: E731
    random.randrange = lambda n: 0            # noqa: E731
    try:
        service._attack_monster(p, m)
    finally:
        random.randint, random.randrange = real


def main() -> int:
    w, p, sess, service = build()
    give_weapon(w, p)

    print("\n[1] 道士场景：只学精神力战法(4) → 命中涨 4 不涨 3")
    p.skills.clear()
    p.skills[4] = {"level": 0, "train": 0, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    sess.drain()
    hit(w, p, service, m, frozen_val=8)
    check("精神力战法修炼点 +8（1+Random(3) 冻结为 8 越界→取 randint 定值）",
          int(p.skills[4]["train"]) == 8, f"train={p.skills[4]['train']}")
    check("未学的基本剑术(3)不被创建", 3 not in p.skills)
    m.alive = False; m.hp = 0; p.map.actors.pop(m.id, None)

    print("\n[2] 战士场景：只学基本剑术(3) → 命中涨 3（回归不破坏）")
    p.skills.clear()
    p.skills[3] = {"level": 0, "train": 0, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    hit(w, p, service, m, frozen_val=5)
    check("基本剑术修炼点 +5", int(p.skills[3]["train"]) == 5,
          f"train={p.skills[3]['train']}")
    m.alive = False; m.hp = 0; p.map.actors.pop(m.id, None)

    print("\n[3] 原版指针语义：3/4 都学 → 只涨 4（case 4 覆盖 PSwordSkill）")
    p.skills.clear()
    p.skills[3] = {"level": 0, "train": 0, "key": 0}
    p.skills[4] = {"level": 0, "train": 0, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    hit(w, p, service, m, frozen_val=7)
    check("精神力战法(4) +7", int(p.skills[4]["train"]) == 7,
          f"train={p.skills[4]['train']}")
    check("基本剑术(3)不再加点（指针被 4 占用）",
          int(p.skills[3]["train"]) == 0, f"train={p.skills[3]['train']}")
    m.alive = False; m.hp = 0; p.map.actors.pop(m.id, None)

    print("\n[4] 前置条件：满级不涨 / 人物等级不足不涨")
    p.skills.clear()
    p.skills[4] = {"level": 3, "train": 0, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    hit(w, p, service, m, frozen_val=9)
    check("精神力战法满级(3)不涨", int(p.skills[4]["train"]) == 0)
    p.skills.clear()
    p.skills[4] = {"level": 0, "train": 0, "key": 0}
    p.level = 5   # NeedL1=9 > 5
    hit(w, p, service, m, frozen_val=9)
    check("人物等级 5 < NeedLevel[0]=9 不涨", int(p.skills[4]["train"]) == 0)
    p.level = 30
    m.alive = False; m.hp = 0; p.map.actors.pop(m.id, None)

    print("\n[5] 升级链路：修炼点攒满 100 → 升到 1 级并扣点")
    p.skills.clear()
    p.skills[4] = {"level": 0, "train": 95, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    sess.drain()
    hit(w, p, service, m, frozen_val=8)   # 95+8=103 ≥ 100 → 升级余 3
    check("精神力战法升到 1 级", int(p.skills[4]["level"]) == 1,
          f"lv={p.skills[4]['level']}")
    check("升级扣减 MaxTrain（余 3 点）", int(p.skills[4]["train"]) == 3,
          f"train={p.skills[4]['train']}")
    check("升级提示下发（修炼成功）",
          any("修炼成功" in str(m2.get("text", "")) for m2 in sess.messages),
          str([m2.get("type") for m2 in sess.messages])[:80])
    m.alive = False; m.hp = 0; p.map.actors.pop(m.id, None)

    print("\n[6] 攻杀剑术(7)加点不受影响（bo21 一击 +1..3）")
    p.skills.clear()
    p.skills[7] = {"level": 0, "train": 0, "key": 0}
    p.skills[3] = {"level": 0, "train": 0, "key": 0}
    m = spawn_mon(w, p, add_mon_template(w))
    hit(w, p, service, m, frozen_val=4)
    check("基本剑术 +4（无 4 时指针回落 3）", int(p.skills[3]["train"]) == 4,
          f"train={p.skills[3]['train']}")

    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
