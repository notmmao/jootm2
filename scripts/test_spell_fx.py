#!/usr/bin/env python3
"""施法特效门控的离线回归：特效只在服务端确认时下发。

原版依据（GameOfMir）：
- ``Magic.pas:270``：RM_SPELL（施法动作）在扣完 MP、进 case 前无条件广播；
- ``ObjBase.pas:21864``：TPlayObject.DoSpell MP 不足 Exit=False → 不扣 MP；
- ``ObjBase.pas:9234-9237``：DoSpell 失败 → 广播 RM_MAGICFIREFAIL（挥手放下、
  无飞行特效）；
- ``Magic.pas:426-427/714``：case 6/13~19 ``boSpellFail:=True`` + CheckAmulet
  失败 → 不发 RM_MAGICFIRE（无特效）；护身符够但打空/抗魔 → 特效照放，
  只是 boTrain=False（:715-721 boSpellFire 默认 True）。

用法：
    python scripts/test_spell_fx.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import U_ARMRINGL  # noqa: E402
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

    def types(self) -> list[str]:
        return [m.get("type", "?") for m in self.messages]

    def drain(self) -> None:
        self.messages.clear()


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-spell-fx-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    tag = str(int(time.time()) % 100000)
    name = "S" + tag
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 1,
                 "hair": 1, "job": 2},
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


def equip_amulet(w, p, count: int) -> None:
    """U_ARMRINGL（slot 5）戴 StdMode=25 Shape=5 护身符（每张 100 点耐久）。"""
    base = next(iter(w.item_templates.values()))
    idx = 900000 + len(w.item_templates)
    t = replace(base, index=idx, stdMode=25, shape=5, duraMax=count * 100)
    w.item_templates[idx] = t
    p.equipment[U_ARMRINGL] = {
        "makeIndex": 800001, "index": idx, "durability": count * 100,
        "durabilityMax": count * 100, "lucky": 0, "slot": -1}


def learn(p, magic_id: int, lv: int = 0) -> None:
    p.skills[magic_id] = {"level": lv, "train": 0, "key": 0}


def cast(service, sess, p, magic_id: int, target_id: int = 0):
    p.skill_cooldown.clear()
    sess.drain()
    service._cast_magic(sess, {"type": "castMagic", "magicId": magic_id,
                               "targetX": p.x + 1, "targetY": p.y,
                               "targetId": target_id})


def main() -> int:
    print("== 施法特效门控回归 ==")
    w, p, sess, service = build()
    p.equipment = [None] * 13

    # 学火球(1)/治愈(2)/灵魂火符(13)，便于直接驱动 _cast_magic
    learn(p, 1)
    learn(p, 2)
    learn(p, 13)

    print("\n[1] 没蓝：广播 spellFail、不扣 MP、无 magicFire")
    p.mp = 0
    cast(service, sess, p, 1)
    check("收到 spellFail", any(m.get("type") == "spellFail" for m in sess.messages),
          f"types={sess.types()}")
    check("无 magicFire", not any(m.get("type") == "magicFire" for m in sess.messages))
    check("不扣 MP", p.mp == 0)

    print("\n[2] 灵魂火符没护身符：spellCast(动作) + spellFail，无 magicFire，MP 照扣")
    p.mp = 500
    cast(service, sess, p, 13)
    check("收到 spellCast（动作照播，对齐 RM_SPELL）",
          any(m.get("type") == "spellCast" for m in sess.messages),
          f"types={sess.types()}")
    check("收到 spellFail", any(m.get("type") == "spellFail" for m in sess.messages))
    check("无 magicFire", not any(m.get("type") == "magicFire" for m in sess.messages))
    check("MP 已扣（原版 DoSpell 先 DamageSpell）", p.mp < 500, f"mp={p.mp}")

    print("\n[3] 灵魂火符有护身符：spellCast + magicFire")
    equip_amulet(w, p, 5)
    cast(service, sess, p, 13)
    check("收到 magicFire（特效照放）",
          any(m.get("type") == "magicFire" and m.get("magicId") == 13
              for m in sess.messages),
          f"types={sess.types()}")
    check("无 spellFail", not any(m.get("type") == "spellFail" for m in sess.messages))

    print("\n[4] 火球空挥（无目标）：软失败特效照放（原版 boSpellFire 默认 True）")
    cast(service, sess, p, 1, target_id=-1)
    check("收到 magicFire",
          any(m.get("type") == "magicFire" and m.get("magicId") == 1
              for m in sess.messages),
          f"types={sess.types()}")
    check("无 spellFail", not any(m.get("type") == "spellFail" for m in sess.messages))

    print("\n[5] 魔法命中广播带 noAttackAnim（施法动作不被挥砍覆盖，RM_MAGSTRUCK 不给攻击者发挥击）")
    t = w.monster_templates.get("稻草人") or next(iter(w.monster_templates.values()))
    mob = Monster(id=w.new_id(), name=t.name, x=p.x + 1, y=p.y, hp=max(20, t.hp),
                  max_hp=max(20, t.hp), level=t.level, exp=t.exp,
                  race=t.race, race_img=t.raceImg, appr=t.appr, template=t,
                  spawn_x=p.x + 1, spawn_y=p.y, spawn_map=p.map)
    w.add_actor(mob, p.map)
    p.skill_cooldown.clear()
    sess.drain()
    service._cast_magic(sess, {"type": "castMagic", "magicId": 1,
                               "targetX": p.x + 1, "targetY": p.y,
                               "targetId": mob.id})
    hits = [h for h in sess.messages if h.get("type") == "attack"]
    check("魔法命中 attack 广播已下发", any(h.get("targetId") == mob.id for h in hits),
          f"types={sess.types()}")
    check("命中广播带 noAttackAnim（前端不再把 spell 覆盖成砍怪动作）",
          any(h.get("targetId") == mob.id and h.get("noAttackAnim") for h in hits),
          f"hits={hits}")

    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
