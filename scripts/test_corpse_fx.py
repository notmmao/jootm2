#!/usr/bin/env python3
"""浏览器端回归：怪物尸体不会「死了又站起来」（Playwright，需用 Python 3.9）。

历史根因：命中帧对齐的 170ms 延迟回调（PlayScene.mjs case 'attack'）在怪
死亡后仍把尸体 _action 设为 'struck'，渲染循环再归位 'stand'；同时服务端
死亡后下发的 entityAdd(corpse) 会替换实体对象，导致 _sprite 重建、尸体帧
纹理缓存全丢。托管攻速快时几乎必现。

    D:/Python39/python.exe scripts/test_corpse_fx.py
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
DB = ROOT / "data" / "_tmp_corpse_fx.db"


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
        steps = build_steps(str(int(time.time()))[-6:])
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for n in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[n]); pre.drain(16)
        pre.close()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("(u) => window.__conn.send('login', {username:u, password:'smoke123'})",
                          user)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('enterGame', {name:c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name:c})", char)
            page.wait_for_function(
                "window.__sceneManager.current && "
                "window.__sceneManager.current.constructor.name === 'PlayScene'",
                timeout=25000)
            page.wait_for_selector("#autoPilotBtn", timeout=25000)

            page.evaluate("""() => {
                window.__deaths = [];
                window.__conn.on('*', m => {
                    if (m.type === 'death') window.__deaths.push(m);
                });
            }""")
            page.click("#autoPilotBtn")
            page.wait_for_function("window.__deaths && window.__deaths.length > 0",
                                   timeout=45000)
            corpse_id = page.evaluate("window.__deaths[0].id")
            # 给尸体 sprite 打标记：若 entityAdd 把实体对象换掉，_sprite 会重建，
            # 标记丢失（表现为尸体帧纹理缓存全丢、尸体消失一瞬再出现）。
            page.evaluate("""(id) => {
                const s = window.__sceneManager.current;
                s._sprite(s.entities.get(id)).__probeMark = 1;
            }""", corpse_id)

            print("\n=== 死亡后尸体状态（先播 ActDie 倒地动画，播完定格） ===")
            # 段表最长 die 段 = MA34 20帧×200ms = 4000ms（raceImg 63），采样
            # 窗口须覆盖到动画播完；原版 TCreature.Die 无条件 lparam3=1 →
            # SM_NOWDEATH → 所有怪播完整 ActDie（Actor.pas:1044-1050）。
            states = []
            prev = 0
            for ms in (0, 150, 350, 600, 1200, 2000, 3000, 3800, 4300, 4800):
                if ms > prev:
                    page.wait_for_timeout(ms - prev)
                prev = ms
                states.append(page.evaluate("""(id) => {
                    const s = window.__sceneManager.current;
                    const e = s.entities.get(id);
                    if (!e) return {exists: false};
                    const sp = s._sprite(e);
                    const spec = sp._frameSpec(Date.now());
                    return {
                        exists: true, action: e._action, corpse: !!e._corpse,
                        dieUntil: e._dieUntil || 0, spriteKept: !!sp.__probeMark,
                        specLoaded: !!spec && spec.every(f => !!sp._tex.get(f.lib + '/' + f.index)),
                        blendLayers: (sp.getLayers(Date.now()) || []).filter(l => l.blend).length,
                    };
                }""", corpse_id))
            print("  状态:", [(s.get("action"), s.get("spriteKept")) for s in states])
            check("尸体始终存在", all(s.get("exists") for s in states))
            check("动作只在 die/death 之间（没有被 struck/stand 覆盖）",
                  all(s.get("action") in ("die", "death") for s in states))
            check("最终定格 death", states[-1].get("action") == "death")
            check("动画播完后无残留倒地计时",
                  all(s.get("dieUntil") == 0 for s in states if s.get("action") == "death"))
            check("entityAdd 未重建 sprite（缓存保持）",
                  all(s.get("spriteKept") for s in states))
            check("定格后尸体帧纹理已加载", states[-1].get("specLoaded"))
            check("定格后不是火焰特效层", all(s.get("blendLayers") == 0
                                              for s in states if s.get("action") == "death"))
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
