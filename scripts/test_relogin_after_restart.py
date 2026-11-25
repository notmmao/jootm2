# -*- coding: utf-8 -*-
"""服务器重启后登录界面无反应 —— 复现/回归脚本（2026-11-25）。

场景（用户报告）：服务器重启后，客户端窗口退回登录界面，输密码点登录
没有任何反应也没有报错，必须刷新页面才能登录。

复现流程：
  1. 起 pyserver（临时 db）→ Playwright 打开页面到登录界面；
  2. WS 前置注册 player/e2e1234 + 建角 E2ERelaunch；
  3. 杀掉服务器进程（模拟重启）→ 浏览器 WS onclose；
  4. 等 2s 后在**原端口**重启服务器（同一 db）；
  5. 不刷新页面：等前端自动重连（新逻辑 ≤6×3s 重试）→
     直接填表单登录 → 断言进入 SelectCharScene。

判定：
  - 修复前行为：重连不会发生（登录界面掉线被 watchDisconnect 直接忽略），
    send 走死连接静默失败，5. 处登录等待超时 → FAIL；
  - 修复后行为：自动重连 + 登录成功进选角 → PASS。

用 D:/Python39/python.exe 跑（Playwright 只装在 3.9）。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path("D:/work/me/game/jootm2")
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data/_relogin.db"
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def wait_ready(proc, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("服务端进程提前退出")
        try:
            with urllib.request.urlopen(BASE + "/api/status", timeout=1):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def start_server():
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(PORT),
         "--db", str(DB), "--no-bots", "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not wait_ready(proc):
        proc.kill()
        raise RuntimeError("服务端启动超时")
    return proc


def main() -> int:
    for f in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
        if f.exists():
            f.unlink()

    proc = start_server()
    print(f"[setup] 服务器已启动 pid={proc.pid}")

    # WS 前置：注册 GM 账号 + 建角（走 python 直连，不依赖浏览器）
    sys.path.insert(0, str(ROOT / "scripts"))
    from smoke_ws import WS
    pre = WS("127.0.0.1", PORT, "/ws", timeout=1.0)
    for m in ({"type": "register", "username": "player", "password": "e2e1234"},
              {"type": "login", "username": "player", "password": "e2e1234"},
              {"type": "listCharacters"},
              {"type": "createCharacter", "name": "E2ERelaunch", "sex": 0, "hair": 1, "job": 0}):
        pre.send(m)
        pre.drain(16)
    pre.close()

    exit_code = 1
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.__conn && window.__conn.connected === true",
                                   timeout=30000)
            page.wait_for_selector("input[type=text]", timeout=15000)
            check("初始登录界面就绪", True)

            # ---- 杀掉服务器（模拟重启下线）----
            proc.kill()
            proc.wait()
            print("[setup] 服务器已杀掉")
            page.wait_for_timeout(1500)
            disconnected = page.evaluate("() => !window.__conn || !window.__conn.connected")
            check("浏览器感知到断线", disconnected)
            banner = page.evaluate("() => document.body.innerText.includes('断开')")
            check("出现断开提示横幅", banner)

            # ---- 原端口重启 ----
            time.sleep(1.5)
            proc = start_server()
            print(f"[setup] 服务器重启完成 pid={proc.pid}")

            # 不刷新页面，等自动重连（重连循环 6×3s + 服务器回连余量）
            try:
                page.wait_for_function(
                    "window.__conn && window.__conn.connected === true", timeout=45000)
                check("未刷新页面自动重连成功", True)
            except Exception as e:
                check("未刷新页面自动重连成功", False, str(e)[:80])
                browser.close()
                return 1

            # ---- 直接输密码登录（就是用户报的那一步）----
            page.wait_for_selector("input[type=text]", timeout=10000)
            page.fill("input[type=text]", "player")
            page.fill("input[type=password]", "e2e1234")
            page.press("input[type=password]", "Enter")
            try:
                page.wait_for_function(
                    "window.__sceneManager.current && "
                    "window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                    timeout=20000)
                check("重启后不刷新即可登录进选角", True)
            except Exception:
                # 看卡在哪：是否弹了错误框 / 仍停在 LoginScene
                stuck = page.evaluate(
                    "() => window.__sceneManager.current?.constructor.name")
                msg = page.evaluate(
                    "() => document.querySelector('[data-msgdlg]')?.innerText || ''")
                check("重启后不刷新即可登录进选角", False,
                      f"停在 {stuck}，弹窗内容：{msg[:60]}")
            if errors:
                check("页面 JS 无报错", False, "; ".join(errors)[:160])
            else:
                check("页面 JS 无报错", True)
            browser.close()
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    failed = [r for r in results if not r[1]]
    print(f"\n{'='*50}\n共 {len(results)} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
