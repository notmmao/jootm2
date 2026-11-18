#!/usr/bin/env python3
"""浏览器端验证：在线托管开关 + HUD 的真实渲染（Playwright，需用 Python 3.9）。

服务端仍强制用 3.10 启动（本项目约定），本脚本自身用装了 Playwright 的
D:/Python39/python.exe 运行：

    D:/Python39/python.exe scripts/test_autopilot_ui.py

产出 docs/autopilot-hud.png（托管开启时的画面截图）。
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
DB = ROOT / "data" / "_tmp_autopilot_ui.db"
SHOT = ROOT / "docs" / "autopilot-hud.png"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ready(base: str, proc, limit: float = 30.0) -> None:
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端提前退出")
        try:
            api(base, "/api/status")
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
         "--db", str(DB), "--no-resource-index",
         "--no-bots"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    try:
        wait_ready(base, proc)
        sys.path.insert(0, str(ROOT / "scripts"))
        from smoke_ws import WS, build_steps
        tag = str(int(time.time()))[-6:]
        steps = build_steps(tag)
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        # 预建账号与角色：绕开登录场景「注册成功自动登录」与 3s 开门动画前
        # 消息丢失的问题，浏览器只走 login → 选角 → 公告 → 进游戏。
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
            page.evaluate("""() => {
                window.__msgs = [];
                window.__conn.on('*', m => window.__msgs.push(m.type));
            }""")
            page.evaluate("(u) => window.__conn.send('login', {username: u, password: 'smoke123'})",
                          user)
            # 登录成功会播 10 帧×300ms 开门 + 淡出，之后才切到选角场景
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
            diag = page.evaluate("""() => ({
                scene: window.__sceneManager.current &&
                       window.__sceneManager.current.constructor.name,
                msgs: (window.__msgs || []).slice(-25),
            })""")
            print("  [diag] 进游戏链路:", diag)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'PlayScene'",
                timeout=25000)
            page.wait_for_selector("#autoPilotBtn", timeout=25000)
            check("进入游戏且 HUD 开关已渲染", True)

            print("\n=== 托管开关 / HUD ===")
            page.click("#autoPilotBtn")
            page.wait_for_function(
                "document.querySelector('#autoPilotStatus').style.display === 'block'",
                timeout=10000)
            text_on = page.inner_text("#autoPilotStatus")
            check("开启后显示状态条", "托管中" in text_on, text_on)
            check("服务端已建立托管实例", api(base, "/api/autopilot")["count"] == 1)
            # 状态条可见时截图（服务端已开始打怪，状态文案会带目标名）
            page.wait_for_timeout(1800)
            text_busy = page.inner_text("#autoPilotStatus")
            try:
                page.locator("#stage").screenshot(path=str(SHOT))
            except Exception:
                page.screenshot(path=str(SHOT))
            check("截图已生成", SHOT.exists(), str(SHOT))
            check("状态跟随 tick 更新", text_busy != "" and "托管中" in text_busy,
                  text_busy)

            print("\n=== 「不挖肉」勾选框 ===")
            page.wait_for_selector("#autoPilotNoButch", timeout=5000)
            check("不挖肉勾选框已渲染", True)
            page.check("#autoPilotNoButch")
            page.wait_for_timeout(300)
            check("勾选后状态条显示不挖肉",
                  "不挖肉" in page.inner_text("#autoPilotStatus"),
                  page.inner_text("#autoPilotStatus"))
            check("勾选已持久化到 localStorage",
                  page.evaluate("localStorage.getItem('jootm2.autopilotNoButch')") == "1")
            check("服务端已收到不挖肉选项",
                  api(base, "/api/autopilot")["pilots"][0]["noButch"] is True)
            page.uncheck("#autoPilotNoButch")
            page.wait_for_timeout(300)
            check("取消勾选后恢复挖肉文案",
                  "挖肉" in page.inner_text("#autoPilotStatus"),
                  page.inner_text("#autoPilotStatus"))

            print("\n=== 手动接管 / 恢复 / 关闭 ===")
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("1")       # 游戏动作键 → 立即接管
            page.wait_for_function(
                "document.querySelector('#autoPilotStatus').textContent.includes('已暂停')",
                timeout=10000)
            check("动作键立即接管", "已暂停" in page.inner_text("#autoPilotStatus"),
                  page.inner_text("#autoPilotStatus"))
            page.keyboard.press("p")
            page.wait_for_function(
                "document.querySelector('#autoPilotStatus').textContent.includes('托管中')",
                timeout=10000)
            check("按 P 恢复托管", True)
            page.keyboard.press("p")
            page.wait_for_function(
                "document.querySelector('#autoPilotStatus').style.display === 'none'",
                timeout=10000)
            check("按 P 关闭后状态条隐藏",
                  api(base, "/api/autopilot")["count"] == 0)
            if errors:
                print("  [页面 JS 异常] " + " | ".join(errors[:3]))
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
