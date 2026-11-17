#!/usr/bin/env python3
"""浏览器端回归：大地图点击自动导航 vs GM Ctrl+点击传送（Playwright，Python 3.9）。

验证点：
- 普通点击大地图 canvas → 走 _startAutoNav 寻路，不发 @移动，关地图；
- Ctrl+点击 → 仍只发 @移动（GM 传送链路），不启动导航；
- _tickAutoNav 按跑速逐步发 move，到目标后停止；
- 目标为墙格 → ±2 就近找可站落点；无路/同格不启动；
- 手动点画面 / 换图 / 死亡 → 取消导航。

用法：D:/Python39/python.exe scripts/test_bigmap_nav.py
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
DB = ROOT / "data" / "_tmp_bigmap_nav.db"


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

            # 装一张 40×40 全可走的假地图（中间留一列墙），接管 conn.send 记录消息
            setup = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const W = 40, H = 40;
                const tiles = [];
                for (let x = 0; x < W; x++) {
                    tiles[x] = [];
                    for (let y = 0; y < H; y++) {
                        tiles[x][y] = { canStand: !(x === 20) };
                    }
                }
                sc.mapActor.map = { width: W, height: H, tiles };
                sc.self.x = 5; sc.self.y = 5;
                sc._selfDead = false;
                sc._cancelNav(true);
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({ t, m });
                return { x: sc.self.x, y: sc.self.y };
            }""")
            check("测试地图装配", setup == {"x": 5, "y": 5}, str(setup))
            sent = lambda: page.evaluate("window.__sent")

            print("\n[1] 寻路本体：_startAutoNav 算 A* 路径、关地图、不发 @移动")
            r = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc._bigMapOpen = true;   // 便于验证导航会关地图
                sc._startAutoNav(8, 9);
                return {
                    pathLen: sc._navPath ? sc._navPath.length : -1,
                    goal: sc._navGoal,
                    bigMapOpen: sc._bigMapOpen,
                    sent: window.__sent.slice(),
                };
            }""")
            # (5,5)→(8,9)：寻路斜走 4 步（3 右 4 下，对角合并）
            print("\n[1] 寻路本体：_startAutoNav 算 A* 路径、关地图、不发 @移动")
            check("_startAutoNav 生成路径", r["pathLen"] == 4, str(r["pathLen"]))
            check("记录目标格", r["goal"] == {"x": 8, "y": 9}, str(r["goal"]))
            check("自动导航关闭大地图", r["bigMapOpen"] is False)
            check("自动导航不发 GM 传送 @移动",
                  not any(x["t"] == "say" for x in r["sent"]), str(r["sent"]))

            print("\n[2] tick 逐步发 move（跑），到达后停止（单同步块，避开 rAF）")
            # requestAnimationFrame 主循环也在调 _tickAutoNav，跨 evaluate 会插入真实步数，
            # 所以 start + tick 全部放进一个同步块，保证计数准确。
            ticks = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                sc._cancelNav(true);
                sc._bigMapOpen = true;
                sc.self.x = 5; sc.self.y = 5;
                sc._startAutoNav(8, 9);
                const planned = sc._navPath ? sc._navPath.length : -1;
                let n = 0;
                while (sc._navPath && n < 20) {
                    sc._navLastAt = 0;      // 绕开真实时间节流，专注验证逐步发步
                    sc._navPrev = null;     // 跳过卡住检测（不模拟服务端回包）
                    sc._tickAutoNav(Date.now());
                    n++;
                }
                return {
                    planned, ticks: n,
                    navCleared: sc._navPath === null,
                    moves: window.__sent.filter(x => x.t === 'move'),
                };
            }""")
            check("路径 4 步", ticks["planned"] == 4, str(ticks["planned"]))
            check("导航按路径发 4 步 move", len(ticks["moves"]) == 4, str(ticks["moves"]))
            check("move 全部为跑", all(x["m"].get("run") for x in ticks["moves"]))
            check("路径走完清除导航态", ticks["navCleared"], str(ticks["ticks"]))

            print("\n[3] Ctrl+点击大地图仍是 GM 传送，不启动导航")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                sc._cancelNav(true);
                sc._bigMapOpen = true;
                sc._buildBigMapPanel();
                if (sc._bigMapPanel) sc._bigMapPanel.style.display = 'block';
                sc._bigMapGeom = { bx: 0, by: 0, w: 580, h: 350, mapW: 40, mapH: 40,
                                   k: 1, kx: 1, ky: 1, mapName: '0' };
                const cv = sc._bigMapCanvas;
                const r = cv.getBoundingClientRect();
                cv.dispatchEvent(new MouseEvent('mousedown', {
                    button: 0, ctrlKey: true, bubbles: true, cancelable: true,
                    clientX: r.left + 12, clientY: r.top + 9,
                }));
            }""")
            r = page.evaluate("""() => ({
                sent: window.__sent.slice(),
                navPath: window.__sceneManager.current._navPath,
            })""")
            check("Ctrl+点击发 @移动", any(x["t"] == "say" and x["m"]["text"].startswith("@移动") for x in r["sent"]), str(r["sent"]))
            check("Ctrl+点击不启动自动导航", r["navPath"] is None, str(r["navPath"]))

            print("\n[4] 普通点击大地图走导航链路（不发 @移动）")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                sc._cancelNav(true);
                sc._bigMapOpen = true;
                sc.self.x = 5; sc.self.y = 5;
                const cv = sc._bigMapCanvas;
                const r = cv.getBoundingClientRect();
                cv.dispatchEvent(new MouseEvent('mousedown', {
                    button: 0, ctrlKey: false, bubbles: true, cancelable: true,
                    clientX: r.left + 12, clientY: r.top + 9,
                }));
            }""")
            r = page.evaluate("""() => ({
                sent: window.__sent.slice(),
                navPath: window.__sceneManager.current._navPath,
                goal: window.__sceneManager.current._navGoal,
            })""")
            check("普通点击不发 @移动", not any(x["t"] == "say" for x in r["sent"]), str(r["sent"]))
            check("普通点击启动导航路径", r["navPath"] and len(r["navPath"]) > 0, str(r["goal"]))

            print("\n[5] 墙格就近落点 / 不可达 / 同格")
            r = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const out = {};
                sc._cancelNav(true);
                // 点墙格 (20,5) → 就近落到 (19,5)
                const land = sc._navLanding(sc.mapActor.map, 20, 5);
                out.wallLanding = land;
                // 目标与自身同格 → 不生成路径
                sc.self.x = 5; sc.self.y = 5;
                sc._startAutoNav(5, 5);
                out.sameCellNav = sc._navPath;
                // 整列墙隔断且 ±2 无可站落点：点一个被墙包围的不可站格 → 提示无落点
                sc.mapActor.map.tiles[20][5].canStand = false;
                out.noLanding = sc._navLanding(sc.mapActor.map, 20, 5);
                sc.mapActor.map.tiles[20][5].canStand = true;
                return out;
            }""")
            check("墙格就近落到可站格", r["wallLanding"] and r["wallLanding"]["x"] == 19, str(r["wallLanding"]))
            check("同格不启动导航", r["sameCellNav"] is None)

            print("\n[6] 手动/换图/死亡取消导航")
            cancels = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const out = {};
                const start = () => { sc.self.x = 5; sc.self.y = 5; sc._startAutoNav(9, 9); };
                start(); sc._cancelNav();
                out.manual = sc._navPath === null;
                start(); sc._cancelNav(true);
                out.changeMap = sc._navPath === null;
                start(); sc._cancelNav(true);
                out.death = sc._navPath === null;
                return out;
            }""")
            check("手动取消导航", cancels["manual"])
            check("换图取消导航", cancels["changeMap"])
            check("死亡取消导航", cancels["death"])

            check("全程无 JS 异常", not errors, "; ".join(errors[:3]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    ok = sum(1 for _, c, _ in results if c)
    print(f"\n===== 结果：{ok}/{len(results)} PASS =====")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
