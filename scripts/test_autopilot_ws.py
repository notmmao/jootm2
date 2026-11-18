#!/usr/bin/env python3
"""在线托管 AutoPilot 的真实 WebSocket 端到端回归。

起一个独立实例（动态端口 + 临时库），走完整登录链路，用真实 WS 帧验证：
    状态查询 → 开启 → REST 可观察 → 手动 move 暂停 → 恢复 → 切换档位
    → 关闭 → 断线清场 → 重登不恢复。

用法：D:/Python310/python.exe scripts/test_autopilot_ws.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from smoke_ws import WS, build_steps  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = "http://127.0.0.1:%d" % PORT
DB = ROOT / "data" / "_tmp_autopilot_ws.db"


def api(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ready(proc, limit=30.0) -> None:
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端提前退出")
        try:
            api("/api/status")
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("服务端启动超时")


def wait_msg(ws, typ: str, limit: float = 3.0, pred=None) -> dict | None:
    """持续读帧直到收到「满足 pred」的指定 type。

    tick 每 1.5s 也会推一次 autopilotState，缓冲区里可能积压旧状态；
    只按 type 取第一条会读到过期帧，所以调用方要给出期望条件。
    """
    end = time.time() + limit
    found = None
    while time.time() < end:
        try:
            msg = ws.recv()
        except (socket.timeout, TimeoutError):
            continue
        except (EOFError, OSError):
            break
        if msg is not None and msg.get("type") == typ \
                and (pred is None or pred(msg)):
            found = msg
            break
    return found


def send_and_wait(ws, payload, pred=None, typ="autopilotState"):
    ws.drain(64)                 # 清掉 tick 广播与上一次的残留回包
    ws.send(payload)
    return wait_msg(ws, typ, pred=pred)


def login(ws, tag: str, register: bool = True) -> None:
    steps = build_steps(tag)
    names = (("register", "login", "listCharacters", "createCharacter",
              "enterGame", "noticeOk") if register else
             ("login", "listCharacters", "enterGame", "noticeOk"))
    for name in names:
        ws.send(steps[name])
        ws.drain(24)


def soak(ws, seconds: int) -> int:
    """可选长跑：真实 tick 下观察 EXP / 击杀 / 血量 / 是否意外停机。"""
    ws.sock.settimeout(0.05)
    ws.send({"type": "autopilot", "action": "on", "preset": "balanced"})
    wait_msg(ws, "autopilotState", pred=lambda m: m.get("active") is True)
    first = api("/api/autopilot")["pilots"][0]
    print(f"  起点: lv{first['level']} exp={first['exp']} "
          f"hp={first['hp']}/{first['maxHp']}")
    start = time.time()
    last = first
    stopped_at = None
    for i in range(seconds):
        time.sleep(1.0)
        ws.drain(64)
        snap = api("/api/autopilot")
        if not snap.get("pilots"):
            stopped_at = i + 1
            print(f"  t+{i + 1}s 托管已停止（死亡 / 熔断 / 背包满）")
            break
        last = snap["pilots"][0]
        if (i + 1) % 10 == 0:
            print(f"  t+{i + 1:2d}s lv{last['level']} exp={last['exp']} "
                  f"kills={last['kills']} hp={last['hp']}/{last['maxHp']} "
                  f"{last['status']}")
    elapsed = time.time() - start
    print(f"  终点: lv{last['level']} exp={last['exp']} kills={last['kills']} "
          f"hp={last['hp']}/{last['maxHp']} · {last['status']}")
    print(f"  用时 {elapsed:.1f}s · 仍在线={stopped_at is None}"
          + (f" · 提前停机于 t+{stopped_at}s" if stopped_at else ""))
    return 0


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    for p in DB.parent.glob(DB.name + "*"):
        p.unlink()
    env = os.environ.copy()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyserver", "--host", "127.0.0.1",
         "--port", str(PORT), "--db", str(DB), "--no-resource-index",
         "--no-bots"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    ws = ws2 = None
    try:
        wait_ready(proc)
        tag = "%d%d" % (os.getpid() % 10000, int(time.time()) % 10000)
        ws = WS("127.0.0.1", PORT, "/ws", timeout=0.5)
        login(ws, tag)
        players = api("/api/players")["players"]
        check("登录成功", any(p["name"] == build_steps(tag)["createCharacter"]["name"]
                              for p in players),
              f"online={[p['name'] for p in players]}")
        soak_seconds = 0
        if "--soak" in sys.argv:
            soak_seconds = int(sys.argv[sys.argv.index("--soak") + 1])
        if soak_seconds:
            print(f"\n=== 真实挂机长跑 {soak_seconds}s ===")
            return soak(ws, soak_seconds)

        print("\n=== 1. 查询 / 开启 ===")
        ws.send({"type": "autopilot", "action": "status"})
        st = wait_msg(ws, "autopilotState")
        check("未开启时状态为 off", st is not None and not st.get("active"))
        check("回包带三档策略", st is not None and len(st.get("presets", [])) == 3)
        st = send_and_wait(ws, {"type": "autopilot", "action": "on",
                                "preset": "balanced"},
                           lambda m: m.get("active") is True)
        check("开启回包 active=true", st is not None,
              f"status={st.get('status') if st else None}")
        check("档位为均衡", st is not None and st.get("preset") == "balanced")

        print("\n=== 2. REST 可观察 + 真实 tick 推进 ===")
        snap = api("/api/autopilot")
        check("REST 可见 1 个托管实例", snap.get("count") == 1,
              f"pilots={snap.get('pilots')}")
        time.sleep(1.6)
        snap2 = api("/api/autopilot")
        check("tick 后实例仍存活", snap2.get("count") == 1,
              f"status={snap2['pilots'][0]['status'] if snap2.get('pilots') else '-'}")

        print("\n=== 3. 手动操作接管 / 再开启 / 切档 ===")
        ws.send({"type": "move", "dir": 2})
        st = wait_msg(ws, "autopilotState",
                      pred=lambda m: m.get("paused") is True)
        check("手动 move 推 paused 状态",
              st is not None and not st.get("active"),
              f"reason={st.get('reason') if st else None}")
        st = send_and_wait(ws, {"type": "autopilot", "action": "on"},
                           lambda m: m.get("active") is True)
        check("再次点击可恢复", st is not None)
        st = send_and_wait(ws, {"type": "autopilot", "action": "preset",
                                "preset": "safe"},
                           lambda m: m.get("preset") == "safe")
        check("切换为安全档", st is not None)

        print("\n=== 4. 关闭 / 断线即停 / 重登不恢复 ===")
        st = send_and_wait(ws, {"type": "autopilot", "action": "off"},
                           lambda m: not m.get("active"))
        check("手动关闭生效", st is not None,
              f"reason={st.get('reason') if st else None}")
        st = send_and_wait(ws, {"type": "autopilot", "action": "on"},
                           lambda m: m.get("active") is True)
        check("断线前处于托管中", api("/api/autopilot").get("count") == 1)
        ws.close()                      # 模拟掉线（不走 logout）
        ws = None
        time.sleep(1.2)
        check("断线后实例被清空", api("/api/autopilot").get("count") == 0)
        check("断线后角色不在线", api("/api/players")["players"] == [])

        ws2 = WS("127.0.0.1", PORT, "/ws", timeout=0.5)
        login(ws2, tag, register=False)
        ws2.send({"type": "autopilot", "action": "status"})
        st = wait_msg(ws2, "autopilotState")
        check("重登后不自动恢复托管",
              st is not None and not st.get("active") and not st.get("paused"))
        check("REST 仍为 0", api("/api/autopilot").get("count") == 0)
    finally:
        if ws is not None:
            ws.close()
        if ws2 is not None:
            ws2.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for p in DB.parent.glob(DB.name + "*"):
            try:
                p.unlink()
            except OSError:
                pass

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n结论: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
