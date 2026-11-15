#!/usr/bin/env python3
"""托管修复专项回归（离线）：捡拾震荡 / 误打召唤物 / 卷轴特效 / 怪转火宝宝。

对齐原版依据：
- 召唤物不算打怪目标：托管选怪跳过 ``master_id >= 0``（道士打自己的宝宝
  = 原版 IsProperTarget 不会把自家 slave 当目标）。
- 捡拾被占格跳过：原版必须站上物品格（ObjBase.pas:1696），落点被活物
  占着永远进不去，追过去只会来回掉头震荡。
- 随机卷轴/回城卷传送走完整 SpaceMove 两段特效（ObjBase.pas:20573/20590 →
  RM_SPACEMOVE_FIRE/SHOW），与宝宝跟随传送一致。
- 怪物仇恨含召唤物：TAnimalObject.Struck → SetTargetCreat(hiter)，宝宝
  打怪后怪转火宝宝（IsProperTarget 含宠物/骷髅）。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_hosted_fixes.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.autopilot import PRESETS  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
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
    db_path = Path(tempfile.mkdtemp()) / "hosted-fixes-test.db"
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


def clear_monsters(srv, p):
    w = srv.world
    for a in list(p.map.actors.values()):
        if isinstance(a, Monster):
            p.map.remove(a.id)
            w.actors.pop(a.id, None)


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


def main() -> None:
    srv = make_service()
    w = srv.world

    print("\n[1] 托管选怪跳过召唤物（道士不打自己的宝宝）")
    p, sess = login(srv, "HF1", job=2)
    clear_monsters(srv, p)
    baby = spawn(srv, p, 1, 0, name="骷髅", master_id=p.id)
    real = spawn(srv, p, 5, 0, name="多钩猫")
    srv.autopilot.enable(p)
    pilot = srv.autopilot.get(p)
    cfg = PRESETS[pilot.preset]
    route = {"monsters": ()}
    tgt = pilot._choose_target(w, p, cfg, route, now_ms())
    check("_choose_target 选中真怪", tgt is real, f"target={tgt.name if tgt else None}")
    near = pilot._nearest_monster(w, p, cfg, now_ms())
    check("_nearest_monster 跳过宝宝", near is real)
    check("贴脸宝宝不触发威胁优先", not pilot._nearby_threat(w, p)
          or baby.target_id == p.id)

    print("\n[2] 捡拾落点被占 → 跳过不追（治来回掉头震荡）")
    p.gold = 100
    check("丢金币成功", w.drop_gold(p, 10))
    gis = [gi for gi in w.ground_items.values() if gi.map is p.map and gi.is_gold()]
    assert gis, "丢金币失败"
    gi_occ = gis[0]
    blocker = spawn(srv, p, gi_occ.x - p.x, gi_occ.y - p.y, name="多钩猫")
    blocker.x, blocker.y = gi_occ.x, gi_occ.y
    chosen = pilot._choose_loot(w, p, cfg, now_ms())
    check("被占格金币不被选中", chosen is None,
          f"gi=({gi_occ.x},{gi_occ.y}) blocker=({blocker.x},{blocker.y})")
    p.map.remove(blocker.id)
    w.actors.pop(blocker.id, None)
    chosen = pilot._choose_loot(w, p, cfg, now_ms())
    check("清场后同一件金币恢复可选", chosen is gi_occ)

    print("\n[3] 随机卷轴 → 完整 SpaceMove 两段特效")
    p2, sess2 = login(srv, "HF2", job=0)
    scroll_t = next((t for t in w.item_templates.values()
                     if t.stdMode == 3 and t.shape == 1), None)
    assert scroll_t is not None, "缺随机卷轴模板"
    p2.bag.append({"makeIndex": 900001, "index": scroll_t.index,
                   "durability": 1000, "durabilityMax": 1000, "lucky": 0})
    home = p2.map.name
    sess2.drain()
    srv._use_item(sess2, {"type": "useItem", "makeIndex": 900001})
    types = [m.get("type") for m in sess2.messages]
    check("spaceMoveHide 下发", "spaceMoveHide" in types, str(types[:6]))
    check("spaceMoveShow 下发", "spaceMoveShow" in types)
    check("changeMap 下发", "changeMap" in types)

    print("\n[4] 宝宝打怪 → 怪记仇转火宝宝并出手")
    p3, _ = login(srv, "HF3", job=2)
    clear_monsters(srv, p3)
    base = next(iter(w.monster_templates.values()))
    slave_tpl = replace(base, name="测试骷髅", race=80, appr=20, level=5,
                        hp=100, ac=0, mac=0, dc=10, maxDc=15, speed=0, hit=0,
                        walkSpeed=800, attackSpeed=1000)
    enemy_tpl = replace(base, name="测试食人怪", race=80, appr=20, level=5,
                        hp=100, ac=0, mac=0, dc=20, maxDc=30, speed=0, hit=0,
                        walkSpeed=800, attackSpeed=1000)
    w.monster_templates["测试骷髅"] = slave_tpl
    w.monster_templates["测试食人怪"] = enemy_tpl
    baby = spawn(srv, p3, 3, 0, name="测试骷髅", master_id=p3.id)
    enemy = spawn(srv, p3, 5, 0, name="测试食人怪")
    baby.x, baby.y = enemy.x + 1, enemy.y          # 宝宝贴脸怪
    # 宝宝出手一刀（走 _summon_ai 的攻击结算路径）
    baby.target_id = enemy.id
    w._summon_ai(baby, now_ms())
    check("怪被宝宝记仇（last_hiter/target）",
          enemy.target_id == baby.id and enemy.last_hiter_id == baby.id)
    check("_resolve_target 转火宝宝", w._resolve_target(enemy, now_ms()) is baby)
    hp0 = baby.hp
    for _ in range(25):                              # 有 miss 概率，多打几轮
        enemy.next_attack_at = 0
        w._monster_ai(enemy, now_ms())
        if baby.hp < hp0 or not baby.alive:
            break
    check("怪对宝宝出手扣血", baby.hp < hp0 or not baby.alive,
          f"hp {hp0} -> {baby.hp}")

    print(f"\n===== PASS {PASS} / FAIL {FAIL} =====")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
