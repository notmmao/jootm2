#!/usr/bin/env python3
"""三职业「官服口径」技能数值回归（离线，不需要服务端/浏览器）。

数据来源（逐帧核对画面数值 + 火炬源码行号对照）：
- 法师：B 站 BV1nQrkYYEM7《全面解析法师经典版本全技能》小道士的传奇，
  docs/GAME_DATA_REFERENCE/BILIBILI-BV1nQrkYYEM7-mage-classic-official.md
- 战士：B 站 BV1LwrCYdEFJ《热血传奇经典版本战士全技能解析》小道士的传奇，
  docs/GAME_DATA_REFERENCE/BILIBILI-BV1LwrCYdEFJ-warrior-classic-official.md
- 原版公式：火炬 `Source/M2Server/ObjBase.pas` / `Magic.pas`（权威蓝本）。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_skill_official_values.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver import gamedata  # noqa: E402
from pyserver import handlers_combat as hc  # noqa: E402
from pyserver.handlers_combat import (  # noqa: E402
    CombatHandlers, delphi_round, sword_second_dam,
)
from pyserver.world import Monster, Player, mag_struck_damage  # noqa: E402

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


def test_magic_csv_spread() -> None:
    """满级加成浮动：DefPower/DefMaxPower 决定，Power 不计入随机段。"""
    print("\n[1] 满级加成浮动（BV1nQrkYYEM7，官服画面值）")
    path = ROOT / "gamedata/mud2/DB/Magic.csv"
    rows = {int(r["MagId"]): r for r in csv.DictReader(gamedata.read_lines(path))}
    # GetPower(lv=3) = Power + DefPower + Random(DefMaxPower-DefPower)
    want = {1: (9, 11), 22: (6, 7), 23: (14, 15), 33: (26, 28)}
    for mid, (lo, hi) in want.items():
        r = rows[mid]
        power = int(r["Power"])
        dlo = power + int(r["DefPower"])
        dhi = power + int(r["DefMaxPower"]) - 1
        check(f"{r['MagName']} 满级加成 {lo}-{hi}", (dlo, dhi) == (lo, hi),
              f"got={dlo}-{dhi}")


def test_shield() -> None:
    """魔法盾 15/30/45/60%（官服）；火炬私服口径 16/24/32/40 已弃用。"""
    print("\n[2] 魔法盾官服减免（BV1nQrkYYEM7）")
    now = 1_000_000
    # 盾是「伤害折成百分比」而非「减免百分比」：20 点 → 3/6/9/12。
    for lv, expect in enumerate((3, 6, 9, 12)):
        p = Player(id=1, level=30)
        p.combat["macMin"] = p.combat["macMax"] = 0
        p.shield_level = lv
        p.shield_until = now + 10000
        got = mag_struck_damage(p, 20, now)
        check(f"{lv} 级盾 20 点魔法伤 → 剩 {expect}", got == expect,
              f"got={got}")


def test_delayed_magic() -> None:
    """RM_DELAYMAGIC：先探伤门槛，再对怪物把原始 pwr ×1.2（不是 ÷1.2）。"""
    print("\n[3] 对怪倍率方向/顺序（ObjBase.pas:7393-7410）")
    orig = hc.mag_struck_damage
    calls: list[int] = []
    h = CombatHandlers()
    h._broadcast_magic_hit = lambda *a: None       # type: ignore[method-assign]
    h._kill_if_dead = lambda *a: None              # type: ignore[method-assign]
    hc.mag_struck_damage = lambda target, dmg, now=None: calls.append(dmg) or 100
    try:
        target = SimpleNamespace(alive=True, race=50, hp=1000, max_hp=1000, id=2)
        dmg = h._delayed_magic(SimpleNamespace(), target, 100, 1, 0)
        check("怪物走两次 GetMagStruckDamage：100 / 120", calls == [100, 120],
              f"calls={calls}")
        check("最终伤害 100（不是 83）", dmg == 100, f"got={dmg}")
        calls.clear()
        target.race = 0
        h._delayed_magic(SimpleNamespace(), target, 100, 1, 0)
        check("玩家目标不乘 1.2", calls == [100, 100], f"calls={calls}")
    finally:
        hc.mag_struck_damage = orig


def test_pass_through() -> None:
    """疾光电影每个目标固定 ×1.5，不随目标序号累乘（ObjBase.pas:7126）。"""
    print("\n[4] 疾光电影固定 1.5 倍（BV1nQrkYYEM7）")
    h = CombatHandlers()
    targets = {}
    for i in range(1, 9):
        m = Monster(id=100 + i, x=0, y=-i, level=1)
        targets[(0, -i)] = m
    h._find_target_at = lambda game_map, x, y: targets.get((x, y))  # type: ignore
    seen: list[int] = []
    h._direct_magic = lambda p, a, pwr, mid, now: seen.append(pwr) or 1  # type: ignore
    orig = hc.magic_hits
    hc.magic_hits = lambda a: True
    try:
        p = SimpleNamespace(x=0, y=0, map=SimpleNamespace(inside=lambda x, y: True))
        h._pass_through_magic(p, 0, 8, 100, True, 10, 0)
        expected = delphi_round(100 * 1.5)
        check("8 个目标全部 150 点", len(seen) == 8 and set(seen) == {expected},
              f"seen={seen}")
    finally:
        hc.magic_hits = orig


def test_sword_values() -> None:
    print("\n[5] 战士刺杀/半月/烈火（BV1LwrCYdEFJ）")
    p = Player(id=1, level=40)
    for lv in range(4):
        p.skills[25] = {"level": lv}
        val = sword_second_dam(p, 40, 25, 13)
        check(f"半月 {lv} 级剑气 40 攻击 → {(6, 9, 12, 15)[lv]}",
              val == (6, 9, 12, 15)[lv], f"got={val}")
    for lv in range(4):
        p.skills[12] = {"level": lv}
        val = sword_second_dam(p, 37, 12, 5)
        check(f"刺杀 {lv} 级破防 37 攻击 → {(15, 22, 30, 37)[lv]}",
              val == (15, 22, 30, 37)[lv], f"got={val}")

    h = CombatHandlers()
    h.world = SimpleNamespace(send_system=lambda *a: None)
    for lv, want in enumerate((74, 95, 117, 138)):
        fp = SimpleNamespace(fire_hit_on=True, skills={26: {"level": lv}})
        _, bonus = h._sword_flag(fp, 53, False)
        check(f"烈火 {lv} 级 53 攻击 → {want}", 53 + bonus == want,
              f"got={53 + bonus}")


def test_summon_ids() -> None:
    print("\n[6] 诱惑之光多宝宝 2/3/4/5 只（BV1nQrkYYEM7）")
    p = Player(id=1)
    check("初始无宝宝 → summon_id=-1", p.summon_id == -1)
    for sid in (11, 12, 13, 14, 15):
        p.summon_id = sid
    check("summon_id 兼容视图返回第一只", p.summon_id == 11, f"got={p.summon_id}")
    check("summon_ids 保留全部 5 只", p.summon_ids == [11, 12, 13, 14, 15],
          f"got={p.summon_ids}")
    p.summon_id = -1
    check("赋 -1 清空全部", p.summon_ids == [] and p.summon_id == -1)


def test_rush_steps() -> None:
    print("\n[7] 野蛮冲撞步数 3/3/4/5（BV1LwrCYdEFJ）")

    class FakeWorld:
        def __init__(self):
            self.moves = 0

        def try_move(self, a, d):
            self.moves += 1
            return True

        def broadcast_rush(self, p, n):
            pass

        def send_system(self, p, text):
            pass

    h = CombatHandlers()
    h._find_target_at = lambda game_map, x, y: None  # type: ignore
    for lv, want in enumerate((3, 3, 4, 5)):
        w = FakeWorld()
        h.world = w
        p = Player(id=1, level=20, x=0, y=0, dir=0)
        h._rush_rush(p, 0, lv)
        check(f"野蛮 {lv} 级走 {want} 格", w.moves == want, f"got={w.moves}")


def main() -> None:
    test_magic_csv_spread()
    test_shield()
    test_delayed_magic()
    test_pass_through()
    test_sword_values()
    test_summon_ids()
    test_rush_steps()
    print("\n" + "=" * 56)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
