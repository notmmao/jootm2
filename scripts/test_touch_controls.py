#!/usr/bin/env python3
"""移动端触屏控制回归（Playwright，Python 3.9）。

覆盖：
- 桌面 context 不创建虚拟 UI（零 DOM、零 body.touch），键鼠流程不受影响；
- 移动触屏 context 自动出现摇杆 + 动作按钮；?touch=0/1 强制覆盖生效；
- 摇杆 8 方向映射、走/跑判定、复用 move 发送节流；
- 攻击/拾取/技能/药水/背包/地图/菜单按钮接到 PlayScene 既有动作；
- 全程无 JS 异常。

用法：D:/Python39/python.exe scripts/test_touch_controls.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_tmp_touch_controls.db"
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")


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


def prepare_accounts(port: int, count: int):
    sys.path.insert(0, str(ROOT / "scripts"))
    from smoke_ws import WS, build_steps
    out = []
    for i in range(count):
        steps = build_steps(str(int(time.time()))[-5:] + "t" + str(i))
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for name in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[name])
            pre.drain(16)
        pre.close()
        out.append((steps["register"]["username"], steps["createCharacter"]["name"]))
    return out


def enter_game(page, base: str, user: str, char: str, suffix: str = "") -> None:
    page.goto(base + "/" + suffix, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=30000)
    page.evaluate("(u) => window.__conn.send('login', {username:u, password:'smoke123'})", user)
    page.wait_for_function(
        "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
        timeout=25000)
    page.evaluate("() => window.__conn.send('listCharacters')")
    page.wait_for_timeout(300)
    page.evaluate("(c) => window.__conn.send('enterGame', {name:c})", char)
    page.wait_for_function(
        "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'",
        timeout=15000)
    page.evaluate("(c) => window.__conn.send('noticeOk', {name:c})", char)
    page.wait_for_function(
        "window.__sceneManager.current && window.__sceneManager.current.self"
        " && Array.isArray(window.__sceneManager.current.self.items)", timeout=15000)


def main() -> int:
    results = []

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
        accounts = prepare_accounts(port, 4)
        errors = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            desktop = browser.new_context(viewport={"width": 1280, "height": 800})
            mobile = browser.new_context(viewport={"width": 844, "height": 390},
                                         is_mobile=True, has_touch=True, user_agent=UA_MOBILE)

            print("\n[1] 桌面默认：不出现任何虚拟控制 UI")
            p = desktop.new_page()
            p.on("pageerror", lambda e: errors.append(str(e)))
            enter_game(p, base, *accounts[0])
            r = p.evaluate("""() => ({
                enabled: !!(window.__touchControls && window.__touchControls.enabled),
                ui: !!document.querySelector('#touch-ui'),
                body: document.body.classList.contains('touch'),
                canClickCanvas: !!document.getElementById('screen'),
            })""")
            check("桌面端禁用触屏控制", r["enabled"] is False, str(r))
            check("桌面端无 #touch-ui", r["ui"] is False)
            check("桌面端无 body.touch", r["body"] is False)
            p.close()

            print("\n[2] 移动触屏：自动出现摇杆与按钮；?touch=0 可关闭")
            p = mobile.new_page()
            p.on("pageerror", lambda e: errors.append(str(e)))
            enter_game(p, base, *accounts[1])
            r = p.evaluate("""() => ({
                enabled: !!(window.__touchControls && window.__touchControls.enabled),
                ui: !!document.querySelector('#touch-ui'),
                body: document.body.classList.contains('touch'),
                stick: getComputedStyle(document.querySelector('#touch-stick')).display,
            })""")
            check("移动设备自动启用", r["enabled"] is True, str(r))
            check("移动端生成 #touch-ui", r["ui"] is True)
            check("移动端 body.touch", r["body"] is True)
            check("摇杆初始隐藏", r["stick"] == "none", r["stick"])
            for sel in ("#tc-attack", "#tc-pick", "#tc-potion", "#tc-s1", "#tc-bag", "#tc-map", "#tc-menu"):
                check("按钮可见 " + sel, p.locator(sel).is_visible())

            # 手机横屏 scale≈0.65：触点必须经 toLocal 换算，否则方向会偏
            p.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = []; sc.conn.send = (t, m) => window.__sent.push({t, m});
                sc._lastMoveAt = 0; sc._cancelNav(true);
            }""")
            mbox = p.locator("#touch-move-zone").bounding_box()
            mx, my = mbox["x"] + mbox["width"] / 2, mbox["y"] + mbox["height"] / 2
            zone = p.locator("#touch-move-zone")
            zone.dispatch_event("pointerdown", {
                "pointerId": 2, "pointerType": "touch", "isPrimary": True,
                "clientX": mx, "clientY": my, "bubbles": True})
            zone.dispatch_event("pointermove", {
                "pointerId": 2, "pointerType": "touch", "isPrimary": True,
                "clientX": mx + 45, "clientY": my, "bubbles": True})
            mres = p.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc._tickTouchMove(performance.now());
                return { scale: document.getElementById('stage').getBoundingClientRect().width / 800,
                         dir: sc.touch.direction(), sent: window.__sent.slice() };
            }""")
            check("手机缩放下摇杆方向正确",
                  mres["dir"] == 2 and any(x["t"] == "move" and x["m"]["dir"] == 2
                                           for x in mres["sent"]), str(mres))
            p.close()

            p = mobile.new_page()
            p.on("pageerror", lambda e: errors.append(str(e)))
            enter_game(p, base, *accounts[2], suffix="?touch=0")
            r = p.evaluate("""() => ({
                enabled: !!(window.__touchControls && window.__touchControls.enabled),
                ui: !!document.querySelector('#touch-ui'),
            })""")
            check("?touch=0 强制关闭", r["enabled"] is False and r["ui"] is False, str(r))
            p.close()

            print("\n[3] ?touch=1 桌面强开，验证摇杆与按钮接线")
            p = desktop.new_page()
            p.on("pageerror", lambda e: errors.append(str(e)))
            enter_game(p, base, *accounts[3], suffix="?touch=1")
            check("?touch=1 强制开启",
                  p.evaluate("window.__touchControls.enabled === true && !!document.querySelector('#touch-ui')"))

            # 装假数据 + 接管 send，全部在浏览器里同步完成，避免 rAF 插步
            p.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.self.x = 10; sc.self.y = 10; sc.self.dir = 2;
                sc._selfDead = false; sc._cancelNav(true);
                sc.entities.set(9001, {id:9001, type:'monster', x:11, y:10, hp:50, dead:false});
                sc.groundItems.set(9002, {id:9002, x:10, y:10});
                sc._lastHitTime = 0; sc.self._action = 'stand';
                sc._lastMoveAt = 0;
                window.__sent = []; sc.conn.send = (t, m) => window.__sent.push({t, m});
            }""")

            # 8 方向映射（直接设模拟量，绕过指针事件，纯映射验证）
            dirs = p.evaluate("""() => {
                const t = window.__touchControls;
                const cases = [[1,0],[0.7,0.7],[0,1],[-0.7,0.7],[-1,0],[-0.7,-0.7],[0,-1],[0.7,-0.7]];
                return cases.map(([x,y]) => { t.dx = x; t.dy = y; return t.direction(); });
            }""")
            check("摇杆 8 方向映射", dirs == [2, 3, 4, 5, 6, 7, 0, 1], str(dirs))
            run = p.evaluate("""() => {
                const t = window.__touchControls;
                t.dx = 0.95; t.dy = 0; return { running: t.running(), mag: t.getAxis().mag };
            }""")
            check("满推判定为跑", run["running"] is True and 0.9 < run["mag"] <= 1, str(run))
            walk = p.evaluate("""() => {
                const t = window.__touchControls;
                t.dx = 0.45; t.dy = 0; return { running: t.running(), dir: t.direction() };
            }""")
            check("半推判定为走", walk["running"] is False and walk["dir"] == 2, str(walk))

            # 真实 Pointer Events：在摇杆区域按下并右推，复用主循环节流发包
            zone = p.locator("#touch-move-zone")
            box = zone.bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            zone.dispatch_event("pointerdown", {
                "pointerId": 1, "pointerType": "touch", "isPrimary": True,
                "clientX": cx, "clientY": cy, "bubbles": True})
            zone.dispatch_event("pointermove", {
                "pointerId": 1, "pointerType": "touch", "isPrimary": True,
                "clientX": cx + 70, "clientY": cy, "bubbles": True})
            mv = p.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0; sc._lastMoveAt = 0;
                sc._tickTouchMove(performance.now());
                return { dir: sc.touch.direction(), run: sc.touch.running(), sent: window.__sent.slice() };
            }""")
            check("指针驱动摇杆有模拟量", mv["dir"] >= 0, str(mv))
            moves = [x for x in mv["sent"] if x["t"] == "move"]
            check("摇杆发出 move 包", len(moves) == 1, str(mv["sent"]))
            if moves:
                check("move 方向/跑与摇杆一致",
                      moves[0]["m"]["dir"] == mv["dir"] and moves[0]["m"]["run"] == mv["run"],
                      str(moves[0]))
            zone.dispatch_event("pointerup", {
                "pointerId": 1, "pointerType": "touch", "clientX": cx + 70, "clientY": cy,
                "bubbles": True})
            check("松开摇杆归零",
                  p.evaluate("window.__touchControls.getAxis().mag === 0 && window.__touchControls.direction() === -1"))

            print("\n[4] 动作按钮接到既有 PlayScene 方法")
            p.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0; sc.chaseTargetId = null;
                sc._lastHitTime = 0; sc.self._action = 'stand';
            }""")
            p.locator("#tc-attack").click()
            atk = p.evaluate("""() => ({ sent: window.__sent.slice(), lock: window.__sceneManager.current.chaseTargetId })""")
            check("攻击键锁定最近怪", atk["lock"] == 9001, str(atk["lock"]))
            check("攻击键发出 attack 包", any(x["t"] == "attack" for x in atk["sent"]), str(atk["sent"]))

            p.evaluate("window.__sent.length = 0")
            p.locator("#tc-pick").click()
            picks = p.evaluate("window.__sent.filter(x => x.t === 'pickup')")
            check("拾取键发出 pickup 包",
                  len(picks) == 1 and picks[0]["m"]["itemId"] == 9002, str(picks))

            p.evaluate("""() => {
                window.__casts = []; window.__belts = []; window.__ui = [];
                const sc = window.__sceneManager.current;
                sc._castFromSlot = (n) => window.__casts.push(n);
                sc._useQuickSlot = (n) => window.__belts.push(n);
                sc._toggleBag = () => window.__ui.push('bag');
                sc._cycleMapView = () => window.__ui.push('map');
                sc._toggleSystemMenu = () => window.__ui.push('menu');
            }""")
            for i in (1, 2, 3):
                p.locator("#tc-s%d" % i).click()
            p.locator("#tc-potion").click()
            for sel in ("#tc-bag", "#tc-map", "#tc-menu"):
                p.locator(sel).click()
            r = p.evaluate("""() => ({ casts: window.__casts, belts: window.__belts, ui: window.__ui })""")
            check("技能键 F1~F3 接线", r["casts"] == [1, 2, 3], str(r["casts"]))
            check("药水键接快捷栏 1", r["belts"] == [0], str(r["belts"]))
            check("背包/地图/菜单键接线", r["ui"] == ["bag", "map", "menu"], str(r["ui"]))

            print("\n[5] 按钮不穿透画布 / 场景退出清理")
            p.evaluate("window.__sent.length = 0")
            p.locator("#tc-attack").click()
            check("按钮不产生额外 move 包",
                  not any(x["t"] == "move" for x in p.evaluate("window.__sent")),
                  str(p.evaluate("window.__sent")))
            cleanup = p.evaluate("""() => {
                const sm = window.__sceneManager;
                sm.current.onExit();
                sm.current = null;   // 停掉主循环，避免已销毁组件继续 render
                return {
                    touch: window.__touchControls,
                    ui: !!document.querySelector('#touch-ui'),
                    body: document.body.classList.contains('touch'),
                };
            }""")
            check("退出场景清理虚拟 UI",
                  cleanup["touch"] is None and cleanup["ui"] is False and cleanup["body"] is False,
                  str(cleanup))
            check("无 JS 异常", not errors, "; ".join(errors[:3]))
            p.close()

            desktop.close()
            mobile.close()
            browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结果：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
