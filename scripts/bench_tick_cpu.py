#!/usr/bin/env python3
"""tick 计算成本基准（CPU 热点优化的收益度量）。

背景：线上 py-spy 火焰图显示忙时 CPU 大头 = AutoPilot 寻路 PathPlanner（~15%）、
Monster AI 索敌（~4%）、goods()/item_template_by_name（~1.5%）。本脚本把这三块
的载体场景固化下来，量 wall-clock 时间，改动前后各跑一次对比。

场景（贴近线上 E603 挂机实况）：
  * 3 个在线托管玩家（autopilot balanced 档）放在 E603 满怪图追怪；
  * 1 个离线挂机代理（afk）同样在 E603；
  * 1 个离线挂机代理在盟重省 0（走补给/商人路径，触发 goods）；
  * 其余地图的怪全部剪掉（与线上一致：只有活跃图跑 AI）。

度量：纯虚拟时钟推进（不 sleep），N 个 tick 的墙钟耗时；分项计时
world.tick / autopilot.tick / afk.tick。同种子可复现。

用法：
    D:/Python310/python.exe scripts/bench_tick_cpu.py            # 默认 3 轮×2000 tick
    D:/Python310/python.exe scripts/bench_tick_cpu.py --ticks 4000 --rounds 5
"""
from __future__ import annotations

import argparse
import json
import random as _random
import statistics
import sys
import tempfile
import time as _time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, Player, now_ms  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from sim_afk_matrix import (Clock, SimSession, dress_up, login,  # noqa: E402
                            pick_hunting_spot, prune_maps)


class SimSessionWS(SimSession):
    """托管要求 session 非空；send_json 直接丢弃（不攒消息，省内存）。"""
    def send_json(self, obj: dict) -> None:
        pass


def build(hunting_map: str, supply_town: str, seed: int):
    _random.seed(seed)
    cfg = load()
    cfg = replace(
        cfg,
        afk_min_level=1,
        afk_excluded_maps=(),
        afk_daily_minutes=10_000,
        afk_pool_cap_minutes=10_000,
        afk_newbie_minutes=10_000,
        afk_max_agents=5,
        afk_per_map_limit=5,
        afk_supply_enabled=1,
        afk_supply_repair=1,
        afk_log_enabled=0,
    )
    db = Database(Path(tempfile.mkdtemp()) / "bench.db")
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    w = srv.world

    clock = Clock(now_ms())
    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith("pyserver") \
                and getattr(mod, "now_ms", None) is not None:
            setattr(mod, "now_ms", clock.now_ms)

    # ---- 3 个在线托管玩家 ----
    online = []
    spot = pick_hunting_spot(w, hunting_map)
    assert spot, f"{hunting_map} 没有刷怪点"
    for i in range(3):
        sess = SimSessionWS()
        p = login(srv, sess, f"Bench{i}", job=i % 3)
        p.level = 25
        p.recompute(w)
        p.gold = 50000
        p.hp, p.mp = p.max_hp, p.max_mp
        dress_up(srv, p, w, 25)
        assert w.space_move(p, hunting_map, spot[0] + i * 2, spot[1]) is not None
        w.player_entered(p)
        p.hp, p.mp = p.max_hp, p.max_mp
        ok, why = srv.autopilot.enable(p, "balanced")
        assert ok, f"托管开启失败: {why}"
        online.append(p)

    # ---- 2 个离线挂机代理（一怪图一城镇）----
    for name, mid in (("AfkHunt", hunting_map), ("AfkTown", supply_town)):
        sess = SimSessionWS()
        p = login(srv, sess, name, job=0)
        p.level = 25
        p.recompute(w)
        p.gold = 50000
        p.hp, p.mp = p.max_hp, p.max_mp
        dress_up(srv, p, w, 25)
        sp = pick_hunting_spot(w, mid) or (100, 100)
        assert w.space_move(p, mid, sp[0], sp[1]) is not None
        w.player_entered(p)
        p.hp, p.mp = p.max_hp, p.max_mp
        ok, why, _ = srv.afk.request_start(p, 480, "balanced")
        assert ok, f"{name} 挂机开启失败: {why}"
        srv.on_disconnect(sess)

    prune_maps(w, {hunting_map, supply_town})
    mons = sum(1 for a in w.actors.values() if isinstance(a, Monster))
    return srv, w, clock, mons


def bench(srv, clock, ticks: int) -> dict:
    step = 100
    t_w = t_a = t_f = 0.0
    t0 = _time.perf_counter()
    for _ in range(ticks):
        clock.advance(step)
        now = clock.t
        s = _time.perf_counter()
        srv.world.tick(now)
        e = _time.perf_counter(); t_w += e - s
        srv.autopilot.tick(now)
        e2 = _time.perf_counter(); t_a += e2 - e
        srv.afk.tick(now)
        e3 = _time.perf_counter(); t_f += e3 - e2
    total = _time.perf_counter() - t0
    return {"total_ms": total * 1000, "world_ms": t_w * 1000,
            "autopilot_ms": t_a * 1000, "afk_ms": t_f * 1000}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=3000, help="计时块 tick 数")
    ap.add_argument("--warmup", type=int, default=1500, help="热身 tick 数（不计时）")
    ap.add_argument("--reps", type=int, default=1,
                    help="独立 build 重复次数（同种子→同状态序列，取最小更稳）")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    results = []
    for rep in range(args.reps):
        srv, w, clock, mons = build("E603", "0", args.seed)
        bench(srv, clock, args.warmup)          # 热身：刷怪满、代理进入常态
        pilots = list(srv.afk._pilots.values())
        def _nm(pl):
            pa = w.actor(pl.player_id)
            return pa.name if pa else "?"
        stat = " | ".join(f"{_nm(pl)}:{pl.status}" for pl in pilots) or "无代理"
        live = sum(1 for a in w.actors.values() if isinstance(a, Monster))
        m = bench(srv, clock, args.ticks)
        m["live_monsters"] = live
        results.append(m)
        print(f"rep{rep}: total={m['total_ms']:.1f}ms "
              f"world={m['world_ms']:.1f} autopilot={m['autopilot_ms']:.1f} "
              f"afk={m['afk_ms']:.1f} 怪={live}")
        print(f"     {stat}")
    best = min(results, key=lambda m: m["total_ms"])
    print(json.dumps({
        "ticks": args.ticks, "seed": args.seed,
        "best_total_ms": round(best["total_ms"], 1),
        "world_ms": round(best["world_ms"], 1),
        "autopilot_ms": round(best["autopilot_ms"], 1),
        "afk_ms": round(best["afk_ms"], 1),
        "tick_us": round(best["total_ms"] * 1000 / args.ticks, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
