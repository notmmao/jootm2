#!/usr/bin/env python3
"""浏览器端验证：背包更新时新获得物品自动入快捷栏（Playwright，需用 Python 3.9）。

对齐原版 AddItemBag（ClFunc.pas:165-171）：新获得的可用物品（StdMode ≤ 3）
优先放进快捷栏 0..5 空格，快捷栏满才进背包网格——否则背包网格 40 格显示
全满时，买/捡的药水落进不可见的第 41-46 格（服务端 MAXBAGITEM=46 才拒绝）。

服务端用 3.10 启动（项目约定），本脚本自身用装了 Playwright 的 3.9 运行：

    D:/Python39/python.exe scripts/test_bag_ui.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_tmp_bag_ui.db"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base: str, proc, limit: float = 30.0) -> None:
    import urllib.request
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端提前退出")
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2):
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
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_ready(base, proc)
        sys.path.insert(0, str(ROOT / "scripts"))
        from smoke_ws import WS, build_steps
        tag = str(int(time.time()))[-6:]
        steps = build_steps(tag)
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for name in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[name])
            pre.drain(16)
        pre.close()

        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("(u) => window.__conn.send('login', {username: u, password: 'smoke123'})",
                          user)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('enterGame', {name: c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name: c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.self && "
                "Array.isArray(window.__sceneManager.current.self.items)",
                timeout=15000)
            check("进游戏后 PlayScene 就绪且背包已装载",
                  page.evaluate("window.__sceneManager.current.constructor.name") == "PlayScene")

            # 统一用 PlayScene.onMessage 派发合成 bagUpdate，并拦截 conn.send
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({t, m});
                window.__bagSeq = 100000;
                window.__bagGold = 500;
                window.__baseItems = sc.self.items.filter(x => x.slot === undefined);
                window.__baseQuick = (sc.self.quickSlots || [0,0,0,0,0,0]).slice();
            }""")

            def potion(mi):
                return {"makeIndex": mi, "index": 1, "name": "测试金创药",
                        "stdMode": 0, "looks": 0, "ac": 10, "durability": 100,
                        "durabilityMax": 100, "weight": 1}

            def equip(mi):
                return {"makeIndex": mi, "index": 2, "name": "测试木剑",
                        "stdMode": 5, "looks": 0, "weight": 2,
                        "durability": 1000, "durabilityMax": 1000}

            def bag_msg(items, quick=None):
                return {"type": "bagUpdate", "gold": 500, "items": items,
                        "quickSlots": quick or [0, 0, 0, 0, 0, 0]}

            print("\n[1] 登录首包（快照装载）不触发自动填充")
            # 进游戏后的真实首包已把 _bagLoaded 置真；这里用重置标志模拟首包
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc._bagLoaded = false;
                window.__sent.length = 0;
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 500,
                    items: window.__baseItems,
                    quickSlots: [0,0,0,0,0,0]});
            }""")
            check("首包不发送 quickSlot", page.evaluate("window.__sent.length") == 0)
            check("首包后 _bagLoaded 置真",
                  page.evaluate("window.__sceneManager.current._bagLoaded") is True)

            print("\n[2] 新增 3 瓶药水自动进快捷栏 0..2（ClFunc.pas:165-171）")
            page.evaluate("""(ids) => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                const items = window.__baseItems.concat(ids.map(i => ({
                    makeIndex: i, index: 1, name: '测试金创药', stdMode: 0,
                    looks: 0, ac: 10, durability: 100, durabilityMax: 100,
                    weight: 1})));
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 500,
                    items, quickSlots: [0,0,0,0,0,0]});
            }""", [100001, 100002, 100003])
            state = page.evaluate("""() => ({
                quick: window.__sceneManager.current.self.quickSlots,
                sent: window.__sent,
            })""")
            check("快捷栏 0..2 = 新药水", state["quick"][:3] == [100001, 100002, 100003],
                  f"quick={state['quick']}")
            check("发送了 3 条 quickSlot",
                  [s["m"]["makeIndex"] for s in state["sent"] if s["t"] == "quickSlot"]
                  == [100001, 100002, 100003])

            print("\n[3] 药水占满 6 格后，第 7 瓶不再入栏（进背包网格）")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const cur = sc.self.items.filter(x => x.slot === undefined);
                const extra = [100004, 100005, 100006, 100007].map(i => ({
                    makeIndex: i, index: 1, name: '测试金创药', stdMode: 0,
                    looks: 0, ac: 10, durability: 100, durabilityMax: 100,
                    weight: 1}));
                window.__sent.length = 0;
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 500,
                    items: cur.concat(extra), quickSlots: sc.self.quickSlots});
            }""")
            state = page.evaluate("""() => ({
                quick: window.__sceneManager.current.self.quickSlots,
                sent: window.__sent.filter(s => s.t === 'quickSlot').length,
                beltMi: window.__sceneManager.current.self.quickSlots.filter(v => v > 0),
            })""")
            check("快捷栏 6 格全满", len([v for v in state["quick"] if v > 0]) == 6)
            check("只再发送 3 条 quickSlot（100004-100006）", state["sent"] == 3,
                  f"sent={state['sent']}")
            check("100007 未入栏", 100007 not in state["quick"])

            print("\n[4] 新增装备（StdMode=5）不进快捷栏")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const cur = sc.self.items.filter(x => x.slot === undefined);
                window.__sent.length = 0;
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 500,
                    items: cur.concat([{makeIndex: 100008, index: 2,
                        name: '测试木剑', stdMode: 5, looks: 0, weight: 2,
                        durability: 1000, durabilityMax: 1000}]),
                    quickSlots: sc.self.quickSlots});
            }""")
            state = page.evaluate("""() => ({
                quick: window.__sceneManager.current.self.quickSlots,
                sent: window.__sent.length,
            })""")
            check("装备未入栏", 100008 not in state["quick"])
            check("未发送任何 quickSlot", state["sent"] == 0)

            print("\n[5] 快捷栏引用已失效（喝掉的药水）→ 新药水补位该格")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.self.quickSlots[2] = 999999;   // 指向已不存在的物品
                const cur = sc.self.items.filter(x => x.slot === undefined);
                window.__sent.length = 0;
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 500,
                    items: cur.concat([{makeIndex: 100009, index: 1,
                        name: '测试金创药', stdMode: 0, looks: 0, ac: 10,
                        durability: 100, durabilityMax: 100, weight: 1}])});
            }""")
            state = page.evaluate("""() => ({
                quick: window.__sceneManager.current.self.quickSlots,
                sent: window.__sent,
            })""")
            check("失效格被清空后新药水补位 slot2",
                  state["quick"][2] == 100009 and 999999 not in state["quick"],
                  f"quick={state['quick']}")

            check("无页面 JS 错误", not errors, "; ".join(errors[:2]))
            browser.close()
    finally:
        proc.terminate()

    print(f"\n结果: {sum(1 for _, ok, _ in results if ok)} passed, "
          f"{sum(1 for _, ok, _ in results if not ok)} failed")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
