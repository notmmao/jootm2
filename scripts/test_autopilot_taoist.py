#!/usr/bin/env python3
"""道士托管行为回归（离线）：治愈术回血优先、灵魂火符远程攻击。

行为规格：
- 血线 ≤ potion_hp（均衡档 0.55）时，道士有蓝且冷却好 → 施治愈术
  （magicId=2，不吃符，目标为空默认自己，Magic.pas:301-317）；没学/没蓝/
  冷却中 → 喝药兜底。没药没蓝到撤退线 → 熔断；有蓝则继续治愈自救。
- 有怪且距离 ≤ 8（施法半径，Magic.pas:266）→ 优先灵魂火符（magicId=13，
  每次耗 1 张符）；没学/没蓝/没符（CheckAmulet 只查装备槽）→ 近战兜底；
  冷却中保持远程站位；超射程先走近，进入射程转远程。
- 治愈成功的 tick 不再攻击（一个 tick 一个主动动作）。

用法（务必用 3.10；动作走真实节拍，测试内按 600ms 步进）：
    D:/Python310/python.exe scripts/test_autopilot_taoist.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataclasses import replace  # noqa: E402

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import U_BUJUK  # noqa: E402
from pyserver.world import Monster, now_ms  # noqa: E402
from pyserver.handlers_combat import spell_cost, skill_level  # noqa: E402
from pyserver.autopilot import _find_potion as _find_potion_in_bag  # noqa: E402

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
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = False

    def drain(self) -> None:
        self.messages.clear()


def make_service():
    cfg = load()
    db_path = Path(tempfile.mkdtemp()) / "autopilot-taoist-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def login(srv, name, job=2):
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
    """记录托管代发的动作（castMagic/useItem/attack）。"""

    def __init__(self, srv):
        self.casts: list[int] = []
        self.uses: list[int] = []
        self.attacks: int = 0
        srv._cast_magic = self._wrap(srv._cast_magic, self.casts)
        srv._use_item = self._wrap(srv._use_item, self.uses)
        self._orig_attack = srv._attack
        srv._attack = self._attack_rec

    def _wrap(self, fn, sink):
        def inner(session, msg):
            sink.append(int(msg.get("magicId", msg.get("makeIndex", 0))))
            return fn(session, msg)
        return inner

    def _attack_rec(self, session, msg):
        self.attacks += 1
        return self._orig_attack(session, msg)

    def reset(self):
        self.casts.clear()
        self.uses.clear()
        self.attacks = 0


def main() -> None:
    srv = make_service()
    w = srv.world
    rec = Recorder(srv)
    ap = srv.autopilot

    p, sess = login(srv, "TAO1")
    p.skills[2] = {"level": 0, "train": 0, "key": 0}    # 治愈术
    p.skills[13] = {"level": 0, "train": 0, "key": 0}   # 灵魂火符
    # 护身符模板（1 张 = 100 耐久）挂装备槽
    base = next(iter(w.item_templates.values()))
    idx = 920000
    w.item_templates[idx] = replace(base, index=idx, stdMode=25, shape=5,
                                    duraMax=1000, name="测试护身符")

    def wear_amulet(dura: int = 500):
        amulet = w.new_item_instance(w.item_templates[idx])
        amulet["durability"] = dura
        p.equipment[U_BUJUK] = amulet
        return amulet

    wear_amulet()

    ok, _ = ap.enable(p, "balanced")
    assert ok, "托管开启失败"
    pilot = ap.get(p)

    def tick(n=1, step=0.6):
        """动作走真实节拍：每个 tick 间隔 step 秒（跑 360ms / 走 540ms）。"""
        for _ in range(n):
            time.sleep(step)
            ap.tick(now_ms())

    print("\n[1] 灵魂火符远程攻击：怪在射程内 → castMagic 13，不出近战")
    mob = spawn(srv, p, 3, 0)          # 距离 3，射程 8 内
    p.mp = max(p.mp, 300)
    rec.reset()
    tick(1)
    check("施放了灵魂火符(13)", 13 in rec.casts, f"casts={rec.casts}")
    check("没有近战攻击", rec.attacks == 0)
    check("符被消耗 500→400",
          p.equipment[U_BUJUK]["durability"] == 400)

    print("\n[2] 治愈术优先回血：扣血后 castMagic 2，且本 tick 不再攻击")
    p.hp = max(1, int(p.max_hp * 0.3))   # 低于 potion_hp=0.55
    p.skill_cooldown.clear()
    p.mp = max(p.mp, 300)
    rec.reset()
    tick(1)
    check("施放了治愈术(2)", rec.casts[:1] == [2], f"casts={rec.casts}")
    check("治愈本 tick 不放火符/不出近战",
          13 not in rec.casts and rec.attacks == 0,
          f"casts={rec.casts} attacks={rec.attacks}")
    check("没有喝药", rec.uses == [])
    check("蓝被扣除", p.mp < 300)

    print("\n[3] 治愈冷却中血线低 → 喝药兜底")
    p.hp = max(1, int(p.max_hp * 0.3))
    rec.reset()
    tick(1, step=0.3)
    check("冷却中不重复施治愈", 2 not in rec.casts)
    check("喝药兜底", len(rec.uses) >= 1, f"uses={rec.uses}")

    print("\n[4] 没符（卸下）→ 贴身近战兜底")
    p.hp = p.max_hp
    p.skill_cooldown.clear()
    p.equipment[U_BUJUK] = None
    clear_mobs(srv, p)
    spawn(srv, p, 1, 0)                # 贴身 dist=1，不需走近
    rec.reset()
    tick(3)
    check("不施火符", 13 not in rec.casts, f"casts={rec.casts}")
    check("近战攻击", rec.attacks >= 1, f"attacks={rec.attacks}")

    print("\n[5] 没蓝 → 近战兜底（不刷「魔法值不足」）")
    wear_amulet()
    p.hp = p.max_hp
    p.skill_cooldown.clear()
    p.mp = 0
    rec.reset()
    msgs_before = len(sess.messages)
    tick(4)
    check("没蓝不施火符", 13 not in rec.casts)
    check("没蓝不施治愈", 2 not in rec.casts)
    sys_texts = [m.get("text", "") for m in sess.messages[msgs_before:]
                 if m.get("type") == "systemMessage"]
    check("没有「魔法值不足」刷屏",
          sum(1 for t in sys_texts if "魔法值不足" in t) == 0,
          f"sys={sys_texts}")

    print("\n[6] 超射程（距离 10）先走近，进射程后转远程")
    p.hp = p.max_hp
    p.mp = max(p.mp, 300)
    p.skill_cooldown.clear()
    clear_mobs(srv, p)
    far = spawn(srv, p, 10, 0)
    rec.reset()
    tick(1)
    check("超射程不施火符", 13 not in rec.casts)
    check("在走近（位置变化）", (p.x, p.y) != (far.x - 10, far.y),
          f"pos=({p.x},{p.y})")
    tick(12)
    check("进射程后施火符", 13 in rec.casts, f"casts={rec.casts}")

    print("\n[7] 治愈术不吃符：卸符后仍能治愈")
    clear_mobs(srv, p)
    p.equipment[U_BUJUK] = None
    p.inc_health = 0                   # 清掉 [3] 喝药残留的回血池
    p.hp = max(1, int(p.max_hp * 0.3))
    p.skill_cooldown.clear()
    p.mp = max(p.mp, 300)
    rec.reset()
    tick(1)
    if 2 not in rec.casts:
        t2 = w.magic_templates.get(2)
        print(f"  [diag] active={pilot.active} status={pilot.status!r} "
              f"dead={p.dead} hp={p.hp}/{p.max_hp} pool={p.inc_health} "
              f"mp={p.mp} job={p.job} t2={t2 is not None} "
              f"delay={getattr(t2, 'delay', None)} "
              f"cost={spell_cost(t2, skill_level(p, 2)) if t2 else None} "
              f"cd2={p.skill_cooldown.get(2)}")
    check("卸符后治愈照放", 2 in rec.casts, f"casts={rec.casts}")

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
