#!/usr/bin/env python3
"""浏览器端回归：玩家被杀后，其他玩家（击杀者视角）看到的是倒地尸体而非站立。

历史根因：服务端 kill_player 广播的 death 消息不带 corpse 标记；前端 case 'death'
给玩家设 _action='die' 播 1400ms 动画，播完切 _action='death'。但玩家动作段表
PLAYER_SEG 没有 death 键 → `|| 'stand'` 回退成站立帧。原版 SM_DEATH（Actor.pas
:1036-1043）是把 startframe 设成 endframe 直接定格 ActDie 末帧。

    D:/Python39/python.exe scripts/test_pvp_death_visual.py
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
DB = ROOT / "data" / "_tmp_pvp_death_visual.db"


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

            print("\n[1] 别人被杀（击杀者视角）→ 直接定格 die 末帧，不是站立")
            # 造一个非自己的玩家实体，发 death 消息
            dead = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const e = {id:9001, type:'player', name:'被杀者', x:sc.self.x+1, y:sc.self.y,
                    dir:0, sex:0, hair:2, dress:0, weapon:0, hp:0, maxHp:20, alive:true};
                sc.entities.set(9001, e);
                sc.onMessage('death', {type:'death', id:9001});
                const after = sc.entities.get(9001);
                const sp = sc._sprite(after);
                const spec = sp._frameSpec(Date.now());
                const humLayer = (spec || []).find(f => f.lib === 'hum');
                return {action: after._action, corpse: !!after._corpse,
                        dieUntil: after._dieUntil || 0,
                        humIndex: humLayer ? humLayer.index : -1};
            }""")
            check("实体标记 corpse", dead.get("corpse") is True)
            check("动作定格 death（不播动画）", dead.get("action") == "death",
                  f"实际 {dead.get('action')}")
            check("无残留倒地计时", dead.get("dieUntil") == 0)
            # HA.die.start=536, frame=4, skip=4, dir=0 → 末帧 = 536+3 = 539
            # 段表映射 death→die 后 index 应落在 536..539，不是 stand 的 0..7
            check("hum 帧落在 ActDie 段（倒地）", 536 <= dead.get("humIndex", -1) <= 539,
                  f"index={dead.get('humIndex')}（stand 应为 0..7）")

            print("\n[2] 快照里的尸体（后进视野）同样定格，不站立")
            snap = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.onMessage('entityAdd', {type:'entityAdd', entity:{
                    id:9002, type:'player', name:'尸体', x:sc.self.x+2, y:sc.self.y,
                    dir:4, sex:1, hair:2, dress:0, weapon:0, hp:0, maxHp:20,
                    alive:false, corpse:true}});
                const e = sc.entities.get(9002);
                const spec = sc._sprite(e)._frameSpec(Date.now());
                const humLayer = (spec || []).find(f => f.lib === 'hum');
                return {action: e._action, corpse: !!e._corpse,
                        humIndex: humLayer ? humLayer.index : -1};
            }""")
            check("快照尸体标记 corpse", snap.get("corpse") is True)
            check("快照尸动作 death", snap.get("action") == "death", str(snap.get("action")))
            # dir=4 → ActDie 段 = 536 + 4*(4+4) = 568..571
            check("快照尸体帧在 ActDie 段（倒地）", 568 <= snap.get("humIndex", -1) <= 571,
                  f"index={snap.get('humIndex')}")

            print("\n[3] 自己死亡仍播完整 die 动画（原版 SM_NOWDEATH）")
            mine = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const self = sc.self;
                self.hp = 0; self.alive = true;
                sc.onMessage('death', {type:'death', id:self.id});
                return {action: self._action, dieUntil: self._dieUntil || 0,
                        selfDead: !!sc._selfDead};
            }""")
            check("自己死亡播 die 动画", mine.get("action") == "die", str(mine.get("action")))
            check("自己死亡有动画计时", mine.get("dieUntil", 0) > time.time() * 1000)
            check("自己死亡黑白屏标志", mine.get("selfDead") is True)
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
