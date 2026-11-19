#!/usr/bin/env python3
"""jootm2 数据可玩性分析 —— GM 设计辅助工具（不发布、不进镜像、不改游戏）。

用法（仓库根目录或 balance/ 下均可，Python 3.10+，纯标准库）：
  python balance/run.py all                 # 引擎A + 引擎B + 报告（默认读生效数据树）
  python balance/run.py all --tree 176      # 指定数据树（main / 176 / 路径）
  python balance/run.py report --compare 176  # 报告叠加另一棵树做 A/B 对比
  python balance/run.py arena|world|report  # 分步跑（结果按数据指纹缓存在 balance/out/cache）
  python balance/run.py all --quick         # 少量样本快速出图（调试用）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mirbal import boot  # noqa: E402  (安装虚拟时钟 + sys.path)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_ds(tree):
    from mirbal.data import DataSet
    _cfg, srv = boot.make_service(tree)
    return DataSet(srv, tree or "main")


def cmd_arena(a, ds=None):
    from mirbal import batch
    ds = ds or load_ds(a.tree)
    levels = (5, 15, 25, 35) if a.quick else batch.LEVELS
    return batch.run_arena(a.tree, ds, fights=4 if a.quick else a.fights, procs=a.procs,
                           levels=levels, log=log, budget_s=a.budget)


def cmd_world(a, ds=None, arena=None, cached_only=False):
    from mirbal import worldsim_batch
    ds = ds or load_ds(a.tree)
    arena = arena if arena is not None else cmd_arena(a, ds)
    return worldsim_batch.run_all(a.tree, ds, arena, quick=a.quick, log=log, budget_s=a.budget,
                                 cached_only=cached_only)


def cmd_report(a):
    from mirbal import report
    ds = load_ds(a.tree)
    arena = cmd_arena(a, ds)
    world = cmd_world(a, ds, arena, cached_only=a.cmd == "report")
    cmp = None
    if a.compare:
        ca = argparse.Namespace(**{**vars(a), "tree": a.compare})
        cds = load_ds(a.compare)
        carena = cmd_arena(ca, cds)
        cmp = (cds, carena, world)          # η 沿用主树的真实地图测量
    path = report.build(ds, arena, world, compare=cmp, out_dir=HERE / "out", log=log)
    log(f"报告：{path}")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["arena", "world", "report", "all"])
    ap.add_argument("--tree", default=None, help="数据树：main / 176 / 路径（默认 = server.properties 生效树）")
    ap.add_argument("--compare", default=None, help="对比数据树")
    ap.add_argument("--fights", type=int, default=8)
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--budget", type=float, default=None, help="单次运行时间预算（秒），到点保存进度退出")
    a = ap.parse_args()
    if a.cmd == "arena":
        r = cmd_arena(a)
        sys.exit(0 if r is not None else 3)
    elif a.cmd == "world":
        r = cmd_world(a)
        sys.exit(0 if r is not None else 3)
    else:
        cmd_report(a)


if __name__ == "__main__":
    main()
