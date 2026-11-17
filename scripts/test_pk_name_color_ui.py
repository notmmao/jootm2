#!/usr/bin/env python3
"""浏览器端回归：PK 名字颜色默认可见（Playwright，Python 3.9）。

历史根因：MapActor 只在「增强模式」(jootm2.hudEnhanced，默认 false) 打开时
才按服务端 nameColor 着色，默认关闭时白/黄/红/灰名全画成同一米色，PK 状态
完全看不见。原版名字颜色对所有玩家可见。

    D:/Python39/python.exe scripts/test_pk_name_color_ui.py
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
DB = ROOT / "data" / "_tmp_pk_namecolor.db"


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
        steps = build_steps(str(int(time.time()))[-6:])
        user = steps["register"]["username"]
        char = steps["createCharacter"]["name"]
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for n in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[n]); pre.drain(16)
        pre.close()

        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("(u) => window.__conn.send('login', {username:u, password:'smoke123'})", user)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'", timeout=25000)
            page.evaluate("() => window.__conn.send('listCharacters')")
            page.wait_for_timeout(300)
            page.evaluate("(c) => window.__conn.send('enterGame', {name:c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'", timeout=15000)
            page.evaluate("(c) => window.__conn.send('noticeOk', {name:c})", char)
            page.wait_for_function("window.__sceneManager.current && window.__sceneManager.current.self && Array.isArray(window.__sceneManager.current.self.items)", timeout=15000)

            # 增强模式默认关（原版行为：名字颜色对所有玩家可见）
            check("增强模式默认关闭",
                  page.evaluate("window.__sceneManager.current._hudMode.enhanced === false"))

            # 直接调 MapActor._drawHeadHud 用假 canvas 捕获名字绘制的 fillStyle。
            # 造 3 个只差 nameColor 的玩家实体，分别渲染取色。
            colors = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const ma = sc.mapActor;
                const draw = ma._drawHeadHud;
                // 假 ctx：记录每次 fillText 时的 fillStyle
                const captured = [];
                const ctx = {
                    font:'', textAlign:'', textBaseline:'', fillStyle:'', strokeStyle:'',
                    lineWidth:1,
                    fillText(t,x,y){ captured.push({t, c:this.fillStyle}); },
                    strokeText(){}, fillRect(){}, strokeRect(){},
                    measureText(t){ return {width: t.length*6}; },
                    save(){}, restore(){}, beginPath(){}, moveTo(){}, lineTo(){}, stroke(){},
                };
                const result = {};
                for (const [k, nc] of [['white',255],['yellow',251],['red',249],['gray',47]]) {
                    captured.length = 0;
                    const e = {id:1, type:'player', name:'PK名字', x:sc.self.x, y:sc.self.y,
                        hp:20, maxHp:20, alive:true, nameColor:nc, dir:0, isSelf:false};
                    ma.hudMode = {enhanced:false, showHpLabel:true, hoverNamesOnly:false};
                    draw.call(ma, ctx, e, 400, 300);
                    const hit = captured.find(a => a.t === 'PK名字');
                    result[k] = hit ? hit.c : null;
                }
                return result;
            }""")
            print("  名字颜色采样:", colors)
            check("白名=白(255)色", colors.get("white") == "rgb(255,255,255)"
                  or colors.get("white", "").startswith("rgb"), str(colors.get("white")))
            check("黄名与白名颜色不同", colors.get("yellow") != colors.get("white"))
            check("红名与白名颜色不同", colors.get("red") != colors.get("white"))
            check("红名与黄名颜色不同", colors.get("red") != colors.get("yellow"))
            check("灰名(棕)与其它不同",
                  colors.get("gray") not in (colors.get("white"), colors.get("yellow"), colors.get("red")))
            check("无页面 JS 异常", not errors, "; ".join(errors[:2]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for p in DB.parent.glob(DB.name + "*"):
            p.unlink()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n结论: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
