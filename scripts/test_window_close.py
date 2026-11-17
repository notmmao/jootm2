#!/usr/bin/env python3
"""浏览器端回归：弹窗×关闭按钮（Playwright，Python 3.9）。

历史根因：在线玩家窗与查看装备窗的 _window onClose 回调只把引用置 null、没有
remove DOM。点×后窗口仍在屏幕上、引用却丢了，再点按钮会新建第二个窗口，
旧窗永远关不掉（严重 bug）。

    D:/Python39/python.exe scripts/test_window_close.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_tmp_window_close.db"


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
        steps = build_steps(str(int(time.time()))[-6:])
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for n in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[n]); pre.drain(16)
        pre.close()

        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("(u) => window.__conn.send('login', {username:u, password:'smoke123'})", user)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'", timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('enterGame', {name:c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'", timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name:c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.self && Array.isArray(window.__sceneManager.current.self.items)", timeout=15000)

            def win_count(title):
                return page.evaluate("""(t) => [...document.querySelectorAll('div')]
                    .filter(d => d.style.zIndex === '30' && d.querySelector('div') &&
                        d.querySelector('div').textContent === t).length""", title)

            print("\n[1] 在线玩家窗：点×关闭且 DOM 真移除")
            page.evaluate("() => window.__sceneManager.current.tradePartyUI.toggleOnlineList()")
            page.wait_for_timeout(120)
            check("在线窗打开", win_count("在线玩家") == 1, f"数量 {win_count('在线玩家')}")
            # 点标题栏里的 ×
            page.evaluate("""() => {
                const dlg = [...document.querySelectorAll('div')].find(d =>
                    d.style.zIndex === '30' && d.textContent.includes('在线玩家') &&
                    d.textContent.includes('×'));
                const x = [...dlg.querySelectorAll('div')].find(d => d.textContent === '×');
                x.click();
            }""")
            page.wait_for_timeout(120)
            check("点×后在线窗 DOM 消失", win_count("在线玩家") == 0, f"数量 {win_count('在线玩家')}")
            check("引用已清空", page.evaluate("!window.__sceneManager.current.tradePartyUI._onlineWin"))

            print("\n[2] 反复开关：不残留多个窗口")
            for i in range(3):
                page.evaluate("() => window.__sceneManager.current.tradePartyUI.toggleOnlineList()")
                page.wait_for_timeout(80)
                check(f"第{i+1}次打开只有 1 个窗", win_count("在线玩家") == 1,
                      f"数量 {win_count('在线玩家')}")
                page.evaluate("""() => {
                    const dlg = [...document.querySelectorAll('div')].find(d =>
                        d.style.zIndex === '30' && d.textContent.includes('在线玩家') &&
                        d.textContent.includes('×'));
                    const x = [...dlg.querySelectorAll('div')].find(d => d.textContent === '×');
                    x.click();
                }""")
                page.wait_for_timeout(80)
                check(f"第{i+1}次点×后关闭", win_count("在线玩家") == 0)

            print("\n[3] 查看装备窗：点原版关闭键也能关闭")
            page.evaluate("""() => window.__sceneManager.current.onMessage('userState', {
                type:'userState', id:88, name:'路人', level:20, job:0, sex:0, hair:2,
                dress:0, weapon:0, items:[]})""")
            page.wait_for_timeout(150)
            check("装备窗打开", page.evaluate("""() =>
                !!window.__sceneManager.current.tradePartyUI._inspectWin &&
                window.__sceneManager.current.tradePartyUI._inspectWin.textContent.includes('路人')"""))
            # 原版 DUserState1 关闭键是 Prguse[371] 图片按钮 @ (8,39)，无文字 ×
            page.evaluate("""() => {
                const w = window.__sceneManager.current.tradePartyUI._inspectWin;
                const close = [...w.children].find(d => d.style.left === '8px' && d.style.top === '39px');
                close.click();
            }""")
            page.wait_for_timeout(120)
            check("点关闭键后装备窗 DOM 消失", page.evaluate("""() =>
                !window.__sceneManager.current.tradePartyUI._inspectWin &&
                ![...document.querySelectorAll('div')].some(d =>
                    d.style.zIndex === '30' && d.textContent.includes('路人'))"""))
            check("无页面 JS 异常", not errors, "; ".join(errors[:2]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for p in DB.parent.glob(DB.name + "*"):
            p.unlink()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n结论: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
