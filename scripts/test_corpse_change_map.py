#!/usr/bin/env python3
"""浏览器端回归：换图/传送后尸体不会「站起来」（Playwright，Python 3.9）。

历史：23117b1 修过登录路径的尸体帧还原，但游戏内 `changeMap` 消息的实体
重建循环（PlayScene.mjs case 'changeMap'）一直是裸 set——§164 把死亡动画
泛化到所有怪（baf5305）后，走门换图再回来、或 GM/回城卷传送，尸体全按
站立帧画（brief 里 corpse:true 没被翻译成 _corpse/_action='death'）。

    D:/Python39/python.exe scripts/test_corpse_change_map.py
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

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_tmp_corpse_cmap.db"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base: str, proc, limit: float = 30.0) -> None:
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端提前退出")
        try:
            urllib.request.urlopen(base + "/api/status", timeout=2)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("服务端启动超时")


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    port = free_port()
    base = "http://127.0.0.1:%d" % port
    for p in DB.parent.glob(DB.name + "*"):
        p.unlink()
    env = os.environ.copy()
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index", "--no-bots"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    try:
        wait_ready(base, proc)
        user = "player"          # GM_ACCOUNTS 内含 player：@刷怪/@移动 可用
        char = "CorpseGM"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("""(u) => {
                window.__conn.send('register', {username:u, password:'smoke123'});
                window.__conn.send('login', {username:u, password:'smoke123'});
            }""", user)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('createCharacter', "
                          "{name:c, sex:0, hair:1, job:0})", char)
            page.wait_for_timeout(500)
            page.evaluate("(c) => window.__conn.send('enterGame', {name:c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name:c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'PlayScene'",
                timeout=25000)

            page.evaluate("""() => {
                window.__deaths = [];
                window.__conn.on('*', m => {
                    if (m.type === 'death') window.__deaths.push(m);
                });
            }""")

            print("\n=== 1. 刷怪并击杀出一具尸体 ===")
            page.evaluate("() => window.__conn.send('say', {text:'@刷怪 鸡 4'})")
            page.wait_for_timeout(800)
            page.evaluate("() => document.getElementById('autoPilotBtn').click()")
            page.wait_for_function("window.__deaths && window.__deaths.length > 0",
                                   timeout=60000)
            corpse_id = page.evaluate("window.__deaths[0].id")
            info = page.evaluate("""(id) => {
                const s = window.__sceneManager.current;
                const e = s.entities.get(id);
                return e ? {x: e.x, y: e.y, action: e._action,
                            corpse: !!e._corpse} : null;
            }""", corpse_id)
            check("尸体在场且定格 death", bool(info) and info.get("corpse")
                  and info.get("action") in ("die", "death"), str(info))
            cx, cy = (info or {}).get("x", 0), (info or {}).get("y", 0)

            print("\n=== 2. 同图传送离开再回原点（changeMap 快照路径） ===")
            page.evaluate("""() => window.__conn.send('say',
                {text:'@移动 100 100'})""")
            page.wait_for_timeout(1200)
            page.evaluate("""([x, y]) => window.__conn.send('say',
                {text:`@移动 ${x} ${y}`})""", [cx, cy])
            page.wait_for_timeout(1200)
            info2 = page.evaluate("""(id) => {
                const s = window.__sceneManager.current;
                const e = s.entities.get(id);
                if (!e) return {exists: false};
                const sp = s._sprite(e);
                const spec = sp._frameSpec(Date.now());
                return {exists: true, action: e._action, corpse: !!e._corpse,
                        specAction: !!spec};
            }""", corpse_id)
            check("传送回来尸体仍存在", info2.get("exists"))
            check("换图快照还原尸体：_corpse=true 且 action=death（不是 stand）",
                  info2.get("corpse") and info2.get("action") == "death", str(info2))

            print("\n=== 3. 关闭托管避免干扰，二次换图复验 ===")
            page.evaluate("() => window.__conn.send('autopilot', {action:'off'})")
            page.wait_for_timeout(300)
            page.evaluate("() => window.__conn.send('say', {text:'@移动 120 120'})")
            page.wait_for_timeout(1000)
            page.evaluate("""([x, y]) => window.__conn.send('say',
                {text:`@移动 ${x} ${y}`})""", [cx, cy])
            page.wait_for_timeout(1000)
            info3 = page.evaluate("""(id) => {
                const s = window.__sceneManager.current;
                const e = s.entities.get(id);
                return e ? {action: e._action, corpse: !!e._corpse} : null;
            }""", corpse_id)
            check("二次换图后尸体仍是 death 定格", bool(info3)
                  and info3.get("corpse") and info3.get("action") == "death",
                  str(info3))
            check("无页面 JS 异常", not errors, "; ".join(errors[:2]))
            browser.close()
    finally:
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
