# -*- coding: utf-8 -*-
"""真实服务器进程 CPU A/B 基准（2026-11-25 寻路优化验证）。

流程：
  1. 用默认库的只读副本拉起一个 `python -m pyserver` 子进程（--no-bots）；
  2. GM 账号 player 登录两个角色（战士 TestHero / 法师 TestMage2），
     @等级 @满 @移动 E603 @刷怪 各 40 只，然后开托管；
  3. 静置 30s 让挂机状态进入稳态，再采样 60s：取服务进程 CPU 时间增量
     （单线程进程，60s 墙钟内 Δcpu 秒数即平均核占用）。

用法（同一份代码可 --reps 2 取中位）：
    D:/Python310/python.exe scripts/bench_server_cpu.py [--port 8123]

退出码 0 正常；结果 JSON 打印在 stdout 末尾。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from smoke_ws import WS  # noqa: E402

PY = sys.executable


def copy_db(dst: str, src: str | None = None) -> None:
    """默认库是 WAL 模式，离线拷贝必须走 backup API。"""
    src = src or os.path.join(ROOT, "data", "jootm2.db")
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)


def ws_steps(port: int):
    def send(ws, obj):
        ws.send(obj)

    def expect(ws, typ, limit=8.0, pred=None):
        end = time.time() + limit
        while time.time() < end:
            try:
                msg = ws.recv()
            except (socket.timeout, TimeoutError):
                continue
            except (EOFError, OSError):
                return None
            if msg is not None and msg.get("type") == typ \
                    and (pred is None or pred(msg)):
                return msg
        return None

    return send, expect


def login_char(ws, name: str) -> bool:
    """player 账号登录并进入指定角色（角色须已存在——账号满 2 位）。"""
    ws.send({"type": "login", "username": "player", "password": "123456"})
    if not _wait(ws, "loginResult", ok=True):
        return False
    ws.send({"type": "listCharacters"})
    lst = _wait(ws, "characterList")
    if lst is None:
        return False
    chars = lst.get("characters") or lst.get("list") or []
    if name not in [c.get("name") for c in chars]:
        return False
    ws.send({"type": "enterGame", "name": name})
    _wait(ws, "notice", limit=10.0)
    ws.send({"type": "noticeOk", "name": name})
    if not _wait(ws, "enterGameResult", ok=True, limit=10.0):
        return False
    _wait(ws, "enterGame", limit=6.0)
    return True


def _wait(ws, typ, limit=8.0, ok=None):
    end = time.time() + limit
    while time.time() < end:
        try:
            msg = ws.recv()
        except (socket.timeout, TimeoutError):
            continue
        except (EOFError, OSError):
            return None
        if msg is not None and msg.get("type") == typ:
            if ok is None or msg.get("ok") is ok:
                return msg
    return None


def gm(ws, line: str, settle=0.4) -> None:
    ws.send({"type": "say", "text": line})
    time.sleep(settle)
    # 吃掉命令回执，避免缓冲区积压
    try:
        ws.drain(32)
    except Exception:
        pass


def setup_offline_pilot(ws, char: str, map_xy: str) -> bool:
    """把角色武装成「离线挂机代理」负载（生产 CPU 主形态）。

    流程：@等级 @造药 → 传送到挂机图 → @离线 grant 加余额 →
    afk start 设计划 → 调用方关闭连接，下线瞬间 pilot 生效，
    此后每 tick 由 OfflinePilot 驱动完整热路径（追怪/拾取/商人补给/
    修理/跨图门链），寻路 A* 高频触发——与生产火焰图一致。"""
    gm(ws, "@等级 40")
    gm(ws, "@制造 强效金创药 20")
    gm(ws, "@制造 强效魔法药 15")
    gm(ws, "@满")
    gm(ws, f"@移动 {map_xy}")
    gm(ws, f"@离线 grant {char} 700")
    ws.send({"type": "afk", "action": "start", "minutes": 600,
             "preset": "balanced"})
    ok = _wait(ws, "afkState", limit=6.0, ok=True)
    try:
        ws.drain(64)
    except Exception:
        pass
    return ok is not None


def sample_cpu(proc, seconds: float, feeder=None, feed_every: float = 6.0,
               readers=()):
    """返回窗口内服务进程的 (CPU 时间增量秒, 墙钟秒)。

    feeder 在每个 feed_every 秒的边界调用一次（保持战斗负载不枯竭）；
    readers 是 WS 客户端列表——**必须持续排空**，否则服务端广播帧积压撑爆
    socket 发送缓冲导致连接被断（实测会连锁让服务主动踢线）。
    """
    import psutil
    ps = psutil.Process(proc.pid)
    ps.cpu_percent(interval=None)          # 初始化基线
    for ws in readers:
        try:
            ws.sock.settimeout(0.05)
        except Exception:
            pass
    t0, w0 = ps.cpu_times(), time.perf_counter()
    next_feed = w0 + feed_every
    while True:
        now = time.perf_counter()
        remain = w0 + seconds - now
        if remain <= 0:
            break
        # 排空所有连接
        for ws in readers:
            try:
                while ws.recv() is not None:
                    pass
            except Exception:
                pass
        time.sleep(min(0.1, max(0.01, remain)))
        if time.perf_counter() >= next_feed and feeder:
            feeder()
            next_feed = time.perf_counter() + feed_every
    t1, w1 = ps.cpu_times(), time.perf_counter()
    return (t1.user + t1.system - t0.user - t0.system), (w1 - w0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--warmup", type=float, default=30.0)
    ap.add_argument("--sample", type=float, default=60.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--db-src", dest="db_src", default=None,
                    help="基准库路径（worktree 运行时应指向主仓库 data/jootm2.db）")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="jootm2-bench-")
    db = os.path.join(tmp, "bench.db")
    copy_db(db, args.db_src)
    proc = subprocess.Popen(
        [PY, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(args.port),
         "--db", db, "--gamedata", os.path.join(ROOT, "gamedata"),
         "--no-resource-index", "--no-bots"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等待就绪
        url = f"http://127.0.0.1:{args.port}/api/status"
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError("服务端提前退出")
            try:
                urllib.request.urlopen(url, timeout=2).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("启动超时")

        # 空闲基线（tick 循环 + 3600+ 常驻怪 AI，无人托管）
        idle = sample_cpu(proc, 15.0)

        # 两个离线挂机代理（生产主负载）：E603 是 20-35 级最佳挂机图，
        # 316 只原生怪持续供给战斗；下线瞬间 pilot 生效。
        w1 = WS("127.0.0.1", args.port, "/ws", timeout=3.0)
        if not login_char(w1, "TestHero"):
            raise RuntimeError("TestHero 登录失败")
        p1_ok = setup_offline_pilot(w1, "TestHero", "E603 152 89")
        w1.close()

        w2 = WS("127.0.0.1", args.port, "/ws", timeout=3.0)
        if not login_char(w2, "歪比巴卜"):
            raise RuntimeError("歪比巴卜 登录失败")
        p2_ok = setup_offline_pilot(w2, "歪比巴卜", "E603 152 92")
        w2.close()

        # 预热期：代理在完整热路径跑起来（选点、战斗、拾取、补给）
        time.sleep(args.warmup)
        busy = sample_cpu(proc, args.sample)

        # 诊断：离线代理状态（经 GM 连接 @离线 list 查询）
        w3 = WS("127.0.0.1", args.port, "/ws", timeout=3.0)
        login_char(w3, "歪比巴卜")
        pilots_txt = []
        gm(w3, "@离线 list", settle=1.0)
        try:
            for _ in range(60):
                try:
                    m = w3.recv()
                except Exception:
                    break
                if m and m.get("type") == "systemMessage":
                    t = m.get("text", "")
                    if "级" in t and "[" in t:
                        pilots_txt.append(t.strip())
        except Exception:
            pass
        w3.close()

        status = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{args.port}/api/status", timeout=3).read())
        result = {
            "label": args.label,
            "port": args.port,
            "idle_cpu_core_frac": round(idle[0] / idle[1], 3),
            "busy_cpu_s": round(busy[0], 2),
            "busy_wall_s": round(busy[1], 2),
            "busy_core_frac": round(busy[0] / busy[1], 3),
            "monsters": status.get("monsters"),
            "setup": {"p1_ok": p1_ok, "p2_ok": p2_ok},
            "pilots": pilots_txt,
        }
        print(json.dumps(result, ensure_ascii=False, indent=1))
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
