#!/usr/bin/env python3
"""武器/装备持久磨损的离线回归（纯标准库）。

对齐原版依据（权威=火炬；2026-11-23 逐行核对后修正两处偏差）：
- ``TCreature.DoDamageWeapon``（火炬 ObjBase.pas:5187-5228）：攻击命中磨损
  ``Random(5)+2 - AddAbil.WeaponStrong``（:5438/5529-5531）；节流
  ``Round(持久/1000)``（:5194/5224，与衣服一致；GameOfMir 老版 :18975 的
  /1.03 不作权威）；扣到 0 置 Dura:=0。
- ``TCreature.StruckDamage``（火炬 ObjBase.pas:3464-3575）：被击伤害 > 0 时
  衣服固定磨损 ``Random(10)+5``（:3487-3518），**其余槽** i=1..11 各 1/8
  概率磨损同量（:3527-3564）：循环从 1 起（衣服不二次磨损）、跳过 U_BUJUK(9)、
  不含 U_CHARM(12)；节流 ``Round(持久/1000)``；到 0 损毁清槽。
- 挖矿磨损 ``Random(15)+5``。

用法：
    python scripts/test_durability.py
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
from pyserver.util import delphi_round  # noqa: E402
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


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-dura-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    tag = str(int(time.time()) % 100000)
    name = "D" + tag
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
    idx = 900000 + len(w.item_templates)
    t = replace(base, index=idx, **kw)
    w.item_templates[idx] = t
    return t


def equip(p, t, slot: int, dura: int | None = None, **inst_kw) -> dict:
    it = {"makeIndex": 800000 + slot, "index": t.index,
          "durability": dura if dura is not None else t.duraMax,
          "durabilityMax": t.duraMax, "lucky": 0, "slot": -1}
    it.update(inst_kw)
    p.equipment[slot] = it
    return it


def frozen_rand(range_int: int, randint_val: int):
    """把 world 模块用的 random.randint / random.randrange 冻成定值。"""
    real_randint, real_randrange = random.randint, random.randrange
    random.randint = lambda a, b: randint_val          # noqa: E731
    random.randrange = lambda n: range_int             # noqa: E731
    return real_randint, real_randrange


def restore_rand(real):
    random.randint, random.randrange = real


def test_do_damage_weapon(w, p, sess):
    print("\n[1] DoDamageWeapon：磨损 / SM_DURACHANGE 节流 / 到 0 损毁")
    wpn_t = add_template(w, stdMode=5, shape=1, duraMax=3000, dcMin=2, dcMax=5)
    it = equip(p, wpn_t, 1, dura=3000)
    sess.drain()
    w.do_damage_weapon(p, 10)
    check("磨损扣减准确", int(it["durability"]) == 2990,
          f"dura={it['durability']}")
    # /1000 节流（火炬 :5194）：3000→2990 显示点 3→3 不变，**不应**下发
    check("同显示点磨损不下发（/1000 节流）",
          not any(m.get("type") == "duraChange" for m in sess.messages),
          f"types={sess.types()}")
    # 跨显示点：1500(显示2) 扣 10 → 1490(显示1) 应下发
    it["durability"] = 1500
    sess.drain()
    w.do_damage_weapon(p, 10)
    check("跨显示点下发 duraChange(slot=1)",
          any(m.get("type") == "duraChange" and m.get("slot") == 1
              and m.get("durability") == 1490 for m in sess.messages),
          f"types={sess.types()}")
    sess.drain()
    # 节流（火炬 :5194）：Round(dura/1000) 未变则不发。d=1000 → 1，
    # 扣 1 到 999 → Round(0.999)=1 不变；再扣到 998... 找一个跨界的对比。
    quiet_d = None
    for d in range(2, 4000):
        if delphi_round(d / 1000) == delphi_round((d - 1) / 1000):
            quiet_d = d
            break
    if quiet_d is not None:
        it["durability"] = quiet_d
        sess.drain()
        w.do_damage_weapon(p, 1)
        check(f"节流生效（d={quiet_d} Round(d/1000) 不变则不下发）",
              not any(m.get("type") == "duraChange" for m in sess.messages),
              f"types={sess.types()}")
    else:
        check("节流边界扫描", False, "未找到 Round(d/1000) 不变的 d")
    # 到 0 损毁清槽
    it["durability"] = 5
    sess.drain()
    w.do_damage_weapon(p, 10)
    check("到 0 武器清槽（原版 SendDelItems + wIndex:=0）",
          p.equipment[1] is None)
    check("损毁后下发 bagUpdate（外观/背包刷新）",
          any(m.get("type") == "bagUpdate" for m in sess.messages),
          f"types={sess.types()}")


def test_wear_struck(w, p, sess):
    print("\n[2] StruckDamage：衣服固定磨损 / 其余 1/8 / 符与护身豁免 / 损毁")
    dress_t = add_template(w, stdMode=10, shape=1, duraMax=5000, acMin=2, acMax=2)
    wpn_t = add_template(w, stdMode=5, shape=1, duraMax=5000, dcMin=2, dcMax=5)
    buju_t = add_template(w, stdMode=25, shape=1, duraMax=5000)
    charm_t = add_template(w, stdMode=53, shape=1, duraMax=5000)
    dress = equip(p, dress_t, 0, dura=5000)
    wpn = equip(p, wpn_t, 1, dura=5000)
    bujuk = equip(p, buju_t, 9, dura=5000)   # U_BUJUK：原版循环跳过
    charm = equip(p, charm_t, 12, dura=5000)  # U_CHARM：原版 i=1..11 不含
    sess.drain()
    # 冻结随机：StruckDamage 的 Random(10)+5 → 定值 10（randint(5,14)），
    # Random(8) → 0（1/8 分支恒中）
    real = frozen_rand(0, 10)
    try:
        w.wear_struck(p, 100)
    finally:
        restore_rand(real)
    # 火炬 :3487 衣服只固定磨损一次；:3527 循环从 1 起不再碰衣服
    check("衣服固定磨损 10（不再二次磨损）",
          int(dress["durability"]) == 4990, f"dress={dress['durability']}")
    check("武器 1/8 磨损 10", int(wpn["durability"]) == 4990,
          f"wpn={wpn['durability']}")
    check("护身符(9) 豁免磨损", int(bujuk["durability"]) == 5000,
          f"bujuk={bujuk['durability']}")
    check("护身(12) 不参与循环", int(charm["durability"]) == 5000,
          f"charm={charm['durability']}")
    check("显示值不变则不下发 duraChange",
          not any(m.get("type") == "duraChange" for m in sess.messages),
          f"types={sess.types()}")
    sess.drain()
    # 显示值 Round(dura/1000) 跨界才发：1500/1000=1.5→2，固定磨损 10 后
    # 1490 → 1.49→1 应立即下发；武器循环磨损到 1490 同一次受击内也只发一条
    dress["durability"] = 1500
    real = frozen_rand(0, 10)
    try:
        w.wear_struck(p, 100)
    finally:
        restore_rand(real)
    dc = [m for m in sess.messages if m.get("type") == "duraChange"
          and m.get("slot") == 0]
    check("跨显示 1 点才下发 duraChange",
          len(dc) == 1 and dc[0].get("durability") == 1490,
          f"dress={dress['durability']} dc={[m.get('durability') for m in dc]}")
    sess.drain()
    # 伤害 ≤0 不磨损（上一轮衣服 1500→1490 后应保持不动）
    real = frozen_rand(0, 10)
    try:
        w.wear_struck(p, 0)
    finally:
        restore_rand(real)
    check("伤害 0 不磨损", int(dress["durability"]) == 1490)
    # 到 0 损毁：衣服一次受击固定磨损定值 10（冻随机），10-10=0 即清槽
    dress["durability"] = 10
    sess.drain()
    real = frozen_rand(0, 10)
    try:
        w.wear_struck(p, 100)
    finally:
        restore_rand(real)
    check("衣服到 0 损毁清槽", p.equipment[0] is None)
    check("损毁后下发 bagUpdate",
          any(m.get("type") == "bagUpdate" for m in sess.messages))


def add_mon_template(w, **kw):
    fields = dict(name="持久测试怪", race=80, appr=20, level=3, hp=1000,
                  ac=0, mac=0, dc=1, maxDc=2, speed=0, hit=0,
                  walkSpeed=800, attackSpeed=1000)
    fields.update(kw)
    base = next(iter(w.monster_templates.values()))
    return replace(base, **fields)


def spawn_mon(w, p, service, t) -> Monster:
    m = Monster(id=w.new_id(), name=t.name, x=p.x + 1, y=p.y, dir=4,
                hp=t.hp, max_hp=t.hp, level=t.level, race=t.race,
                template=t, spawn_x=p.x + 1, spawn_y=p.y)
    w.add_actor(m, p.map)
    return m


def test_attack_wear(w, p, sess, service):
    print("\n[3] 攻击命中磨损 Random(5)+2-strong")
    wpn_t = add_template(w, stdMode=5, shape=1, duraMax=3000, dcMin=2, dcMax=5,
                         strong=1)
    equip(p, wpn_t, 1, dura=1500)   # 显示点边界附近：磨损 1..5 必跨 /1000
    p.recompute(w)
    # 模板 speed/hit=0 → melee_hit 目标命中点 0 → 必中（world.py:862-864）
    m = spawn_mon(w, p, service, add_mon_template(w))
    sess.drain()
    service._attack_monster(p, m)
    wpn = p.equipment[1]
    wear = 1500 - int(wpn["durability"]) if wpn else -1
    check("命中后武器磨损 Random(5)+2-1 ∈ [1,5]", 1 <= wear <= 5,
          f"wear={wear}")
    check("攻击命中下发 duraChange",
          any(mm.get("type") == "duraChange" for mm in sess.messages),
          f"types={sess.types()}")
    p.map.actors.pop(m.id, None)


def test_monster_hit_player(w, p, sess, service):
    print("\n[4] 玩家被怪击 → StruckDamage 磨损装备")
    dress_t = add_template(w, stdMode=10, shape=1, duraMax=5000, acMin=2, acMax=2)
    dress = equip(p, dress_t, 0, dura=5000)
    m = spawn_mon(w, p, service, add_mon_template(w, dc=5, maxDc=8))
    sess.drain()
    w._apply_hit_to_player(m, p, 50)
    check("被怪击中衣服固定磨损 5..14（循环不再碰衣服）",
          0 < 5000 - int(dress["durability"]) <= 14,
          f"wear={5000 - int(dress['durability'])}")
    check("受击下发 stats", any(mm.get("type") == "stats" for mm in sess.messages))
    p.hp = p.max_hp


def main() -> int:
    print("== 武器/装备持久磨损回归 ==")
    w, p, sess, service = build()
    p.equipment = [None] * 13
    test_do_damage_weapon(w, p, sess)
    test_wear_struck(w, p, sess)
    test_attack_wear(w, p, sess, service)
    test_monster_hit_player(w, p, sess, service)
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
