#!/usr/bin/env python3
"""挂机网页后台 REST 集成测试（Python 3.10，起真实服务端）。

    D:/Python310/python.exe scripts/test_afk_dashboard.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    if ok:
        P += 1
    else:
        F += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base, proc, limit=40):
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端提前退出")
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2):
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("启动超时")


def post(base, path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read().decode())


def main():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from smoke_ws import WS, build_steps
    from pyserver import auth_token
    from pyserver.db import Database

    tmp = tempfile.TemporaryDirectory(prefix="jootm2_dash_")
    db_path = Path(tmp.name) / "test.db"
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(db_path), "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    ws_list = []
    try:
        wait_ready(base, proc)

        stamp = str(int(time.time()))[-6:]
        steps = build_steps(stamp)
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        ws = WS("127.0.0.1", port, "/ws", timeout=1.0)
        ws_list.append(ws)
        for n in ("register", "login", "listCharacters", "createCharacter", "enterGame", "noticeOk"):
            ws.send(steps[n]); ws.drain(16)
        ws.drain(20)

        db = Database(db_path)
        acc = db.find_account(user)
        check("测试账号存在", acc is not None)
        chars = db.list_characters(acc.id)
        cid = next(c["id"] for c in chars if c["name"] == char)
        token = auth_token.issue(acc.id, acc.username)

        # 另建 GM 账号 player（GM_ACCOUNTS 白名单）
        gws = WS("127.0.0.1", port, "/ws", timeout=1.0)
        ws_list.append(gws)
        gws.send({"type": "register", "username": "player", "password": "gm123456"}); gws.drain(16)
        gws.send({"type": "login", "username": "player", "password": "gm123456"}); gws.drain(16)
        gacc = db.find_account("player")
        gtoken = auth_token.issue(gacc.id, gacc.username)

        print("\n[1] 静态后台页面可访问")
        with urllib.request.urlopen(base + "/afk.html", timeout=5) as r:
            html = r.read().decode("utf-8")
        check("/afk.html 可访问", "离线修炼" in html)
        check("页面含 REST 端点", "/api/dash/auth" in html)
        check("页面不再有在线托管", "在线托管" not in html and "scope: 'autopilot'" not in html)
        check("页面不再有设定计划按钮", "afkStart" not in html and "afkMinutes" not in html)

        print("\n[2] token 认证")
        bad = post(base, "/api/dash/auth", {"token": "invalid.token"})
        check("无效 token 被拒", bad.get("ok") is False, str(bad))
        ok = post(base, "/api/dash/auth", {"token": token})
        check("有效 token 通过", ok.get("ok") is True, str(ok))
        check("返回角色列表含建的角色", any(c["id"] == cid for c in ok.get("characters", [])))
        gok = post(base, "/api/dash/auth", {"token": gtoken})
        check("GM 账号标记正确", gok.get("isGm") is True, str(gok)[:120])
        check("普通账号非 GM", ok.get("isGm") is False)

        print("\n[3] 状态查询")
        st = get(base, f"/api/dash/state?token={token}&charId={cid}")
        check("在线角色状态可读", st.get("ok") is True, str(st)[:200])
        check("含 autopilot 状态", "autopilot" in st)
        check("含 afk 状态", "afk" in st)

        print("\n[4] 越权拦截")
        forged = get(base, f"/api/dash/state?token={token}&charId=999999")
        check("伪造角色 id 被拒", forged.get("ok") is False, str(forged))
        normal_all = get(base, f"/api/dash/state?token={token}&all=1")
        check("普通账号 all=1 拿不到全服 pilots", "pilots" not in normal_all)

        print("\n[5] GM 全服视图")
        gm = get(base, f"/api/dash/state?token={gtoken}&all=1")
        check("GM all=1 可读", gm.get("ok") is True and gm.get("isGm") is True, str(gm)[:200])
        check("含 pilots/agents", "autopilot" in gm and "afk" in gm)

        print("\n[6] 操作托管（在线角色）")
        act = post(base, "/api/dash/action",
                   {"token": token, "charId": cid, "scope": "autopilot",
                    "action": "on", "preset": "balanced"})
        check("开启托管成功", act.get("ok") is True, str(act))
        st2 = get(base, f"/api/dash/state?token={token}&charId={cid}")
        check("状态显示托管开启", st2.get("autopilot", {}).get("active") is True,
              str(st2.get("autopilot", {}))[:200])
        off = post(base, "/api/dash/action",
                   {"token": token, "charId": cid, "scope": "autopilot", "action": "off"})
        check("关闭托管成功", off.get("ok") is True, str(off))
        # 越权操作：用 A 的 token 操作 B 的角色
        cross = post(base, "/api/dash/action",
                     {"token": token, "charId": 999999, "scope": "autopilot", "action": "on"})
        check("越权操作被拒", cross.get("ok") is False, str(cross))

        print("\n[7] 离线修炼后台专用 REST（/api/dash/afk）")
        av = get(base, f"/api/dash/afk?token={token}&charId={cid}")
        check("离线视图可读（角色在线也可）", av.get("ok") is True
              and "balance" in av and "lastReport" in av, str(av)[:160])
        cross2 = get(base, f"/api/dash/afk?token={token}&charId=999999")
        check("离线视图越权被拒", cross2.get("ok") is False, str(cross2))
        badact = post(base, "/api/dash/action",
                      {"token": token, "charId": cid, "scope": "afk", "action": "start"})
        check("后台 afk start 已下线", badact.get("ok") is False, str(badact))

    finally:
        try:
            if "db" in locals(): db.conn.close()
        except Exception: pass
        for w in ws_list:
            try: w.close()
            except Exception: pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        import shutil
        shutil.rmtree(tmp.name, ignore_errors=True)

    print(f"\n结果: {P} PASS / {F} FAIL")
    sys.exit(1 if F else 0)


if __name__ == "__main__":
    main()
