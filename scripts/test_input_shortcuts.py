#!/usr/bin/env python3
"""浏览器端回归：弹窗输入框与游戏快捷键不冲突（Playwright，Python 3.9）。

背景：组队邀请框输入含数字的名字（如 "t1"）时，数字键 1..6 会被
PlayScene 的全局快捷用药水分支 preventDefault 吃掉，字符打不进去。

验证点：
- 组队邀请框输入 "t1"：字符完整、不触发快捷用药水、回车发出 partyInvite{name:'t1'}
- 交易发起框同样能输入 "t1"（同一处根因）
- 输入框外按 1 仍能快捷用药水（没有过度屏蔽）

用法：D:/Python39/python.exe scripts/test_input_shortcuts.py
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
DB = ROOT / "data" / "_tmp_input_shortcuts.db"


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
            page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("(u) => window.__conn.send('login', {username: u, password: 'smoke123'})", user)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'", timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('enterGame', {name: c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'", timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name: c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.self && Array.isArray(window.__sceneManager.current.self.items)", timeout=15000)

            # 记录上行消息 + 侦测全局快捷键是否被误触发
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({t, m});
                window.__quickUsed = 0;
                const orig = sc._useQuickSlot.bind(sc);
                sc._useQuickSlot = (...a) => { window.__quickUsed++; return orig(...a); };
            }""")
            sent = lambda: page.evaluate("window.__sent")
            quick = lambda: page.evaluate("window.__quickUsed")

            print("\n[1] 组队邀请框输入含数字的名字 t1")
            page.evaluate("() => window.__sceneManager.current.tradePartyUI.toggleParty()")
            page.wait_for_timeout(120)
            inv = page.locator('input[placeholder="输入玩家名邀请入队"]')
            check("邀请输入框存在", inv.count() == 1)
            inv.click()
            page.keyboard.type("t1")
            page.wait_for_timeout(100)
            check("数字 1 能输入（名字完整）", inv.input_value() == "t1",
                  f"实际 {inv.input_value()!r}")
            check("输入时不触发快捷用药水", quick() == 0, f"调用 {quick()} 次")
            page.keyboard.press("Enter")
            page.wait_for_timeout(120)
            check("回车发 partyInvite 带完整名字",
                  any(x["t"] == "partyInvite" and x["m"].get("name") == "t1" for x in sent()),
                  str(sent()[-3:]))

            print("\n[2] 交易发起框同样能输入 t1（同根因）")
            page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(d => d.title === '交易');
                if (b.length) b[0].click();
            }""")
            page.wait_for_timeout(120)
            # 用 DOM 精确取「发起交易」窗里的输入框（placeholder 为空）
            page.evaluate("""() => {
                const inp = [...document.querySelectorAll('input')].find(
                    i => !i.placeholder && i.parentElement && i.parentElement.innerText.includes('发起交易'));
                if (inp) { inp.id = '__tradeNameInput'; }
            }""")
            t_input = page.locator('#__tradeNameInput')
            check("交易输入框存在", t_input.count() == 1)
            t_input.click()
            page.keyboard.type("t1")
            page.wait_for_timeout(100)
            check("交易框数字能输入", t_input.input_value() == "t1",
                  f"实际 {t_input.input_value()!r}")
            check("交易框输入不触发药水", quick() == 0, f"调用 {quick()} 次")

            print("\n[3] 输入框外数字键仍正常快捷用药水")
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            page.keyboard.press("1")
            page.wait_for_timeout(100)
            check("失焦后 1 触发快捷用药水", quick() >= 1, f"调用 {quick()} 次")
            check("无 JS 运行时异常", not errors, str(errors[:2]))

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for p in DB.parent.glob(DB.name + "*"):
            p.unlink()

    bad = [(n, d) for n, ok, d in results if not ok]
    print(f"\n总计 {len(results)} 项，通过 {len(results) - len(bad)}，失败 {len(bad)}")
    for n, d in bad:
        print(f"  FAIL {n} {d}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
