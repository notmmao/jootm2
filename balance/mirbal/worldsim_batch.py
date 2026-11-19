"""引擎 B 场景调度：按等级挑「单挑最优怪」所在的最密刷怪图，跑 1/5/15/40 人同图。"""
from __future__ import annotations

import json
import multiprocessing as mp

from pyserver.util import calc_get_exp

from . import boot
from .batch import fingerprint

LEVELS = (7, 13, 19, 25, 31)
NS = (1, 5, 15, 40)


def pick_maps(ds, arena, levels):
    plans = []
    for L in levels:
        best = None
        for c in arena:
            if c["tier"] != "shop" or abs(c["level"] - L) > 2:
                continue
            for f in c["fights"]:
                m = ds.monsters.get(f["monster"])
                if not f["ttk"] or m is None or f["deaths"] > 0:
                    continue
                v = calc_get_exp(L, m.level, m.exp) / f["ttk"]
                if best is None or v > best[0]:
                    best = (v, m)
        if best is None:
            continue
        m = best[1]
        by_map = {}
        for s in m.spawns:
            by_map[s.map_id] = by_map.get(s.map_id, 0) + s.count
        map_id = max(by_map, key=by_map.get)
        plans.append(dict(level=L, map_id=map_id, monster=m.name))
    return plans


def _work(args):
    tree, map_id, level, n, minutes, monster = args
    from . import worldsim
    try:
        r = worldsim.run(tree, map_id, level, n, minutes=minutes, monsters=[monster])
        r["target"] = monster
        return r
    except Exception as e:      # 单场失败不影响整批
        return dict(error=f"{type(e).__name__}: {e}", map=map_id, level=level, n=n)


def run_all(tree, ds, arena, quick=False, log=print, budget_s=None, cached_only=False):
    import time
    fp = fingerprint(tree)
    cdir = boot.BAL / "out" / "cache" / f"world-{tree or 'main'}-{fp}{'-q' if quick else ''}"
    cdir.mkdir(parents=True, exist_ok=True)
    levels = (13,) if quick else LEVELS
    ns = (1, 5) if quick else NS
    minutes = 3 if quick else 10
    plans = pick_maps(ds, arena, levels)
    allt = [(tree, p["map_id"], p["level"], n, minutes, p["monster"]) for p in plans for n in ns]
    key = lambda t: cdir / f"{t[2]}-{t[3]}.json"
    tasks = [t for t in allt if not key(t).is_file()]
    if tasks and cached_only:
        log(f"[world] 只用已有缓存（缺 {len(tasks)} 场）")
    elif tasks:
        log(f"[world] 待跑 {len(tasks)}/{len(allt)} 场景（每场 {minutes} 游戏分钟）…")
        t0 = time.time()
        pool = mp.get_context("fork").Pool(2)
        try:
            for i, r in enumerate(pool.imap_unordered(_work, tasks)):
                key((None, None, r["level"], r["n"])).write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
                log(f"[world] {i + 1}/{len(tasks)} {r.get('map_title', r.get('map'))} Lv{r['level']} N={r['n']}"
                    + (f" 错误 {r['error']}" if "error" in r else f" 用时 {r['real_s']}s"))
                if budget_s and time.time() - t0 > budget_s:
                    log("[world] 时间预算到，下次续跑")
                    return None
        finally:
            pool.terminate()
    out = [json.loads(key(t).read_text(encoding="utf-8")) for t in allt if key(t).is_file()]
    return [r for r in out if "error" not in r]
