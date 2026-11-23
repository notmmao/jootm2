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

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
