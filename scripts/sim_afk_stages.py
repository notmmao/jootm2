#!/usr/bin/env python3
"""离线修炼四阶段横比（用户 2026-11-24）：20-25 / 25-30 / 30-35 / 35-40。

每个阶段把该等级段内所有可用猎场都跑一遍，回答三件事：
1. **这个阶段有没有合适的升级怪**（经验/分钟够不够，等级差不吃亏）
2. **刷新是否太密 / 太稀**（击杀速率 vs 重生速率，空转占时）
3. **怪物是否太强 / 掉落是否太低**（死亡率、药耗、金币/小时、装备磨损）

指标与 sim_afk_matrix 一致（经验/分、击杀/分、金币/时、磨损、密度、空转），
额外补两项本阶段关键：
* **补给占时%**：太靠前阶段如果补给占时 50%+，说明收益被走路吃掉
* **每杀金币**：掉落水平（原版 GetDropPosition + 掉率表），判断「掉落太低」

用法（务必 3.10）：
    D:/Python310/python.exe scripts/sim_afk_stages.py                # 全 4 阶段
    D:/Python310/python.exe scripts/sim_afk_stages.py --hours 1
    D:/Python310/python.exe scripts/sim_afk_stages.py --stage 20-25
    D:/Python310/python.exe scripts/sim_afk_stages.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time as _time
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pyserver  # noqa: E402,F401
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, now_ms  # noqa: E402
import sim_afk_matrix as MX  # noqa: E402
from sim_afk_matrix import (Clock, SimSession, dress_up, login,  # noqa: E402
                            patch_clock, pick_hunting_spot, prune_maps,
                            snapshot_durability)

# 每个阶段跑哪些图。依据是脚本 _tmp_inventory 统计出的「实际怪等级」，
# 不看配置里的推荐等级（配置与真实刷怪常常不一致，那本身就是要发现的问题）。
STAGES = {
    "20-25": {
        "level": 22,
        "maps": ["D402", "D405", "E603", "D002"],
    },
    "25-30": {
        "level": 27,
        "maps": ["D405", "E603", "D5063", "D515"],
    },
    "30-35": {
        "level": 32,
        "maps": ["D5063", "D505", "D515", "E603"],
    },
    "35-40": {
        "level": 37,
        "maps": ["D505", "D5063"],
    },
}


@dataclass
class Stage:
    key: str          # 阶段名 20-25
    level: int
    map_id: str
    job: int = 0
    preset: str = "balanced"

    @property
    def label(self) -> str:
        return f"{self.key}级 · {self.map_id}"


@dataclass
class Result:
    stage: str
    level: int
    map_id: str
    map_name: str = ""
    minutes: float = 0.0
    wall_sec: float = 0.0
    # 怪
    monsters0: int = 0
    cells: int = 0
    density_per_k: float = 0.0
    mon_levels: list = field(default_factory=list)
    mon_names: dict = field(default_factory=dict)
    mon_exp_avg: float = 0.0
    # 收益
    exp_gained: int = 0
    kills: int = 0
    gold_delta: int = 0
    level_start: int = 0
    level_end: int = 0
    lv_ups: int = 0
    # 磨损
    dura_before: dict = field(default_factory=dict)
    dura_after: dict = field(default_factory=dict)
    dura_broken: list = field(default_factory=list)
    dura_lost: list = field(default_factory=list)
    # 补给/风险
    supply_rounds: int = 0
    supply_min_pct: float = 0.0
    supply_fails: int = 0
    deaths: int = 0
    min_hp_pct: float = 100.0
    idle_min_pct: float = 0.0
    stop_reason: str = ""

    # ---- 派生指标 ----
    @property
    def exp_per_min(self) -> float:
        return self.exp_gained / self.minutes if self.minutes else 0.0

    @property
    def kills_per_min(self) -> float:
        return self.kills / self.minutes if self.minutes else 0.0

    @property
    def gold_per_hour(self) -> float:
        return self.gold_delta / self.minutes * 60 if self.minutes else 0.0

    @property
    def gold_per_kill(self) -> float:
        return self.gold_delta / self.kills if self.kills else 0.0

    @property
    def exp_per_kill(self) -> float:
        return self.exp_gained / self.kills if self.kills else 0.0


def run_stage(st: Stage, hours: float, tick_ms: int = 100, seed: int = 42) -> Result:
    import random as _random
    _random.seed(seed)
    cfg = load()
    cfg = replace(
        cfg,
        afk_min_level=1,
        afk_excluded_maps=(),
        afk_daily_minutes=max(120, int(hours * 60) + 60),
        afk_pool_cap_minutes=max(600, int(hours * 60) + 60),
        afk_newbie_minutes=max(600, int(hours * 60) + 60),
        afk_max_agents=5,
        afk_per_map_limit=5,
        afk_supply_enabled=1,
        # 修理**开启**：本阶段要测真实长跑表现，磨损不该被抹平，
        # 但装备该修就修——否则武器 2 小时归零，收益曲线失真。
        afk_supply_repair=1,
        afk_supply_unbind=1,
        afk_log_enabled=0,
    )
    db = Database(Path(tempfile.mkdtemp()) / f"stage-{st.key}-{st.map_id}.db")
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    w = srv.world

    clock = Clock(now_ms())
    patch_clock(clock)

    sess = SimSession()
    p = login(srv, sess, f"St{st.key.replace('-', '')}{st.map_id}", st.job)
    p.level = st.level
    p.recompute(w)
    p.gold = 30000
    p.hp, p.mp = p.max_hp, p.max_mp
    dress_up(srv, p, w, st.level)

    spot = pick_hunting_spot(w, st.map_id)
    if spot is None:
        db.close()
        r = Result(st.key, st.level, st.map_id)
        r.stop_reason = f"地图 {st.map_id} 没有刷怪点"
        return r
    if w.space_move(p, st.map_id, spot[0], spot[1]) is None:
        db.close()
        r = Result(st.key, st.level, st.map_id)
        r.stop_reason = f"无法放到 {st.map_id}{spot}"
        return r
    w.player_entered(p)
    p.hp, p.mp = p.max_hp, p.max_mp

    gm = p.map
    r = Result(st.key, st.level, st.map_id)
    r.map_name = getattr(w, "map_titles", {}).get(gm.name, "") or gm.name
    mons0 = [a for a in gm.actors.values() if isinstance(a, Monster)]
    r.monsters0 = len(mons0)
    r.cells = gm.width * gm.height
    r.density_per_k = r.monsters0 / r.cells * 1000 if r.cells else 0
    r.mon_levels = sorted({a.level for a in mons0})
    from collections import Counter
    r.mon_names = dict(Counter(a.name for a in mons0).most_common(6))
    r.mon_exp_avg = (sum(getattr(a, "exp", 0) for a in mons0) / len(mons0)
                     if mons0 else 0)
    r.level_start = st.level
    r.dura_before = {k: v for k, v in snapshot_durability(p).items() if v[0]}

    prune_maps(w, {st.map_id, cfg.afk_supply_town_map})

    ok, why, _ = srv.afk.request_start(p, int(hours * 60), st.preset)
    if not ok:
        r.stop_reason = f"开启被拒：{why}"
        db.close()
        return r
    srv.on_disconnect(sess)
    pilot = srv.afk._pilots.get(p.id)
    if pilot is None:
        r.stop_reason = "离线代理未接管"
        db.close()
        return r

    start_ms = clock.t
    total_ms = int(hours * 3600 * 1000)
    t_wall = _time.perf_counter()
    samples: list[dict] = []
    min_hp = p.max_hp
    last_min = -1
    stop_at = None
    stop_reason = ""

    while clock.t - start_ms < total_ms:
        clock.advance(tick_ms)
        try:
            srv.tick()
        except Exception as e:
            stop_at = (clock.t - start_ms) / 60_000
            stop_reason = f"tick 异常：{type(e).__name__}: {e}"
            break
        cur0 = w.actor(p.id)
        if cur0 is not None:
            min_hp = min(min_hp, cur0.hp)
        mi = int((clock.t - start_ms) // 60_000)
        if mi == last_min:
            continue
        last_min = mi
        live = srv.afk._pilots.get(p.id)
        cur = w.actor(p.id)
        if live is None or cur is None:
            stop_at = mi
            stop_reason = (pilot.reason if pilot else "") or "pilot 丢失"
            break
        samples.append({
            "m": mi,
            "x": cur.x, "y": cur.y, "map": cur.map.name if cur.map else "",
            "hp": cur.hp, "level": cur.level, "bag": len(cur.bag),
            "gold": cur.gold, "supply": live.supply_state,
            "status": live.status, "kills": live.kills,
            "deaths": live.deaths,
        })

    r.wall_sec = _time.perf_counter() - t_wall
    r.minutes = (clock.t - start_ms) / 60_000
    r.dura_after = {k: v for k, v in snapshot_durability(p).items() if v[0]}
    r.level_end = p.level
    r.lv_ups = max(0, p.level - r.level_start)
    r.kills = pilot.kills if pilot else 0
    r.deaths = pilot.deaths if pilot else 0
    r.supply_fails = pilot.supply_fails if pilot else 0
    r.exp_gained = srv.afk._exp_gained(w, pilot, p) if pilot else 0
    r.gold_delta = p.gold - pilot.gold_start if pilot else 0
    if pilot and pilot.log_supplies:
        r.supply_rounds = pilot.log_supplies
    for slot, (before, _mx, _nm) in r.dura_before.items():
        after = r.dura_after.get(slot)
        if after is None or after[0] == 0:
            nm = MX.SLOT_NAMES.get(slot, str(slot))
            if r.deaths > 0:
                r.dura_lost.append(nm)
            else:
                r.dura_broken.append(nm)
    if samples:
        num = len(samples)
        r.supply_min_pct = sum(1 for s in samples if s["supply"]) / num * 100
        r.idle_min_pct = sum(1 for s in samples
                             if "没有可打的怪" in s["status"]) / num * 100
        r.min_hp_pct = min_hp * 100 / max(1, p.max_hp)
    r.stop_reason = stop_reason if stop_at is not None else ""

    if p.id in srv.afk._pilots:
        srv.afk.disable(p, "模拟结束")
    db.close()
    return r


def print_row(r: Result) -> None:
    if r.stop_reason and not r.minutes:
        print(f"  [{r.stage}] {r.map_id:<6} ★ 未能开跑：{r.stop_reason}")
        return
    worn = sum(1 for s in r.dura_before
               if r.dura_after.get(s) is None or r.dura_after[s][0] == 0)
    print(f"  {r.map_id:<6} {r.map_name:<10} "
          f"怪{r.monsters0:>4}(均Lv{sum(r.mon_levels) / max(1, len(r.mon_levels)):>4.0f})"
          f" 密度{r.density_per_k:>5.1f} "
          f"| 经验/分 {r.exp_per_min:>6.0f} 击杀/分 {r.kills_per_min:>4.1f} "
          f"金币/时 {r.gold_per_hour:>6.0f} 每杀 {r.gold_per_kill:>5.0f}金"
          f"| 死{r.deaths} 最低HP{r.min_hp_pct:>4.0f}% "
          f"补给{r.supply_min_pct:>4.0f}% 空转{r.idle_min_pct:>3.0f}% "
          f"损{worn}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--stage", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--tick", type=int, default=100)
    args = ap.parse_args()

    keys = [args.stage] if args.stage else list(STAGES)
    results: list[Result] = []
    for key in keys:
        cfg_stage = STAGES[key]
        print(f"\n{'=' * 100}")
        print(f"【阶段 {key}】基准等级 {cfg_stage['level']} · "
              f"{len(cfg_stage['maps'])} 张图 × {args.hours:g} 小时")
        print(f"{'=' * 100}")
        print(f"  {'图':<6} {'名称':<10} {'怪/密度':<14}"
              f"{'收益':<44}{'风险/损耗'}")
        for mid in cfg_stage["maps"]:
            st = Stage(key, cfg_stage["level"], mid)
            r = run_stage(st, args.hours, args.tick)
            results.append(r)
            print_row(r)
        # 阶段小结
        valid = [r for r in results if r.stage == key and r.minutes > 0]
        if len(valid) > 1:
            best = max(valid, key=lambda x: x.exp_per_min)
            print(f"  → 本阶段最佳：{best.map_id} {best.map_name}"
                  f"（{best.exp_per_min:.0f} 经验/分，死 {best.deaths} 次，"
                  f"最低HP {best.min_hp_pct:.0f}%）")

    if args.json:
        out = []
        for r in results:
            d = {k: v for k, v in r.__dict__.items()}
            d.update(exp_per_min=round(r.exp_per_min, 1),
                     kills_per_min=round(r.kills_per_min, 2),
                     gold_per_hour=round(r.gold_per_hour, 1),
                     gold_per_kill=round(r.gold_per_kill, 1),
                     exp_per_kill=round(r.exp_per_kill, 1))
            out.append(d)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n已写出 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
