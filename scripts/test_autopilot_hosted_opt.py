#!/usr/bin/env python3
"""托管功能优化回归（离线）：法师远程 / 背包限流清肉 / 武器与护身符自动补戴。

行为规格：
- 法师(job=1)：射程内按 雷电术(11)>大火球(5)>火球术(1) 优先级施法；没蓝/没学
  不贴身砍怪（等回蓝）；超射程照常走近。
- 背包：余量不足停挖肉；背包 8 格告警时按品质从差到好丢肉，留 MEAT_KEEP 份。
- 装备：武器清槽后从背包自动穿耐久最高的下一把；护身符/毒粉耗尽自动补戴。
  施法前即时补符，避免「有符在背包却回落近战」。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_autopilot_hosted_opt.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.autopilot import BAG_SIZE, BUTCH_BAG_RESERVE, MEAT_KEEP  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import U_ARMRINGL, U_BUJUK, U_WEAPON  # noqa: E402
from pyserver.world import Monster, now_ms  # noqa: E402

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

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        pass


def make_service():
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "autopilot-hosted-opt.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def login(srv, name, job=1):
    sess = FakeSession()
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(sess)
    assert p is not None, "登录链路失败"
    return p, sess


def spawn(srv, p, dx, dy, name="稻草人"):
    w = srv.world
    t = w.monster_templates.get(name) or next(iter(w.monster_templates.values()))
    m = Monster(id=w.new_id(), name=t.name, x=p.x + dx, y=p.y + dy,
                hp=t.hp, max_hp=max(1, t.hp), level=t.level, exp=t.exp,
                race=t.race, race_img=t.raceImg, appr=t.appr, template=t,
                spawn_x=p.x + dx, spawn_y=p.y + dy, spawn_map=p.map)
    w.add_actor(m, p.map)
    return m


def clear_mobs(srv, p):
    w = srv.world
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            w.actors.pop(a.id, None)


class Recorder:
    def __init__(self, srv):
        self.casts: list[int] = []
        self.attacks: int = 0
        self.take_on: list[int] = []
        self.drops: list[int] = []
        self.butches: int = 0
        self._orig_cast = srv._cast_magic
        self._orig_attack = srv._attack
        self._orig_take_on = srv._take_on_item
        self._orig_drop = srv._drop
        self._orig_butch = srv._butch
        srv._cast_magic = self._cast_rec
        srv._attack = self._attack_rec
        srv._take_on_item = self._take_on_rec
        srv._drop = self._drop_rec
        srv._butch = self._butch_rec

    def _cast_rec(self, s, m):
        self.casts.append(int(m.get("magicId", 0)))
        return self._orig_cast(s, m)

    def _attack_rec(self, s, m):
        self.attacks += 1
        return self._orig_attack(s, m)

    def _take_on_rec(self, s, m):
        self.take_on.append(int(m.get("makeIndex", 0)))
        return self._orig_take_on(s, m)

    def _drop_rec(self, s, m):
        self.drops.append(int(m.get("makeIndex", 0)))
        return self._orig_drop(s, m)

    def _butch_rec(self, s, m):
        self.butches += 1
        return self._orig_butch(s, m)

    def reset(self):
        self.casts.clear()
        self.take_on.clear()
        self.drops.clear()
        self.attacks = 0
        self.butches = 0


def tick(srv, n=1, step=0.65):
    for _ in range(n):
        time.sleep(step)
        srv.autopilot.tick(now_ms())


def main() -> None:
    srv = make_service()
    w = srv.world
    rec = Recorder(srv)
    ap = srv.autopilot

    # 造几件测试装备模板：武器（StdMode 5）、护身符（25/Shape 5）、毒粉（25/Shape 1）
    base = next(iter(w.item_templates.values()))
    idx = 930000
    w.item_templates[idx] = replace(base, index=idx, stdMode=5, shape=0,
                                    duraMax=100, needLevel=1, name="测试木剑")
    idx_amu = idx + 1
    w.item_templates[idx_amu] = replace(base, index=idx_amu, stdMode=25, shape=5,
                                        duraMax=1000, needLevel=1, name="测试护身符")
    idx_poi = idx + 2
    w.item_templates[idx_poi] = replace(base, index=idx_poi, stdMode=25, shape=1,
                                        duraMax=1000, needLevel=1, name="测试毒粉")

    # ============ 1. 法师远程 ============
    print("\n[1] 法师按等级选最优单体技能远程攻击")
    p, sess = login(srv, "MAGEOPT", job=1)
    p.skills[1] = {"level": 0, "train": 0, "key": 0}     # 火球术
    p.skills[5] = {"level": 0, "train": 0, "key": 0}     # 大火球
    p.skills[11] = {"level": 0, "train": 0, "key": 0}    # 雷电术
    p.mp = max(p.mp, 2000)
    ok, _ = ap.enable(p, "balanced")
    assert ok
    clear_mobs(srv, p)
    mob = spawn(srv, p, 3, 0)
    p.skill_cooldown.clear()
    rec.reset()
    tick(srv, 1)
    check("优先放雷电术(11)", rec.casts[:1] == [11], f"casts={rec.casts}")
    check("法师远程不出近战", rec.attacks == 0)

    # 只学火球术 → 退化到火球术
    del p.skills[11]
    del p.skills[5]
    p.skill_cooldown.clear()
    rec.reset()
    tick(srv, 1)
    check("只学火球术→放火球术(1)", rec.casts[:1] == [1], f"casts={rec.casts}")

    print("\n[2] 法师没蓝不贴身砍怪")
    p.mp = 0
    mob.hp = mob.max_hp = 99999                         # 别被打死
    p.skill_cooldown.clear()
    rec.reset()
    tick(srv, 3)
    check("没蓝不放技能", 11 not in rec.casts and 1 not in rec.casts,
          f"casts={rec.casts}")
    check("没蓝不出近战砍怪", rec.attacks == 0, f"attacks={rec.attacks}")
    ap.disable(p, "测试切换")

    # ============ 3. 武器自动换装 ============
    print("\n[3] 武器损毁→自动从背包补耐久最高的下一把")
    p2, sess2 = login(srv, "WAROPT", job=0)
    ok, _ = ap.enable(p2, "balanced")
    assert ok
    clear_mobs(srv, p2)
    p2.equipment[U_WEAPON] = None                        # 模拟武器损毁清槽
    # 背包放两把武器，耐久 30 / 80
    low = w.new_item_instance(w.item_templates[idx])
    low["durability"] = 30
    high = w.new_item_instance(w.item_templates[idx])
    high["durability"] = 80
    p2.bag.extend([low, high])
    rec.reset()
    tick(srv, 2)
    check("武器槽已补上", p2.equipment[U_WEAPON] is not None)
    check("选了耐久最高的那把(80)",
          p2.equipment[U_WEAPON] is not None
          and p2.equipment[U_WEAPON]["makeIndex"] == high["makeIndex"])
    check("补戴走 takeOnItem handler", high["makeIndex"] in rec.take_on,
          f"take_on={rec.take_on}")

    # ============ 4. 护身符/毒粉自动补戴 ============
    print("\n[4] 护身符与毒粉耗尽→自动补戴")
    p2.equipment[U_BUJUK] = None
    p2.equipment[U_ARMRINGL] = None
    amu = w.new_item_instance(w.item_templates[idx_amu]); amu["durability"] = 500
    poi = w.new_item_instance(w.item_templates[idx_poi]); poi["durability"] = 300
    p2.bag.extend([amu, poi])
    rec.reset()
    # EQUIP_CHECK_INTERVAL_MS=1000，间隔要 >1s 才不会被节流挡住
    tick(srv, 2, step=1.2)
    check("护身符槽已补上",
          p2.equipment[U_BUJUK] is not None or p2.equipment[U_ARMRINGL] is not None)
    worn = [a for a in (p2.equipment[U_BUJUK], p2.equipment[U_ARMRINGL]) if a]
    check("补的是护身符+毒粉两类", len(worn) == 2, f"worn={len(worn)}")
    ap.disable(p2, "测试切换")

    # ============ 5. 背包限流 + 清低品质肉 ============
    print("\n[5] 背包余量不足→停挖肉；吃紧→丢最差品质的肉")
    p3, sess3 = login(srv, "BAGOPT", job=0)
    ok, _ = ap.enable(p3, "safe")
    assert ok
    clear_mobs(srv, p3)
    pilot = ap.get(p3)
    # 肉模板 StdMode 40
    idx_meat = idx + 3
    w.item_templates[idx_meat] = replace(base, index=idx_meat, stdMode=40,
                                         shape=0, duraMax=100, name="测试肉")
    def make_meat(dura):
        it = w.new_item_instance(w.item_templates[idx_meat])
        it["durability"] = dura
        return it
    # 停挖肉：把背包填到阈值以上
    p3.bag.clear()
    filler = w.new_item_instance(w.item_templates[idx])   # 武器当填充（非肉）
    filler["durability"] = 100
    while len(p3.bag) < BAG_SIZE - BUTCH_BAG_RESERVE:
        p3.bag.append(dict(filler))
    rec.reset()
    tick(srv, 1)
    check("背包逼近满→不出刀挖肉", rec.butches == 0, f"butches={rec.butches}")

    # 清理：背包再塞 8 份肉（品质 10..80），应丢掉品质最低的，留 MEAT_KEEP 份
    p3.bag.clear()
    meats = [make_meat(10 * (i + 1)) for i in range(8)]
    p3.bag.extend(meats)
    while len(p3.bag) < BAG_SIZE - BUTCH_BAG_RESERVE:
        p3.bag.append(dict(filler))
    rec.reset()
    # CLEAN_INTERVAL_MS=2000，逐份丢避免刷屏：tick 4 次 × 2.1s 丢 4 份
    tick(srv, 4, step=2.1)
    check("背包吃紧时有丢肉动作", len(rec.drops) >= 1, f"drops={rec.drops}")
    check("丢的是品质最低的肉(耐久10)",
          rec.drops and rec.drops[0] == meats[0]["makeIndex"],
          f"期望 {meats[0]['makeIndex']} 实得 {rec.drops[:1]}")
    # 每次只丢一份：4 次 tick 丢 4 份 → 8-4=4 ≤ MEAT_KEEP(6)
    remaining_meat = sum(1 for it in p3.bag
                         if (w.item_templates.get(int(it.get("index", 0))) or base).stdMode == 40)
    check("保留的肉不超过 MEAT_KEEP 上限", remaining_meat <= MEAT_KEEP,
          f"剩 {remaining_meat} 份")
    ap.disable(p3, "测试结束")

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
