"""回归（2026-11-23 用户反馈）：选角界面「退出」必须真正登出——清除免密 token。

覆盖：
1. 表单真实登录 → 选角 → token 已存、用户名已存
2. 点「退出」热区（真实 click）→ 回登录界面 → token 已清、用户名保留、
   且没有再自动免密登回去（停留在 LoginScene ≥2s）
3. 回登录后手动再登录成功（token 重新签发）→ 证明清 token 没弄坏正常登录
4. ctx 注入 token 冷启动 → 自动免密直达选角（token 登录路径不受影响）
5. 该 ctx 再点退出 → token 清除 → 手动登录恢复
6. 游戏内「退出角色」（_backToSelect 语义 = 保持登录态）不清 token

用 D:/Python39（Playwright）跑，服务端 D:/Python310 + 独立临时 db（data/_exit.db）。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path("D:/work/me/game/jootm2")
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data/_exit.db"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_ready(base: str, proc) -> None:
    for _ in range(300):
        if proc.poll() is not None:
            raise RuntimeError("服务端进程提前退出")
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=1):
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("服务端启动超时")


# ---- 页面内 JS 片段 ---------------------------------------------------------

JS_SCENE = "() => window.__sceneManager && window.__sceneManager.current && window.__sceneManager.current.constructor.name"

# 找选角界面的「退出」热区：SelectCharScene._buildButtons 依次 append 7 个
# cursor:pointer 的 div（66..72），退出是最后一个（index 6）。取其 client 矩形中心。
JS_EXIT_HIT_RECT = """() => {
    const cur = window.__sceneManager.current;
    if (!cur || cur.constructor.name !== 'SelectCharScene' || !cur.root) return null;
    const hots = [...cur.root.children].filter(
        el => el.style && el.style.cursor === 'pointer');
    const hit = hots[hots.length - 1];   // 退出（72）是最后创建的热区
    if (!hit) return null;
    const r = hit.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2, count: hots.length};
}"""


def login_by_form(page, user: str, pwd: str) -> None:
    page.wait_for_selector("input[type=text]", timeout=15000)
    page.fill("input[type=text]", user)
    page.fill("input[type=password]", pwd)
    page.press("input[type=password]", "Enter")


def wait_scene(page, name: str, timeout: int = 25000) -> None:
    page.wait_for_function(
        f"() => window.__sceneManager && window.__sceneManager.current "
        f"&& window.__sceneManager.current.constructor.name === '{name}'",
        timeout=timeout)


def click_exit(page) -> bool:
    rect = page.evaluate(JS_EXIT_HIT_RECT)
    if not rect:
        return False
    page.mouse.click(rect["x"], rect["y"])
    return True


def main() -> int:
    for f in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
        if f.exists():
            f.unlink()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    ws_list = []
    try:
        wait_ready(base, proc)

        # ---- 0) WS 前置：注册 player / 建角 ExitHero ----
        sys.path.insert(0, str(ROOT / "scripts"))
        from smoke_ws import WS
        pre = WS("127.0.0.1", port, "/ws", timeout=1.0)
        ws_list.append(pre)
        for m in ({"type": "register", "username": "player", "password": "e2e1234"},
                  {"type": "login", "username": "player", "password": "e2e1234"},
                  {"type": "listCharacters"},
                  {"type": "createCharacter", "name": "ExitHero", "sex": 0, "hair": 1, "job": 0}):
            pre.send(m)
            pre.drain(16)
        pre.close()
        ws_list.remove(pre)

        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            # ---- 1) 表单登录 → token/用户名落 localStorage ----
            ctx1 = browser.new_context(viewport={"width": 1280, "height": 800})
            page1 = ctx1.new_page()
            page1.on("pageerror", lambda e: errors.append("p1:" + str(e)))
            page1.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page1.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            login_by_form(page1, "player", "e2e1234")
            wait_scene(page1, "SelectCharScene")
            tok = page1.evaluate("() => localStorage.getItem('jootm2.authToken')")
            user = page1.evaluate("() => localStorage.getItem('jootm2.lastUsername')")
            check("1a 登录成功后 token 已签发", bool(tok))
            check("1b 用户名已记住", user == "player", str(user))

            # ---- 2) 点「退出」→ 真登出 ----
            hit = click_exit(page1)
            check("2a 选角界面找到退出热区并点击", hit)
            wait_scene(page1, "LoginScene", timeout=20000)
            tok2 = page1.evaluate("() => localStorage.getItem('jootm2.authToken')")
            user2 = page1.evaluate("() => localStorage.getItem('jootm2.lastUsername')")
            check("2b token 已清除", tok2 is None, str(tok2)[:20])
            check("2c 用户名保留", user2 == "player", str(user2))
            page1.wait_for_timeout(2500)   # 留时间给可能的自动 tokenLogin
            scene = page1.evaluate(JS_SCENE)
            check("2d 未自动免密登回（停留在登录界面）", scene == "LoginScene", str(scene))
            inputs_visible = page1.evaluate(
                "() => !!document.querySelector('input[type=password]')")
            check("2e 登录表单可交互", inputs_visible)

            # ---- 3) 手动再登录成功（清 token 没弄坏正常路径）----
            login_by_form(page1, "player", "e2e1234")
            wait_scene(page1, "SelectCharScene", timeout=20000)
            tok3 = page1.evaluate("() => localStorage.getItem('jootm2.authToken')")
            check("3 手动重登成功且 token 重新签发", bool(tok3) and tok3 != tok)

            # ---- 4) 游戏内「退出角色」不清 token（保持登录态语义）----
            page1.evaluate("() => window.__conn.send('listCharacters')")
            page1.wait_for_timeout(300)
            page1.evaluate("() => window.__conn.send('enterGame', {name:'ExitHero'})")
            wait_scene(page1, "NoticeScene", timeout=15000)
            page1.evaluate("() => window.__conn.send('noticeOk', {name:'ExitHero'})")
            page1.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.self",
                timeout=15000)
            page1.evaluate("() => window.__sceneManager.current._backToSelect()")
            wait_scene(page1, "SelectCharScene", timeout=15000)
            tok4 = page1.evaluate("() => localStorage.getItem('jootm2.authToken')")
            check("4 退出角色回选角保留 token", tok4 == tok3)

            # ---- 5) 注入 token 冷启动自动免密 → 退出清 token → 手动登录恢复 ----
            ctx2 = browser.new_context(viewport={"width": 1280, "height": 800})
            page2 = ctx2.new_page()
            page2.on("pageerror", lambda e: errors.append("p2:" + str(e)))
            page2.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page2.evaluate(
                f"() => localStorage.setItem('jootm2.authToken', {json.dumps(tok3)});")
            page2.evaluate("() => localStorage.setItem('jootm2.lastUsername', 'player');")
            page2.reload(wait_until="domcontentloaded")
            page2.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            wait_scene(page2, "SelectCharScene", timeout=25000)
            check("5a 冷启动 token 自动免密直达选角", True)
            hit2 = click_exit(page2)
            check("5b 退出热区可点", hit2)
            wait_scene(page2, "LoginScene", timeout=20000)
            tok5 = page2.evaluate("() => localStorage.getItem('jootm2.authToken')")
            check("5c 退出后 token 已清", tok5 is None)
            page2.wait_for_timeout(2000)
            check("5d 未再自动登回", page2.evaluate(JS_SCENE) == "LoginScene")
            login_by_form(page2, "player", "e2e1234")
            wait_scene(page2, "SelectCharScene", timeout=20000)
            check("5e 清 token 后手动登录恢复正常", True)

            check("Z 无页面 JS 报错", not errors, "; ".join(errors[:2]))
            browser.close()
        return 0 if all(ok for _, ok, _ in results) else 1
    finally:
        for ws in ws_list:
            try:
                ws.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    code = main()
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结论: {passed}/{total} 通过" + ("" if passed == total else "  ← 有失败项"))
    sys.exit(code)
