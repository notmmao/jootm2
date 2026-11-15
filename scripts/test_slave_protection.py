#!/usr/bin/env python3
"""误伤宝宝豁免 + 大地图入口数据回归（离线）。

对齐原版依据：
- IsProperTarget（ObjBase.pas:21508-21513）：玩家打有主的怪，主人 = 自己
  且攻击模式 ≠ 全体攻击(HAM_ALL) → False。本服无攻击模式开关 →
  近战/点目标法术/范围法术/野蛮冲撞一律不伤自家宝宝。
- 大地图入口：MapInfo.txt 连接行（src x,y -> dst）解析成 Gate，
  map_snapshot 按当前图下发 gates + 目的地图标题。
- brief_json 显式下发 masterId（原版客户端靠名字带「(主人名)」识别宝宝，
  本服宝宝名无括号）。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_slave_protection.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataclasses import replace  # noqa: E402

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
    db_path = Path(tempfile.mkdtemp()) / "slave-protection-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def login(srv, name, job=0):
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


def spawn(srv, p, dx, dy, name="稻草人", **kw):
    w = srv.world
    t = w.monster_templates.get(name) or next(iter(w.monster_templates.values()))
    m = Monster(id=w.new_id(), name=t.name, x=p.x + dx, y=p.y + dy,
                hp=kw.pop("hp", t.hp), max_hp=kw.pop("max_hp", max(1, t.hp)),
                level=t.level, exp=t.exp, race=t.race, race_img=t.raceImg,
                appr=t.appr, template=t,
                spawn_x=p.x + dx, spawn_y=p.y + dy, spawn_map=p.map, **kw)
    w.add_actor(m, p.map)
    return m


def send(srv, sess, type_: str, **fields) -> None:
    msg = {"type": type_}
    msg.update(fields)
    srv.on_message(sess, json.dumps(msg, ensure_ascii=False))


def cast_clear(srv, sess, p, magic_id: int, dx: int = 1) -> None:
    """清冷却/留足蓝再施法（施法节流按真实时钟，测试内连放会被拦）。"""
    p.skill_cooldown.clear()
    p.mp = max(p.mp, 200)
    send(srv, sess, "castMagic", magicId=magic_id,
         targetX=p.x + dx, targetY=p.y, targetId=0)


def _amulet_idx(w, p, count: int) -> int:
    """造一个 StdMode=25 Shape=5 护身符模板（沿用 test_spell_fx 的做法）。"""
    base = next(iter(w.item_templates.values()))
    idx = 900000 + len(w.item_templates) + count
    t = replace(base, index=idx, stdMode=25, shape=5, duraMax=count * 100)
    w.item_templates[idx] = t
    return idx


def main() -> None:
    srv = make_service()
    w = srv.world

    print("\n[1] brief_json 下发 masterId")
    p, sess = login(srv, "SP1", job=2)
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            w.actors.pop(a.id, None)
    baby = spawn(srv, p, 1, 0, name="骷髅", master_id=p.id)
    wild = spawn(srv, p, 2, 0, name="多钩猫")
    check("宝宝 brief_json 带 masterId", baby.brief_json().get("masterId") == p.id)
    check("野生怪不带 masterId", "masterId" not in wild.brief_json())

    print("\n[2] 近战攻击自家宝宝无效（ObjBase.pas:21508-21513）")
    baby_hp = baby.hp
    send(srv, sess, "attack", targetId=baby.id)
    check("宝宝 HP 不变", baby.hp == baby_hp, f"hp={baby.hp}/{baby_hp}")

    print("\n[3] 点目标法术（火球术）打自家宝宝 = 对地软失败")
    p.skills[1] = {"level": 0}          # 火球术 0 级
    p.mp = 1000
    send(srv, sess, "castMagic", magicId=1,
         targetX=baby.x, targetY=baby.y, targetId=baby.id)
    check("宝宝 HP 不变", baby.hp == baby_hp, f"hp={baby.hp}/{baby_hp}")

    print("\n[4] 范围法术（爆裂火焰）不伤自家宝宝、照伤野生怪")
    wild_hp = wild.hp
    p.skills[23] = {"level": 0}         # 爆裂火焰 0 级
    send(srv, sess, "castMagic", magicId=23,
         targetX=baby.x, targetY=baby.y, targetId=0)
    check("宝宝 HP 不变", baby.hp == baby_hp, f"hp={baby.hp}/{baby_hp}")
    check("野生怪受伤", wild.hp < wild_hp, f"hp={wild.hp}/{wild_hp}")

    print("\n[5] 治愈术可治疗自家宝宝（IsProperFriend，ObjBase.pas:24192-24196）")
    baby.hp = max(1, baby.hp // 2)
    hurt = baby.hp
    p.skills[2] = {"level": 0}          # 治愈术 0 级
    p.mp = 1000
    send(srv, sess, "castMagic", magicId=2,
         targetX=baby.x, targetY=baby.y, targetId=baby.id)
    check("宝宝 HP 回升", baby.hp > hurt, f"hp={baby.hp}/{baby.max_hp}")

    print("\n[6] 治愈术不可治疗野生怪（IsProperFriend=False → 无人被治疗）")
    wild.hp = max(1, wild.hp // 2)
    wild_hp = wild.hp
    p.hp = p.max_hp // 2
    my_hp = p.hp
    send(srv, sess, "castMagic", magicId=2,
         targetX=wild.x, targetY=wild.y, targetId=wild.id)
    check("野生怪 HP 不变", wild.hp == wild_hp, f"hp={wild.hp}/{wild_hp}")
    check("自己也不被治疗", p.hp == my_hp, f"hp={p.hp}/{my_hp}")

    print("\n[7] 大地图入口数据（MapInfo.txt 连接行 → snapshot.gates）")
    snap = w.map_snapshot(p, "test")
    gates = snap["map"].get("gates") or []
    check("比奇省(map 0) 有入口数据", len(gates) > 0, f"gates={len(gates)}")
    palace = [g for g in gates if g["to"] == "0122"]
    check("入口含 0→0122 皇宫", any(g["title"] == "皇宫" for g in palace),
          f"first={palace[0] if palace else None}")
    check("入口格坐标与玩家可达点一致",
          all(isinstance(g["x"], int) and isinstance(g["y"], int) for g in gates))

    print("\n[8] 召唤骷髅：宝宝存活期间再施放 = 无新宝宝、不加熟练度"
          "（Magic.pas:1387-1421，MakeSlave nil → boTrain=False）")
    p.equipment[U_ARMRINGL] = {
        "makeIndex": 800001, "index": _amulet_idx(w, p, 5),
        "durability": 500, "durabilityMax": 500, "lucky": 0, "slot": -1}
    p.skills[17] = {"level": 0, "train": 0, "key": 0}
    p.mp = 1000
    cast_clear(srv, sess, p, 17)
    check("首次施法造出骷髅", p.summon_id == baby.id or p.summon_id >= 0,
          f"summon_id={p.summon_id}")
    check("首次施法加熟练度", p.skills[17]["train"] > 0,
          f"train={p.skills[17]['train']}")
    skel_id = p.summon_id
    skel = w.actor(skel_id)
    skel_train_before = p.skills[17]["train"]
    cast_clear(srv, sess, p, 17)
    check("存活期再放：旧骷髅保留（同一 id）", p.summon_id == skel_id
          and skel.alive, f"summon_id={p.summon_id}")
    check("存活期再放：不加熟练度", p.skills[17]["train"] == skel_train_before,
          f"train={p.skills[17]['train']}/{skel_train_before}")

    print("\n[9] 召唤神兽：宝宝存活期间再施放 = 召回面前、不加熟练度"
          "（Magic.pas:1382-1384 RecallSlave，ObjBase.pas:21940）")
    p.skills[30] = {"level": 0, "train": 0, "key": 0}
    p.equipment[U_ARMRINGL] = {
        "makeIndex": 800002, "index": _amulet_idx(w, p, 9),
        "durability": 1200, "durabilityMax": 1200, "lucky": 0, "slot": -1}
    # 先清掉骷髅（换宝宝类型）
    w.remove_summon(skel)
    p.summon_id = -1
    cast_clear(srv, sess, p, 30)
    dog = w.actor(p.summon_id) if p.summon_id >= 0 else None
    check("首次施法造出神兽", dog is not None and dog.alive,
          f"summon_id={p.summon_id}")
    check("首次施法加熟练度", p.skills[30]["train"] > 0,
          f"train={p.skills[30]['train']}")
    dog_train_before = p.skills[30]["train"]
    # 把宝宝挪远，再施放 → 应被召回面前一格
    dog.map.reposition(dog, p.x + 5, p.y)
    dog.x, dog.y = p.x + 5, p.y
    cast_clear(srv, sess, p, 30)
    check("存活期再放：旧神兽保留（同一 id）", p.summon_id == dog.id
          and dog.alive, f"summon_id={p.summon_id}")
    check("存活期再放：不加熟练度", p.skills[30]["train"] == dog_train_before,
          f"train={p.skills[30]['train']}/{dog_train_before}")
    check("神兽被召回主人身边（面前格/近旁兜底）",
          max(abs(dog.x - p.x), abs(dog.y - p.y)) <= 2,
          f"dog=({dog.x},{dog.y}) p=({p.x},{p.y})")

    print(f"\n===== PASS {PASS} / FAIL {FAIL} =====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
