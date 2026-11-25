# -*- coding: utf-8 -*-
"""真实进程 py-spy 火焰图 A/B（寻路优化验证，2026-11-25）。

总量 CPU 采样受挂机代理随机行为影响方差太大；火焰图的**相对占比**
（PathPlanner / _resolve_target / tick 占比）才是稳定的前后对比指标。

流程：
  1. 后台起 pyserver（默认库副本，--no-bots）；
  2. 登录两个角色 → @离线 grant + afk start → 断开 → 两个离线代理
     在生产热路径挂机（E603 满怪图 + 商人补给）；
  3. 预热 25s 进入稳态，py-spy record 45s（100Hz, speedscope 格式）；
  4. 解析 profile：按函数聚合 self 采样，输出寻路/AI/网络三大类占比。

用法：
    D:/Python310/python.exe scripts/bench_profile_ab.py --label after
产物：data/profile-<label>.json（speedscope，可上传 https://www.speedscope.app 看火焰图）
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from smoke_ws import WS  # noqa: E402
import bench_server_cpu as B  # noqa: E402

PYPY = r"D:\Python310\Scripts\py-spy.exe"


def record(pid: int, seconds: float, out_path: str) -> None:
    subprocess.run([PYPY, "record", "--pid", str(pid), "-d", str(int(seconds)),
                    "-r", "100", "--format", "speedscope", "-o", out_path,
                    "--nonblocking"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def aggregate(speedscope_path: str):
    """按函数聚合 self（叶子）采样占比，并归类三大热点。"""
    with open(speedscope_path, encoding="utf-8") as f:
        d = json.load(f)
    frames = d["shared"]["frames"]
    self_w = collections.Counter()
    total = 0.0
    for prof in d["profiles"]:
        if prof.get("type") != "sampled":
            continue
        # speedscope sampled profile：每个样本是一条调用栈（帧索引列表，
        # 末位=叶子帧），weights 与之对齐
        for stack, w in zip(prof["samples"], prof["weights"]):
            if not stack:
                continue
            self_w[stack[-1]] += w
            total += w
    rows = []
    for idx, w in self_w.most_common(20):
        fr = frames[idx]
        name = fr.get("name", "?")
        mod = (fr.get("file") or "?").replace("\\", "/").split("/")[-1]
        rows.append((round(w / total * 100, 2), f"{name} @{mod}:{fr.get('line','?')}"))
    cat = collections.Counter()
    pat_path = re.compile(r"PathPlanner|advance|_step_to|bfs_path")
    pat_ai = re.compile(r"_monster_ai|_resolve_target|_acquire_target|do_move|try_move")
    pat_idle = re.compile(r"_poll|_run_once|__call_soon|_run\b|select|epoll|"
                          r"cancel|sendall|send_json|dump|encode|recv")
    for idx, w in self_w.items():
        fr = frames[idx]
        name = fr.get("name", "")
        if w == 0:
            continue
        if pat_path.search(name):
            cat["寻路 PathPlanner"] += w
        elif pat_ai.search(name):
            cat["怪物 AI/移动"] += w
        elif pat_idle.search(name):
            cat["事件循环/IO"] += w
        else:
            cat["其他(游戏逻辑)"] += w
    cats = {k: round(v / total * 100, 2) for k, v in cat.most_common()}
    return rows, cats, int(total)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--port", type=int, default=8141)
    ap.add_argument("--warmup", type=float, default=25.0)
    ap.add_argument("--record", type=float, default=45.0)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="jootm2-prof-")
    db = os.path.join(tmp, "p.db")
    B.copy_db(db)
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyserver", "--host", "127.0.0.1",
         "--port", str(args.port), "--db", db, "--no-resource-index",
         "--no-bots"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError("服务提前退出")
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{args.port}/api/status", timeout=2).read()
                break
            except Exception:
                time.sleep(0.2)

        for char, xy in (("TestHero", "E603 152 89"), ("歪比巴卜", "E603 152 92")):
            ws = WS("127.0.0.1", args.port, "/ws", timeout=5.0)
            if not B.login_char(ws, char):
                raise RuntimeError(f"{char} 登录失败")
            ok = B.setup_offline_pilot(ws, char, xy)
            print(f"{char}: pilot setup {'ok' if ok else 'FAILED'}", flush=True)
            ws.close()

        time.sleep(args.warmup)
        out = os.path.join(ROOT, "data", f"profile-{args.label}.json")
        print(f"recording {args.record:.0f}s on pid={proc.pid} ...", flush=True)
        record(proc.pid, args.record, out)

        rows, cats, nsamples = aggregate(out)
        print(json.dumps({"label": args.label, "seconds": nsamples,
                          "categories_pct": cats}, ensure_ascii=False, indent=1))
        print("--- TOP self frames ---")
        for pct, name in rows:
            print(f"{pct:6.2f}%  {name}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
