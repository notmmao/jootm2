#!/usr/bin/env python3
"""jootm2 服务端协议冒烟测试（零依赖、无需任何客户端资源）。

只依赖 Python 标准库，手写 WebSocket 握手与帧编解码，直连 /ws 走一遍
「注册 → 登录 → 建角 → 进游戏 → 移动」，用来验证服务端在缺少
resources/（WIL/WIX 图库与 .map）时是否仍能完整工作。

用法:
    python scripts/smoke_ws.py
    python scripts/smoke_ws.py --host 127.0.0.1 --port 8080
    python scripts/smoke_ws.py --json report.json

退出码: 0 = 全部通过, 1 = 有步骤失败, 2 = 无法连接/握手失败。
"""

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time

DEFAULT_STEPS = (
    "ping", "register", "login", "listCharacters",
    "createCharacter", "enterGame", "noticeOk", "move",
)


class WS:
    """最小 WebSocket 客户端：只实现文本帧，够用于 JSON 协议验证。"""

    def __init__(self, host, port, path, timeout=2.0):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(timeout)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手期间连接关闭")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        self.status = head.split(b"\r\n")[0].decode(errors="replace")
        if "101" not in self.status:
            raise RuntimeError("WebSocket 握手失败: " + self.status)

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        n = len(data)
        hdr = bytearray([0x81])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(hdr) + payload)

    def _take(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("连接关闭")
            self.buf += chunk
        part, self.buf = self.buf[:n], self.buf[n:]
        return part

    def recv(self):
        h = self._take(2)
        op, masked, n = h[0] & 0x0F, h[1] & 0x80, h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._take(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._take(8))[0]
        mask = self._take(4) if masked else b""
        payload = self._take(n)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op == 9:  # ping → pong
            self.sock.sendall(b"\x8a\x80" + os.urandom(4))
            return None
        if op != 1:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def drain(self, max_frames=16):
        """读走当前可读到的所有帧（tick 广播会夹带 entityAdd/stats 等）。"""
        out = []
        for _ in range(max_frames):
            try:
                msg = self.recv()
            except (socket.timeout, TimeoutError):
                break
            if msg is not None:
                out.append(msg)
        return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def build_steps(tag):
    user, char = "smoke" + tag, "Smoke" + tag[-4:]
    return {
        "ping": {"type": "ping"},
        "register": {"type": "register", "username": user, "password": "smoke123"},
        "login": {"type": "login", "username": user, "password": "smoke123"},
        "listCharacters": {"type": "listCharacters"},
        "createCharacter": {"type": "createCharacter", "name": char, "sex": 0, "hair": 1, "job": 0},
        "enterGame": {"type": "enterGame", "name": char},
        "noticeOk": {"type": "noticeOk", "name": char},
        "move": {"type": "move", "dir": 2},
    }


# 每一步期望出现的消息类型（noticeOk 后服务端会连发多条）
EXPECT = {
    "ping": ("pong",),
    "register": ("registerResult",),
    "login": ("loginResult",),
    "listCharacters": ("characterList",),
    "createCharacter": ("createCharacterResult",),
    "enterGame": ("notice", "enterGameResult"),
    "noticeOk": ("enterGameResult", "enterGame"),
    "move": ("move",),
}


def run(host, port, path):
    tag = "%d%d" % (os.getpid() % 10000, int(time.time()) % 10000)
    steps, report = build_steps(tag), {"host": host, "port": port, "path": path, "steps": [], "ok": False}
    try:
        ws = WS(host, port, path)
    except (OSError, RuntimeError) as e:
        report["error"] = str(e)
        print("无法连接 %s:%d%s → %s" % (host, port, path, e))
        return 2, report
    report["handshake"] = ws.status
    print("握手: %s" % ws.status)
    try:
        all_ok = True
        for name in DEFAULT_STEPS:
            ws.send(steps[name])
            got = ws.drain()
            types = [m.get("type", "?") for m in got]
            expected = EXPECT[name]
            ok = any(t in types for t in expected)
            # 关键结果消息还要求 ok=true
            for m in got:
                if m.get("type") in ("registerResult", "loginResult",
                                     "createCharacterResult", "enterGameResult"):
                    if not m.get("ok"):
                        ok = False
            all_ok = all_ok and ok
            print("%-18s %s  %s" % (name, "PASS" if ok else "FAIL", ", ".join(types) or "(无响应)"))
            report["steps"].append({"name": name, "ok": ok, "received": types})
        report["ok"] = all_ok
        return (0 if all_ok else 1), report
    except (EOFError, OSError) as e:
        report["error"] = "连接中断: %s" % e
        print(report["error"])
        return 1, report
    finally:
        ws.close()


def main():
    ap = argparse.ArgumentParser(description="jootm2 服务端协议冒烟测试（无需客户端资源）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--path", default="/ws")
    ap.add_argument("--json", dest="json_out", help="把结果写入 JSON 文件")
    a = ap.parse_args()
    code, report = run(a.host, a.port, a.path)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告: " + a.json_out)
    print("结论: " + ("服务端协议层全通（无需 resources/）" if code == 0 else "存在失败步骤"))
    return code


if __name__ == "__main__":
    sys.exit(main())
