#!/usr/bin/env python3
"""托管功能优化回归（离线）：法师远程 / 背包限流清肉 / 武器与护身符自动补戴。

行为规格：
- 法师(job=1)：射程内按 雷电术(11)>大火球(5)>火球术(1) 优先级施法；没蓝/没学
  不贴身砍怪（等回蓝）；超射程照常走近。施法按原版客户端节拍
  (500+200+技能Delay) 节流，不机关枪连发（基础节拍 SPELL_INPUT_BASE_MS）；
  「空蓝才喝」——蓝量（含回蓝池）不够放最低耗蓝技能时才喝蓝药。
- 动作锁：出刀/施法未落地（CanNextAction 要求 IsIdle）前不移动，对齐原版。
- 背包：余量不足停挖肉；背包 8 格告警时按品质从差到好丢肉，留 MEAT_KEEP 份。
- 装备：武器清槽后从背包自动穿耐久最高的下一把；护身符/毒粉耗尽自动补戴。
  施法前即时补符，避免「有符在背包却回落近战」。
- 「不挖肉」独立开关：勾选后任何档位都不挖肉（含清空低品质肉动作）。

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

from pyserver.autopilot import (BAG_SIZE, BUTCH_BAG_RESERVE, MEAT_KEEP,  # noqa: E402
                                SPELL_INPUT_BASE_MS, _min_mage_spell_cost)
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
        self.uses: list[int] = []
        self.butches: int = 0
        self.moves: list[int] = []
        self._orig_use = srv._use_item
        self._orig_cast = srv._cast_magic
        self._orig_attack = srv._attack
        self._orig_take_on = srv._take_on_item
        self._orig_drop = srv._drop
        self._orig_butch = srv._butch
        self._orig_move = srv._move
        srv._use_item = self._use_rec
        srv._cast_magic = self._cast_rec
        srv._attack = self._attack_rec
        srv._take_on_item = self._take_on_rec
        srv._drop = self._drop_rec
        srv._butch = self._butch_rec
        srv._move = self._move_rec

    def _use_rec(self, s, m):
        self.uses.append(int(m.get("makeIndex", 0)))
        return self._orig_use(s, m)

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

    def _move_rec(self, s, m):
        self.moves.append(int(m.get("dir", 0)))
        return self._orig_move(s, m)

    def reset(self):
        self.casts.clear()
        self.take_on.clear()
        self.drops.clear()
        self.uses.clear()
        self.attacks = 0
        self.butches = 0
        self.moves.clear()


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
    mob.hp = mob.max_hp = 99999                          # 别被打死，保证一直有目标
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
    # 原版客户端施法节拍 = SPELL_INPUT_BASE_MS + 火球Delay(60/10) ≈ 1106ms
    fireball_beat = (SPELL_INPUT_BASE_MS + w.magic_templates[1].delay / 10) / 1000
    tick(srv, 1, step=fireball_beat + 0.05)     # 间隔要够一拍
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

    # ============ 2b. 原版施法节拍 ============
    print("\n[2b] 原版客户端施法节拍：不机关枪连发")
    p4, sess4 = login(srv, "MAGECD", job=1)
    p4.skills[1] = {"level": 0, "train": 0, "key": 0}
    p4.mp = max(p4.mp, 2000)
    ap.enable(p4, "balanced")
    clear_mobs(srv, p4)
    mob4 = spawn(srv, p4, 3, 0)
    mob4.hp = mob4.max_hp = 99999                        # 别打死，一直有目标
    p4.skill_cooldown.clear()
    rec.reset()
    tick(srv, 4, step=0.15)     # 4 次 tick 共 0.6s，远小于原版一拍
    check("快速连点只放出 1 发火球", len(rec.casts) == 1, f"casts={rec.casts}")
    # 项目 t.delay 是 DB Delay×10（火球 600ms），原版客户端节拍要用 DB 原值
    fireball_beat = (SPELL_INPUT_BASE_MS + w.magic_templates[1].delay / 10) / 1000
    time.sleep(fireball_beat)
    srv.autopilot.tick(now_ms())
    check("过一拍才放第 2 发", len(rec.casts) == 2, f"casts={rec.casts}")
    ap.disable(p4, "测试切换")

    # ============ 2c. 法师「空蓝才喝」============
    print("\n[2c] 法师空蓝（不够放最低耗蓝技能）才喝蓝药")
    p5, sess5 = login(srv, "MANAOPT", job=1)
    p5.skills[1] = {"level": 0, "train": 0, "key": 0}
    ap.enable(p5, "balanced")
    clear_mobs(srv, p5)
    idx_mana = idx + 4
    w.item_templates[idx_mana] = replace(base, index=idx_mana, stdMode=0,
                                         shape=1, acMin=0, macMin=50,
                                         duraMax=100, name="测试魔法药")
    mana = w.new_item_instance(w.item_templates[idx_mana])
    p5.bag.append(mana)
    p5.inc_spell = 0
    min_cost = _min_mage_spell_cost(w, p5)
    assert min_cost is not None and min_cost > 0

    # 蓝够放技能时不喝（新口径：不再按 20%/35% 蓝线就灌）
    p5.mp = min_cost + 5
    rec.reset()
    tick(srv, 1)
    check("蓝够时不喝蓝药", mana["makeIndex"] not in rec.uses,
          f"mp={p5.mp} need={min_cost} uses={rec.uses}")

    # 蓝不够放最低一招 → 喝
    p5.mp = min_cost - 1
    mp_before = p5.mp
    rec.reset()
    tick(srv, 1)
    check("空蓝自动喝蓝药(useItem)", mana["makeIndex"] in rec.uses,
          f"uses={rec.uses}")
    check("喝药后 MP 实际提升", p5.mp > mp_before, f"{mp_before} -> {p5.mp}")
    check("蓝药已从背包消耗",
          all(it.get("makeIndex") != mana["makeIndex"] for it in p5.bag))
    ap.disable(p5, "测试切换")

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

    # ============ 6. 攻击/施法动作锁：动作未落地不移动 ============
    print("\n[6] 施法/出刀未落地时不移动（对齐原版 CanNextAction=IsIdle）")
    p6, sess6 = login(srv, "ACTLOCK", job=1)
    p6.skills[1] = {"level": 0, "train": 0, "key": 0}     # 火球术
    p6.mp = 2000
    ok, _ = ap.enable(p6, "balanced")
    assert ok
    clear_mobs(srv, p6)
    mob6 = spawn(srv, p6, 10, 0)                          # 10 格外：需要先走近
    mob6.hp = mob6.max_hp = 99999
    p6.skill_cooldown.clear()
    pilot6 = ap.get(p6)
    # 手动制造「刚施法」状态（动作锁起点），怪在射程外本应触发 _step_to
    stamp = now_ms()
    pilot6.last_cast_at = stamp
    pilot6.last_cast_beat = 1106
    pilot6.last_attack_at = 0
    rec.reset()
    srv.autopilot.tick(stamp + 100)                        # 动作 100ms，远未落地
    check("施法动作未落地不移动", len(rec.moves) == 0, f"moves={rec.moves}")
    # 动作落地后再 tick → 允许移动
    srv.autopilot.tick(stamp + 1200)
    check("动作落地后可继续移动", len(rec.moves) >= 1, f"moves={rec.moves}")
    ap.disable(p6, "测试切换")

    # ============ 7. 不挖肉独立开关 ============
    print("\n[7] 「不挖肉」勾选后任何档位都不挖肉")
    p7, sess7 = login(srv, "NOBUTCH", job=0)
    ok, _ = ap.enable(p7, "safe", no_butch=True)           # safe 档默认挖肉，但开关覆盖
    assert ok
    assert ap.get(p7).no_butch is True
    clear_mobs(srv, p7)
    # 造一具可挖尸体放在身边（挖肉只需距离 ≤2）；butchable 看 template.race
    from pyserver.world import Monster as _M
    chicken_t = next((t for t in w.monster_templates.values()
                      if int(t.race) == 51), None)
    assert chicken_t is not None, "找不到鸡模板，无法测挖肉"
    m7 = _M(id=w.new_id(), name=chicken_t.name, x=p7.x + 1, y=p7.y,
            hp=1, max_hp=1, level=1, exp=1, race=51, race_img=chicken_t.raceImg,
            appr=chicken_t.appr, template=chicken_t,
            spawn_x=p7.x + 1, spawn_y=p7.y, spawn_map=p7.map)
    m7.alive = False
    m7.corpse_until = now_ms() + 180_000
    w.add_actor(m7, p7.map)
    rec.reset()
    tick(srv, 3, step=0.3)
    check("勾选不挖肉→safe 档也不出刀", rec.butches == 0, f"butches={rec.butches}")
    check("state_dict 回传 noButch",
          ap.state_dict(p7, ap.get(p7), "safe")["noButch"] is True)
    # 关闭开关 → 恢复挖肉
    srv.on_message(sess7, json.dumps({"type": "autopilot", "action": "option",
                                      "noButch": False}, ensure_ascii=False))
    check("option 消息即时关掉不挖肉", ap.get(p7).no_butch is False)
    rec.reset()
    tick(srv, 3, step=0.75)
    check("关掉开关后恢复挖肉", rec.butches >= 1, f"butches={rec.butches}")
    ap.disable(p7, "测试结束")

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
