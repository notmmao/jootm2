#!/usr/bin/env python3
"""精英怪机制回归（Python 3.10）。

覆盖：概率掷骰、HP/DC/经验倍率、仅天然刷怪参与、brief_json 下发 elite、
前端名字/绿色由 test_name_style.mjs 覆盖。
    D:/Python310/python.exe scripts/test_elite_monster.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataclasses import replace  # noqa: E402
from pyserver.config import load as load_config  # noqa: E402
from pyserver.templates import MonsterTemplate  # noqa: E402
from pyserver import world as world_mod  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    if ok:
        P += 1
    else:
        F += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def make_fake(config):
    """最小 world 桩：只实现 _spawn_monster 依赖的方法。"""
    fw = SimpleNamespace(config=config, _next_id=1000, added=[])
    fw.new_id = lambda: (setattr(fw, "_next_id", fw._next_id + 1) or fw._next_id)
    fw.add_actor = lambda m, gm=None: fw.added.append(m)
    fw._init_race_traits = world_mod.GameWorld._init_race_traits
    return fw


def spawn(config, template, allow_elite=True):
    fw = make_fake(config)
    m = world_mod.GameWorld._spawn_monster(fw, None, template, 10, 10,
                                           0, allow_elite)
    return m


cfg = load_config(ROOT)
check("默认概率 5%", abs(cfg.elite_rate - 0.05) < 1e-9, str(cfg.elite_rate))
check("默认 HP×2", cfg.elite_hp_mult == 2.0)
check("默认 DC×1.5", cfg.elite_dc_mult == 1.5)
check("默认经验×3", cfg.elite_exp_mult == 3.0)

t = MonsterTemplate(name="稻草人", race=12, raceImg=18, appr=18, level=7,
                    exp=20, hp=50, mp=0, dc=2, maxDc=5)

print("\n[1] 必然精英（rate=1）")
cfg_always = replace(cfg, elite_rate=1.0)
m = spawn(cfg_always, t)
check("elite 标记", m.elite is True)
check("HP×2", m.max_hp == 100 and m.hp == 100, f"{m.hp}/{m.max_hp}")
check("经验×3", m.exp == 60, str(m.exp))
check("DC 加成 = round(5*0.5)=2", m.slave_dc_bonus == 2, str(m.slave_dc_bonus))
check("brief_json 下发 elite", m.brief_json().get("elite") is True)

print("\n[2] 必然非精英（rate=0）")
cfg_none = replace(cfg, elite_rate=0.0)
m = spawn(cfg_none, t)
check("非 elite", m.elite is False)
check("HP 原值", m.max_hp == 50, str(m.max_hp))
check("经验原值", m.exp == 20, str(m.exp))
check("DC 无加成", m.slave_dc_bonus == 0, str(m.slave_dc_bonus))
check("brief_json elite=false", m.brief_json().get("elite") is False)

print("\n[3] allow_elite=False 不参与（宝宝等）")
m = spawn(cfg_always, t, allow_elite=False)
check("禁用时非精英", m.elite is False)

print("\n[4] 摆设怪（经验/血为0）不参与")
t_dummy = MonsterTemplate(name="练功师", hp=100, exp=0)
m = spawn(cfg_always, t_dummy)
check("经验0不变精英", m.elite is False)
t_dummy2 = MonsterTemplate(name="木桩", hp=0, exp=10)
m = spawn(cfg_always, t_dummy2)
check("血0不变精英", m.elite is False)

print("\n[5] 概率分布（rate=0.5，大样本）")
import random  # noqa: E402
cfg_half = replace(cfg, elite_rate=0.5)
random.seed(42)
n_elite = sum(1 for _ in range(200) if spawn(cfg_half, t).elite)
check("约一半为精英（80~120）", 80 <= n_elite <= 120, str(n_elite))

print("\n[6] 精英爆率（elite.dropMult 判时乘，默认×5）")
check("默认爆率×5", cfg.elite_drop_mult == 5.0)
from pyserver.world_extra import WorldExtra  # noqa: E402
from pyserver.drop_rates import DropRates  # noqa: E402
from pyserver.templates import ItemTemplate  # noqa: E402


class _Entry:
    def __init__(self, name, num, den):
        self.item_name, self.sel_point, self.max_point, self.count = name, num - 1, den, 1


def drop_count(config, elite, n=4000, seed=7):
    """用 drop_loot 真实链路统计 1/10 掉落命中次数。"""
    random.seed(seed)
    it = ItemTemplate(index=1, name="金创药", looks=1, stdMode=0)
    made = []
    fw = SimpleNamespace(
        config=config, drop_rates=DropRates(),
        drop_tables={"稻草人": [_Entry("金创药", 1, 10)]},
        scatter_pos=lambda gm, x, y: (x, y),
        add_ground_item=lambda gi: made.append(gi),
        item_template_by_name=lambda nm: it,
        new_item_instance=lambda tpl, dura: {"index": tpl.index},
        new_id=lambda: len(made) + 1,
    )
    m = SimpleNamespace(template=SimpleNamespace(name="稻草人"), elite=elite,
                        map="0", x=5, y=5)
    hits = []
    for _ in range(n):
        made.clear()
        WorldExtra.drop_loot(fw, m, killer=None)
        hits.append(len(made))
    return sum(hits)


base = drop_count(cfg, elite=False)
el = drop_count(cfg, elite=True)
# 期望 400 对 2000（1/10 → ×5 = 1/2 × 4000），容差按二项 ±4σ 叠加 base 波动
check(f"精英爆率约为基础×5（{base} vs {el}）",
      base > 0 and abs(el - 5 * base) < 400, f"base={base} elite={el}")
boosted = drop_count(replace(cfg, elite_drop_mult=1.0), elite=True)
check("dropMult=1 时精英与普通一致", abs(boosted - base) < 80, f"{boosted} vs {base}")

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
