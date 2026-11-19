#!/usr/bin/env python3
"""食人花(_stick_ai)攻击道士宝宝不抛异常回归（离线，虚拟时钟）。

对齐原版依据（火炬 ObjMon2.pas:154-297 / TStickMonster）：
- 食人花 race=85 走 _stick_ai，只打相邻格目标，够不着也不会走；
- 目标是道士宝宝（Monster, master_id>=0）时，走通用「怪打怪」结算
  （与 _monster_ai 近战 / _tick_pending_ranged_hits 远程同款）：
  扣 hp + _send_hits，hp<=0 走 kill_monster，不读 Player 专属字段。

历史 bug（GM-ANALYSIS §0.1）：_stick_ai 命中分支直接调 _apply_hit_to_player，
后者读 target.mana_to_health（仅 Player 有）→ AttributeError，
让这一轮 tick 后面排队的逻辑全被跳过。模拟中触发 158 次。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_stick_monster_pet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "balance"))

from mirbal import boot, clock  # noqa: E402  (含 clock.install)

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


def main() -> None:
    cfg, srv = boot.make_service(None)
    w = srv.world

    stick_t = next((t for t in w.monster_templates.values() if t.race == 85), None)
    assert stick_t is not None, "数据里没有食人花模板（race 85）"
    # 随便找一只正常怪当宝宝模板（非食人花、有 hp）
    pet_t = next(t for t in w.monster_templates.values()
                 if t.race != 85 and getattr(t, "hp", 0) > 0)

    p = boot.new_player(srv, 2)          # 道士宿主
    gm = p.map
    # 清空地图上的怪，隔离场景
    for a in list(w.actors.values()):
        if isinstance(a, Monster):
            a.map.remove(a.id)
            w.actors.pop(a.id, None)

    def place(t, dx, dy, master=-1):
        m = Monster(id=w.new_id(), name=t.name, x=p.x + dx, y=p.y + dy, dir=4,
                    hp=max(1, t.hp), max_hp=max(1, t.hp),
                    mp=getattr(t, "mp", 0), max_mp=getattr(t, "mp", 0),
                    level=t.level, race=t.race, race_img=getattr(t, "raceImg", 0),
                    appr=getattr(t, "appr", 0), template=t,
                    spawn_x=p.x + dx, spawn_y=p.y + dy, spawn_map=gm,
                    master_id=master, respawn_interval_ms=10 ** 12)
        w.add_actor(m, gm)
        return m

    print("\n[1] 食人花 vs 道士宝宝（相邻格）：tick 不抛异常、宝宝掉血")
    flower = place(stick_t, 3, 0)
    pet = place(pet_t, 4, 0, master=p.id)   # 与食人花相邻
    flower.hidden = False
    flower.target_id = pet.id
    flower.next_attack_at = 0
    hp0 = pet.hp
    errs = []
    for _ in range(30):
        now = clock.advance_ms(100)
        try:
            w.tick(now)
        except Exception as e:            # 回归点：这里曾经抛 AttributeError
            errs.append(f"{type(e).__name__}: {e}")
            break
    check("tick 无异常", not errs, "; ".join(errs))
    check("宝宝被扣血或已死亡", pet.hp < hp0 or not pet.alive,
          f"hp {hp0}->{pet.hp} alive={pet.alive}")

    print("\n[2] 食人花 vs 玩家：仍走魔法盾/护身减伤链路（Player 分支未退化）")
    for a in list(w.actors.values()):
        if isinstance(a, Monster):
            a.map.remove(a.id)
            w.actors.pop(a.id, None)
    p.hp = p.max_hp
    p.alive, p.dead = True, False
    flower2 = place(stick_t, 3, 0)
    flower2.hidden = False
    flower2.target_id = p.id
    flower2.next_attack_at = 0
    errs2 = []
    for _ in range(15):
        now = clock.advance_ms(100)
        try:
            w.tick(now)
        except Exception as e:
            errs2.append(f"{type(e).__name__}: {e}")
            break
    check("打玩家 tick 无异常", not errs2, "; ".join(errs2))
    check("玩家受到伤害", p.hp <= p.max_hp)

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
