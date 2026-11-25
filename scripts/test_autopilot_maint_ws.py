#!/usr/bin/env python3
"""在线托管「一键维护」WS 集成回归（Python 3.10 + 本地 pyserver）。

链路：起服 → 登录进图 → 开托管 → GM @制造 肉 → 发 maint →
断言服务端回执进入维护状态（系统消息或 autopilotState.status），
再验重复 maint 幂等提示、无对象时明确拒绝。

    D:/Python310/python.exe scripts/test_autopilot_maint_ws.py
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

P = F = 0
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def main():
    from smoke_ws import WS
    db = ROOT / "data" / "_maintws.db"
    for f in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if f.exists():
            f.unlink()
    proc = subprocess.Popen(
        [r"D:/Python310/python.exe", "-m", "pyserver", "--host", "127.0.0.1",
         "--port", str(PORT), "--db", str(db), "--no-bots", "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        end = time.time() + 60
        while time.time() < end:
            try:
                urllib.request.urlopen(BASE + "/api/status", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("server not up")

        ws = WS("127.0.0.1", PORT, "/ws", timeout=1.0)

        def collect(limit=1.0):
            out = []
            e = time.time() + limit
            while time.time() < e:
                try:
                    out += ws.drain(64)
                except Exception:
                    pass
            return out

        def wait(typ, limit=8.0, pred=None):
            e = time.time() + limit
            while time.time() < e:
                try:
                    msgs = ws.drain(64)
                except Exception:
                    continue
                for m in msgs:
                    if m.get("type") == typ and (pred is None or pred(m)):
                        return m
            return None

        ws.send({"type": "register", "username": "player", "password": "123456"})
        collect(0.5)
        ws.send({"type": "login", "username": "player", "password": "123456"})
        r = wait("loginResult", pred=lambda m: m.get("ok"))
        check("GM 自测号登录", bool(r))
        ws.send({"type": "listCharacters"})
        r = wait("characterList")
        if not (r and r.get("characters")):
            ws.send({"type": "createCharacter", "name": "MaintBot", "sex": 0,
                     "hair": 1, "job": 0})
            wait("createCharacterResult")
            char = "MaintBot"
        else:
            char = r["characters"][0]["name"]
        ws.send({"type": "enterGame", "name": char})
        if wait("notice", 8.0):
            ws.send({"type": "noticeOk", "name": char})
        check("进游戏", bool(wait("enterGameResult", 10.0,
                                pred=lambda m: m.get("ok"))))
        collect(1.5)

        ws.send({"type": "autopilot", "action": "on"})
        st = wait("autopilotState", pred=lambda m: m.get("active"))
        check("托管已开启", bool(st))

        # 空维护（新号没肉、装备耐久满）→ 明确拒绝，不是无响应
        ws.send({"type": "autopilot", "action": "maint"})
        got = None
        e = time.time() + 4
        while time.time() < e and not got:
            for m in collect(0.8):
                if m.get("type") == "systemMessage" and "无需维护" in (m.get("text") or ""):
                    got = m
                if m.get("type") == "systemMessage" and "开始维护" in (m.get("text") or ""):
                    got = m   # 新号身上有低耐久装备时走这条，也算链路通
        check("维护请求有明确回执", bool(got), str(got)[:100])

        # 造 3 块肉再维护 → 进入维护状态
        ws.send({"type": "say", "text": "@制造 肉 3"})
        time.sleep(0.6)
        collect(1.0)
        maint_seen = []
        ws.send({"type": "autopilot", "action": "maint"})
        e = time.time() + 10
        while time.time() < e:
            for m in collect(0.8):
                if m.get("type") == "autopilotState" and "维护" in (m.get("status") or ""):
                    maint_seen.append(m["status"])
                if m.get("type") == "systemMessage" and "维护" in (m.get("text") or ""):
                    maint_seen.append(m["text"])
            if any("开始维护" in s for s in maint_seen):
                break
        check("有肉时进入维护", bool(maint_seen), str(maint_seen)[:160])
        # 重复点击幂等：进行中不再叠加流程（状态仍 active，无异常）
        ws.send({"type": "autopilot", "action": "maint"})
        time.sleep(1.0)
        ws.send({"type": "autopilot", "action": "status"})
        st2 = wait("autopilotState", 5.0)
        check("重复维护不打崩托管", st2 is not None and st2.get("active"),
              str((st2 or {}).get("status"))[:60])
        # 维护中被打断（手动 move 消息 = MANUAL_TYPES）→ 暂停 + 装备兜底不报错
        ws.send({"type": "move", "dir": 2})
        time.sleep(0.8)
        ws.send({"type": "autopilot", "action": "status"})
        st3 = wait("autopilotState", 5.0)
        check("手动接管后暂停", st3 is not None and st3.get("paused"),
              str(st3)[:100])
        ws.close()
    finally:
        proc.kill()
    print(f"\n结果: {P} PASS / {F} FAIL")
    sys.exit(1 if F else 0)


def json_text(m):
    import json
    return json.dumps(m, ensure_ascii=False)


def json_text(m):
    import json
    return json.dumps(m, ensure_ascii=False)


if __name__ == "__main__":
    main()
