# -*- coding: utf-8 -*-
"""导航后台续跑回归（2026-11-23）。

验证：寻路（大地图点击导航）在页面转入后台后不再因 rAF 被浏览器冻结而停步，
改由 setInterval 专用定时器继续发 move；回前台/取消导航时定时器停表。

手法：
- 用「rAF 空转」精确模拟 Chrome 后台冻结渲染循环；
- 覆盖 document.hidden / visibilityState 并派发 visibilitychange 模拟前后台切换
  （Playwright 无法真实隐藏页面，这层的代码路径与被冻结的定时器/事件一致）；
- 服务端 echo 驱动 self.x/y，位置变化即证明步进仍在发生。

用法（需 3.9 Playwright，服务端由脚本自起在随机端口 + 临时库）：
    D:/Python39/python.exe scripts/test_nav_background.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_navbg.db"

sys.path.insert(0, str(ROOT / "scripts"))
from smoke_ws import WS  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(base: str, proc: subprocess.Popen) -> None:
    import urllib.request
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("服务端进程提前退出")
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("服务端未就绪")


ENTER_JS = """
async () => {
    const w = window.__sceneManager.current;
    if (!w.self) return {err: 'no self'};
    // 冻结 rAF：tick 闭包里 requestAnimationFrame(tick) 走 window 动态解析，
    // 覆盖后下一帧起循环即停，等价 Chrome 后台冻结。
    window.requestAnimationFrame = () => 0;
    // 找一个可达目标（出生点附近，8 候选距离）
    const p = w.self;
    for (const [dx, dy] of [[8,0],[0,8],[-8,0],[0,-8],[6,6],[-6,6],[6,-6],[-6,-6]]) {
        w._startAutoNav(p.x + dx, p.y + dy);
        if (w._navPath) return {ok: true, goal: [p.x + dx, p.y + dy]};
    }
    return {err: 'no reachable goal'};
}
"""

HIDE_JS = """
() => {
    Object.defineProperty(document, 'hidden', {get: () => true, configurable: true});
    Object.defineProperty(document, 'visibilityState',
        {get: () => 'hidden', configurable: true});
    document.dispatchEvent(new Event('visibilitychange'));
}
"""

SHOW_JS = """
() => {
    Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
    Object.defineProperty(document, 'visibilityState',
        {get: () => 'visible', configurable: true});
    document.dispatchEvent(new Event('visibilitychange'));
}
"""

STAT_JS = """
() => {
    const w = window.__sceneManager.current;
    return {x: w.self.x, y: w.self.y,
            nav: !!w._navPath, timer: !!w._navBgTimer,
            steps: (w._navPath ? w._navPath.length : 0)};
}
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    for f in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
        if f.exists():
            f.unlink()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_ready(base, proc)

        # WS 前置：注册 player + 建角
        pre = WS("127.0.0.1", port, "/ws", timeout=1.0)
        for m in ({"type": "register", "username": "player", "password": "nav123"},
                  {"type": "login", "username": "player", "password": "nav123"},
                  {"type": "createCharacter", "name": "NavHero", "sex": 0, "hair": 1, "job": 0}):
            pre.send(m)
            pre.drain(16)
        pre.close()

        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.wait_for_selector("input[type=text]", timeout=15000)
            page.fill("input[type=text]", "player")
            page.fill("input[type=password]", "nav123")
            page.press("input[type=password]", "Enter")
            page.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=25000)
            page.evaluate("() => window.__conn.send('enterGame', {name:'NavHero'})")
            page.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page.evaluate("() => window.__conn.send('noticeOk', {name:'NavHero'})")
            page.wait_for_function(
                "window.__sceneManager.current.self && Array.isArray(window.__sceneManager.current.self.items)",
                timeout=15000)
            page.wait_for_timeout(2500)

            # ---- 1) 冻结 rAF 后启动导航：主循环停 → 前台状态下不应步进 ----
            start = page.evaluate(ENTER_JS)
            check("1 导航启动且目标可达", start.get("ok"), str(start)[:100])
            if not start.get("ok"):
                return 1
            s0 = page.evaluate(STAT_JS)
            time.sleep(1.2)   # 先让冻结瞬间「在飞」的一步 echo 落定再取样
            s0 = page.evaluate(STAT_JS)
            time.sleep(3.0)
            s1 = page.evaluate(STAT_JS)
            check("2 rAF 冻结且前台(未隐藏)时确实停步（对照组）",
                  s1["x"] == s0["x"] and s1["y"] == s0["y"] and s1["timer"] is False,
                  f"s0=({s0['x']},{s0['y']}) s1=({s1['x']},{s1['y']})")

            # ---- 3) 模拟转入后台：visibilitychange → 定时器接管 ----
            page.evaluate(HIDE_JS)
            s2 = page.evaluate(STAT_JS)
            check("3a 隐藏后定时器已启动", s2["timer"] is True)
            time.sleep(4.0)
            s3 = page.evaluate(STAT_JS)
            moved = s3["x"] != s2["x"] or s3["y"] != s2["y"]
            check("3b 后台期间位置继续推进（rAF 仍冻结）", moved,
                  f"({s2['x']},{s2['y']}) -> ({s3['x']},{s3['y']}) steps={s2['steps']}->{s3['steps']}")
            check("3c 后台导航未被中断（仍在跑 或 已正常走完到目标）",
                  s3["nav"] is True or (s3["steps"] == 0 and moved),
                  f"nav={s3['nav']} steps={s3['steps']}")

            # ---- 4) Web Lock 豁免已申请 ----
            locks = page.evaluate(
                "async () => (await navigator.locks.query()).held.map(h => h.name)")
            check("4 后台导航持有 Web Lock 豁免（部分 Chrome 完全不冻结）",
                  "jootm2.nav-background" in locks, str(locks))

            # ---- 5) 回前台：定时器停表（rAF 保持冻结，位置应再次静止） ----
            page.evaluate(SHOW_JS)
            time.sleep(0.5)
            s4 = page.evaluate(STAT_JS)
            check("5a 恢复可见后定时器停表", s4["timer"] is False)
            time.sleep(2.5)
            s5 = page.evaluate(STAT_JS)
            check("5b 回前台但 rAF 仍冻结 → 停步（无残留双驱动）",
                  s5["x"] == s4["x"] and s5["y"] == s4["y"],
                  f"({s4['x']},{s4['y']}) -> ({s5['x']},{s5['y']})")

            # ---- 6) 取消导航：再隐藏也不得有活动定时器 ----
            page.evaluate("() => window.__sceneManager.current._cancelNav(true)")
            page.evaluate(HIDE_JS)
            time.sleep(0.5)
            s6 = page.evaluate(STAT_JS)
            check("6 取消导航后隐藏 → 无定时器、无路径",
                  s6["timer"] is False and s6["nav"] is False)
            browser.close()

        check("Z 无页面 JS 报错", not errors, "; ".join(errors[:2]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    fails = [r for r in results if not r[1]]
    print(f"\n结论: {len(results) - len(fails)}/{len(results)} 通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
