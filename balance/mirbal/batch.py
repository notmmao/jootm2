"""引擎 A 批量调度（multiprocessing，标准库）+ 结果缓存（按数据指纹）。"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path

from . import boot

LEVELS = (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 38, 41, 44, 47, 50, 55, 60)
JOB_IDS = (0, 1, 2)
TIERS = ("none", "shop", "best")
LEVEL_WINDOW = (-14, 12)


def fingerprint(tree: str | None) -> str:
    """数据树 + 倍率表 + pyserver 代码 的内容指纹：任何一处变化缓存就失效。"""
    root = boot.tree_path(tree) or (boot.ROOT / "gamedata")
    h = hashlib.sha1()
    paths = [root / "mud2" / "DB"]
    env = root / "Mir200" / "Envir"
    paths += [env / "MonGen.txt", env / "MonItems", env / "Market_Def", env / "MapInfo.txt"]
    paths += [boot.rates_file(), boot.ROOT / "pyserver", boot.ROOT / "config" / "server.properties",
              *(boot.BAL / "mirbal" / f for f in ("arena.py", "gear.py", "worldsim.py", "boot.py"))]
    for p in paths:
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else []
        for f in files:
            if f.suffix in (".pyc",) or "__pycache__" in f.parts or "out" in f.parts:
                continue
            h.update(str(f.relative_to(boot.ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def plan(ds, levels=LEVELS, jobs=JOB_IDS, tiers=TIERS):
    farm = ds.farmable()
    tasks = []
    for job in jobs:
        for lv in levels:
            mons = [m.name for m in farm if lv + LEVEL_WINDOW[0] <= m.level <= lv + LEVEL_WINDOW[1]]
            for tier in tiers:
                tasks.append((job, lv, tier, mons))
    return tasks


def _work(args):
    tree, job, lv, tier, mons, fights = args
    from . import arena
    return arena.run_combo(tree, job, lv, tier, mons, fights=fights)


def run_arena(tree, ds, fights=8, procs=None, levels=LEVELS, log=print, budget_s=None):
    """逐组合缓存（可断点续跑）；budget_s 到点即停，返回 None 表示尚未跑完。"""
    import time
    fp = fingerprint(tree)
    cdir = boot.BAL / "out" / "cache" / f"arena-{tree or 'main'}-{fp}-f{fights}"
    cdir.mkdir(parents=True, exist_ok=True)
    tasks = []
    for j, lv, t, mons in plan(ds, levels):
        f = cdir / f"{j}-{lv}-{t}.json"
        if not f.is_file():
            tasks.append((tree, j, lv, t, mons, fights))
    total = len(plan(ds, levels))
    if tasks:
        procs = procs or max(1, (os.cpu_count() or 2))
        log(f"[arena] 待跑 {len(tasks)}/{total} 组合 × ≤{fights} 场，{procs} 进程 …")
        t0 = time.time()
        pool = mp.get_context("fork").Pool(procs)
        try:
            for i, r in enumerate(pool.imap_unordered(_work, tasks, chunksize=1)):
                (cdir / f"{r['job']}-{r['level']}-{r['tier']}.json").write_text(
                    json.dumps(r, ensure_ascii=False), encoding="utf-8")
                if (i + 1) % 15 == 0:
                    log(f"[arena] {i + 1}/{len(tasks)}")
                if budget_s and time.time() - t0 > budget_s:
                    log(f"[arena] 时间预算到，已完成 {i + 1}，下次续跑")
                    pool.terminate()
                    return None
        finally:
            pool.terminate()
    out = []
    for j, lv, t, _m in plan(ds, levels):
        f = cdir / f"{j}-{lv}-{t}.json"
        if f.is_file():
            out.append(json.loads(f.read_text(encoding="utf-8")))
    log(f"[arena] 完成 {len(out)}/{total}（缓存 {cdir.name}）")
    return out
