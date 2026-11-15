#!/usr/bin/env python3
"""浏览器端回归：升级光效 14 帧不出现空白帧（Playwright，需用 Python 3.9）。

背景：WIL 的 getTexture 是异步的，特效帧以前边播边取、首次访问返回 null，
于是每 80ms 换一次帧号就整帧空白 —— 表现为「升级特效闪得厉害」。
本验证检查进游戏后预热是否把 chrsel[4..17] 全部填好、每个 rel 帧都能取到
贴图（含相邻帧回退）、连升两级不会叠加两份 lighter 光效。

    D:/Python39/python.exe scripts/test_levelup_fx.py
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
DB = ROOT / "data" / "_tmp_levelup_fx.db"


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
         "--db", str(DB), "--no-resource-index"],
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
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for name in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[name])
            pre.drain(16)
        pre.close()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
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
                "window.__sceneManager.current.constructor.name === 'PlayScene'",
                timeout=25000)

            print("\n=== 升级光效 14 帧预取 ===")
            # 进游戏 enter() 后预取 + 1.5s 补发，给足纹理回填时间。
            ready = page.wait_for_function("""() => {
                const s = window.__sceneManager.current;
                if (!s || !s._fxCache) return false;
                for (let f = 0; f < 14; f++) {
                    if (!s._fxCache.get('chrsel/' + (4 + f))) return false;
                }
                return true;
            }""", timeout=20000)
            check("chrsel[4..17] 14 帧全部预取就绪", ready is not None)

            # 每个 rel 帧都能拿到贴图（当前帧缺时用相邻帧回退），即不会整帧空白。
            frames = page.evaluate("""() => {
                const s = window.__sceneManager.current;
                const out = [];
                for (let f = 0; f < 14; f++) {
                    out.push(!!s._fxTexture({lib: 'chrsel', base: 4}, f, 14));
                }
                return out;
            }""")
            check("14 个 rel 帧均有可绘制贴图", all(frames),
                  f"可用 {sum(frames)}/14")

            print("\n=== 连升两级不叠加光效 ===")
            count = page.evaluate("""() => {
                const s = window.__sceneManager.current;
                s._spawnLevelupFx();
                s._spawnLevelupFx();
                return s.effects.filter(f => f.kind === 'levelup').length;
            }""")
            check("重复触发只保留一份 levelup", count == 1, f"count={count}")

            print("\n=== 光效实际绘制（像素亮度）===")
            # 采样角色上方 300×320 区域：lighter 金色光柱会明显抬亮。
            sample = """() => {
                const c = document.getElementById('screen');
                const d = c.getContext('2d').getImageData(250, 0, 300, 320).data;
                let sum = 0;
                for (let i = 0; i < d.length; i += 16) sum += d[i] + d[i+1] + d[i+2];
                return sum;
            }"""
            # 先让上一份光效播完，取一帧基线。
            page.wait_for_timeout(1300)
            base_sum = page.evaluate(sample)
            page.evaluate("""() => {
                const s = window.__sceneManager.current;
                s.effects = s.effects.filter(f => f.kind !== 'levelup');
                s._spawnLevelupFx();
            }""")
            bright = []
            for _ in range(8):
                page.wait_for_timeout(80)
                bright.append(page.evaluate(sample))
            gain = max(bright) - base_sum
            active = sum(1 for v in bright if v > base_sum + max(1, base_sum // 100))
            check("光效绘制时画面明显变亮", gain > base_sum * 0.01,
                  f"基线={base_sum} 峰值={max(bright)} 增益={gain}")
            check("连续 8 帧中没有整帧空白", active >= 7,
                  f"有光帧 {active}/8 · 序列={[v - base_sum for v in bright]}")
            if errors:
                print("  [页面 JS 异常] " + " | ".join(errors[:3]))
            check("无页面 JS 异常", not errors, "; ".join(errors[:2]))
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
