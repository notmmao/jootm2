"""E2E 验证（2026-11-23 八项改动）：用 D:/Python39（Playwright）跑，服务端用 D:/Python310。

覆盖：
1. 手动登录流程（真实输入框），token 落 localStorage
2. 进入游戏后名字风格默认=原版（_hudMode.nameStyle）
3. GM @刷怪 稻草人×5（elite.rate=1.0 临时配置 → 全精英），选中精英后
   实体带 elite 标记、名字前缀「精英·」渲染不报错，截图
4. Esc 系统菜单点「名字风格」→ 切到当前改良 → 截图 → 再切回
5. 新浏览器上下文带 token 冷启动 → 自动免密登录直达选人场景（截图）
6. afk.html 挂机后台：token 连接 → 刷新状态 → 开启托管 → GM 全服视图（截图）

截图存到 data/e2e_shots/。用独立临时 db（data/_e2e.db）与 elite 专用配置。
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
SHOTS = ROOT / "data/e2e_shots"
DB = ROOT / "data/_e2e.db"
ELITE_CFG = ROOT / "data/_e2e_elite.properties"

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


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    for f in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
        if f.exists():
            f.unlink()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--config", str(ELITE_CFG), "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    ws_list = []
    try:
        wait_ready(base, proc)

        # ---- 0) WS 前置：注册 GM 账号 player + 建角（GM_ACCOUNTS 按用户名判定）----
        sys.path.insert(0, str(ROOT / "scripts"))
        from smoke_ws import WS
        pre = WS("127.0.0.1", port, "/ws", timeout=1.0)
        ws_list.append(pre)
        for m in ({"type": "register", "username": "player", "password": "e2e1234"},
                  {"type": "login", "username": "player", "password": "e2e1234"},
                  {"type": "listCharacters"},
                  {"type": "createCharacter", "name": "E2EHero", "sex": 0, "hair": 1, "job": 0}):
            pre.send(m)
            msgs = pre.drain(16)
            if m["type"] == "register":
                check("WS 注册 GM 账号 player",
                      any(x.get("type") == "registerResult" and x.get("ok") for x in msgs))
        pre.close()
        ws_list.remove(pre)

        errors: list[str] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            # ---- 1) 真实输入框登录 + 进入游戏 ----
            ctx1 = browser.new_context(viewport={"width": 1280, "height": 800})
            page1 = ctx1.new_page()
            page1.on("pageerror", lambda e: errors.append("p1:" + str(e)))
            page1.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page1.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page1.wait_for_selector("input[type=text]", timeout=15000)
            page1.fill("input[type=text]", "player")
            page1.fill("input[type=password]", "e2e1234")
            page1.press("input[type=password]", "Enter")
            page1.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=25000)
            page1.screenshot(path=str(SHOTS / "01_select_char_after_login.png"))
            tok = page1.evaluate("() => localStorage.getItem('jootm2.authToken')")
            check("登录成功且 token 已存 localStorage", bool(tok), (tok or "")[:24] + "…")
            saved_user = page1.evaluate("() => localStorage.getItem('jootm2.lastUsername')")
            check("用户名已保存", saved_user == "player", str(saved_user))

            page1.evaluate("() => window.__conn.send('listCharacters')")
            page1.wait_for_timeout(300)
            page1.evaluate("() => window.__conn.send('enterGame', {name:'E2EHero'})")
            page1.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page1.evaluate("() => window.__conn.send('noticeOk', {name:'E2EHero'})")
            page1.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.self && Array.isArray(window.__sceneManager.current.self.items)",
                timeout=15000)
            page1.wait_for_timeout(2500)   # 地面缓存预取 + 实体到位

            # ---- 2) 默认原版名字风格 ----
            style = page1.evaluate("() => window.__sceneManager.current._hudMode.nameStyle")
            check("名字风格默认=原版(original)", style == "original", str(style))
            page1.screenshot(path=str(SHOTS / "02_play_original_style.png"))

            # ---- 3) GM @刷怪（elite.rate=1.0 → 全精英），选中精英验证 ----
            page1.evaluate("() => window.__conn.send('say', {text:'@刷怪 稻草人 5'})")
            page1.wait_for_timeout(1500)
            elite = page1.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const p = sc.self;
                for (const e of sc.entities.values()) {
                    if (e.type === 'monster' && e.elite && e.hp > 0
                        && Math.abs(e.x - p.x) <= 6 && Math.abs(e.y - p.y) <= 6) {
                        sc.magicTarget = e;
                        return {id: e.id, name: e.name, elite: e.elite,
                                hp: e.hp, maxHp: e.maxHp, x: e.x, y: e.y};
                    }
                }
                return null;
            }""")
            check("@刷怪 生成精英怪（elite 标记下发到前端）", elite is not None,
                  json.dumps(elite, ensure_ascii=False)[:120] if elite else "视野内无精英")
            if elite:
                page1.wait_for_timeout(700)
                page1.screenshot(path=str(SHOTS / "03_elite_selected_original.png"))
                still = page1.evaluate(
                    "(id) => { const e = window.__sceneManager.current.entities.get(id);"
                    "return e ? !!e.elite : false; }", elite["id"])
                check("精英实体持续存在且标记正确", still)

            # ---- 4) Esc 菜单切换名字风格 ----
            page1.keyboard.press("Escape")
            page1.wait_for_timeout(400)
            btn_orig = page1.locator("text=名字风格：原版")
            check("系统菜单出现「名字风格：原版」按钮", btn_orig.count() > 0)
            page1.screenshot(path=str(SHOTS / "04_sysmenu_name_style_button.png"))
            if btn_orig.count() > 0:
                btn_orig.first.click()
                page1.wait_for_timeout(300)
                style2 = page1.evaluate(
                    "() => window.__sceneManager.current._hudMode.nameStyle")
                ls2 = page1.evaluate("() => localStorage.getItem('jootm2.nameStyle')")
                check("点击后切到当前改良", style2 == "current" and ls2 == "current",
                      f"{style2}/{ls2}")
                page1.wait_for_timeout(700)
                page1.screenshot(path=str(SHOTS / "05_play_current_style.png"))
                btn_cur = page1.locator("text=名字风格：当前改良")
                check("按钮文案随状态更新", btn_cur.count() > 0)
                if btn_cur.count() > 0:
                    btn_cur.first.click()   # 切回原版，保持默认观感
                    page1.wait_for_timeout(200)
            page1.keyboard.press("Escape")

            # ---- 5) 新上下文带 token 冷启动 → 自动免密登录 ----
            ctx2 = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx2.add_init_script(
                f"localStorage.setItem('jootm2.authToken', {json.dumps(tok)});"
                "localStorage.setItem('jootm2.lastUsername', 'player');")
            page2 = ctx2.new_page()
            page2.on("pageerror", lambda e: errors.append("p2:" + str(e)))
            page2.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            try:
                page2.wait_for_function(
                    "window.__sceneManager && window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                    timeout=20000)
                auto_ok = True
            except Exception:
                auto_ok = False
            check("token 冷启动自动免密登录直达选人场景", auto_ok)
            dlg = page2.evaluate("""() => {
                for (const d of document.querySelectorAll('div')) {
                    if (d.textContent.includes('ID不存在') || d.textContent.includes('账号或密码'))
                        return d.textContent.slice(0, 40);
                }
                return '';
            }""")
            check("自动登录失败弹窗未出现", dlg == "", dlg)
            page2.screenshot(path=str(SHOTS / "06_auto_login_token.png"))

            # ---- 6) 挂机后台 afk.html ----
            page3 = ctx2.new_page()
            page3.on("pageerror", lambda e: errors.append("p3:" + str(e)))
            page3.goto(base + "/afk.html", wait_until="domcontentloaded", timeout=15000)
            page3.wait_for_timeout(500)
            auto_tok_filled = page3.input_value("#token")
            check("afk.html 自动带入 localStorage token", auto_tok_filled == tok)
            page3.click("#authBtn")
            page3.wait_for_function(
                "() => !document.getElementById('dashboard').classList.contains('hidden')",
                timeout=10000)
            conn_msg = page3.inner_text("#connMsg")
            check("后台连接成功且显示 GM 身份", "已连接" in conn_msg and "GM" in conn_msg, conn_msg)
            opt = page3.evaluate("""() => {
                const s = document.getElementById('charSel');
                return {n: s.options.length, text: s.selectedOptions[0] ? s.selectedOptions[0].textContent : ''};
            }""")
            check("角色下拉含在线角色 E2EHero",
                  opt["n"] >= 1 and "E2EHero" in opt["text"], opt["text"])
            page3.click("#refreshBtn")
            page3.wait_for_timeout(800)
            dash_msg = page3.inner_text("#dashMsg")
            check("刷新状态成功", "实时状态" in dash_msg, dash_msg)
            page3.screenshot(path=str(SHOTS / "07_afk_dashboard.png"))

            page3.select_option("#apPreset", index=0)
            page3.click("#apOn")
            page3.wait_for_timeout(1200)
            ap_active = page3.evaluate(
                "() => document.getElementById('apStats').innerText.includes('开启中')")
            check("网页后台开启托管成功（状态区显示开启中）", ap_active)
            page3.screenshot(path=str(SHOTS / "08_afk_autopilot_on.png"))

            page3.click("#gmBtn")
            page3.wait_for_timeout(1000)
            rows = page3.evaluate(
                "() => document.getElementById('gmTable').querySelectorAll('tr').length")
            check("GM 全服视图有数据行", rows >= 1, f"{rows} 行")
            page3.screenshot(path=str(SHOTS / "09_gm_all_view.png"))

            # ---- 页面无 JS 错误 ----
            check("E2E 全程无页面 JS 异常", not errors, "; ".join(errors)[:200])

            browser.close()

        # 托管开启后服务端 tick 跑一小段再收尾，确认不崩
        time.sleep(2)
        with urllib.request.urlopen(base + "/api/status", timeout=3) as r:
            check("收尾后 /api/status 仍 200", r.status == 200)

    finally:
        for w in ws_list:
            try:
                w.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n结果: {len(results) - len(fails)} PASS / {len(fails)} FAIL")
    if fails:
        print("FAIL 列表: " + ", ".join(fails))
    print("截图目录: " + str(SHOTS))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
