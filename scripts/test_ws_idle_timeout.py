"""半开连接回归：TCP 不断开也不发帧时，服务端必须在超时后清场。

用短超时（1s ping / 3s idle）起一个独立实例，登录后完全静默，
验证 on_disconnect 被触发（在线列表清空）且连接被关闭。
用法：D:/Python310/python.exe scripts/test_ws_idle_timeout.py
"""
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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()          # 避免撞上本机已有的 8080/8099 实例
BASE = "http://127.0.0.1:%d" % PORT
DB = ROOT / "data" / "_tmp_ws_idle.db"


def api(path):
    with urllib.request.urlopen(BASE + path, timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ready(proc, limit=30.0):
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


def main():
    env = os.environ.copy()
    env["JOOTM2_WS_PING_INTERVAL"] = "1"
    env["JOOTM2_WS_IDLE_TIMEOUT"] = "3"
    for p in DB.parent.glob(DB.name + "*"):
        p.unlink()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyserver", "--host", "127.0.0.1",
         "--port", str(PORT), "--db", str(DB), "--no-resource-index"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    ws = ws2 = None
    try:
        wait_ready(proc)
        ws = WS("127.0.0.1", PORT, "/ws", timeout=0.5)
        steps = build_steps("idle")
        for name in ("register", "login", "listCharacters",
                     "createCharacter", "enterGame", "noticeOk"):
            ws.send(steps[name])
            got = ws.drain()
            for m in got:
                if m.get("type") in ("registerResult", "createCharacterResult"):
                    assert m.get("ok"), f"{name} 失败（可能连到了旧实例）: {m}"
        before = api("/api/players")["players"]
        assert before, "登录后在线列表为空"
        print("[1] 登录后在线:", [p["name"] for p in before])

        time.sleep(5.0)   # 静默：不读不写，模拟息屏 / 页面直接关掉的 TCP 半开
        after = api("/api/players")["players"]
        assert not after, f"超时后仍在线（断线即停失效）: {after}"
        print("[2] 超时后在线列表清空 → on_disconnect 已清场")

        ws.sock.settimeout(2.0)
        closed = False
        try:
            while True:
                if not ws.sock.recv(4096):
                    closed = True
                    break
        except (OSError, TimeoutError):
            pass
        assert closed, "服务端未关闭半开连接"
        print("[3] 客户端侧连接已关闭")

        # 对照：浏览器收到 ping 会自动回 pong。模拟一个「不发业务消息但会
        # 回 pong」的在线客户端（托管时正是这种状态），不应被误踢。
        ws2 = WS("127.0.0.1", PORT, "/ws", timeout=0.3)
        steps2 = build_steps("live")
        for name in ("register", "login", "listCharacters",
                     "createCharacter", "enterGame", "noticeOk"):
            ws2.send(steps2[name])
            got = ws2.drain()
            for m in got:
                if m.get("type") in ("registerResult", "createCharacterResult"):
                    assert m.get("ok"), f"{name} 失败: {m}"
        end = time.time() + 5.0
        while time.time() < end:
            try:
                ws2.recv()          # WS.recv 遇到 ping 会自动回 pong
            except (socket.timeout, TimeoutError, EOFError, OSError):
                pass
        live = [p["name"] for p in api("/api/players")["players"]]
        assert steps2["createCharacter"]["name"] in live, \
            f"会回 pong 的正常在线客户端被误踢: {live}"
        print("[4] 会回 pong 的在线客户端未被误踢:", live)
        print("结论: PASS 半开连接超时清场，正常在线不受影响")
        return 0
    finally:
        if ws:
            ws.close()
        if ws2:
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


if __name__ == "__main__":
    sys.exit(main())
