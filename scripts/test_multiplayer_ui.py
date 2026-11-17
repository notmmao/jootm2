#!/usr/bin/env python3
"""浏览器端验证：多人功能 UI（Playwright，Python 3.9，服务端 3.10）。

验证点：
- 头顶气泡：收到 chat 后说话者实体带 _sayText/_sayUntil；渲染 snap 含 say 字段。
- 密语：收到 whisper 在聊天板显示「[密语] ...」，特殊着色；不弹头顶气泡。
- 在线列表：HUD「在线玩家」按钮开窗并发 onlineList；收到后显示名字/等级/地图。
- 查看装备：收到 userState 弹装备窗，13 槽按 slot 显示物品 tooltip。

用法：D:/Python39/python.exe scripts/test_multiplayer_ui.py
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
DB = ROOT / "data" / "_tmp_multiplayer_ui.db"


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

            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({t, m});
            }""")
            sent = lambda: page.evaluate("window.__sent")
            emit = lambda t, m: page.evaluate("([t,m]) => window.__sceneManager.current.onMessage(t,m)", [t, m])

            print("\n[1] 头顶气泡：chat 写入说话者实体 _sayText/_sayUntil")
            bubble = emit("chat", {"type": "chat", "id": 4242, "name": "路人甲", "text": "你好世界"})
            page.wait_for_timeout(100)
            # 合成一个 id=4242 的实体，再发 chat
            injected = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.entities.set(4242, {id:4242, type:'player', name:'路人甲', x:sc.self.x+1, y:sc.self.y, hp:10, maxHp:10});
                sc.onMessage('chat', {type:'chat', id:4242, name:'路人甲', text:'你好世界'});
                const e = sc.entities.get(4242);
                return {text: e._sayText, until: e._sayUntil};
            }""")
            check("实体带 _sayText", injected.get("text") == "你好世界", str(injected))
            check("气泡 4 秒有效期", injected.get("until", 0) > time.time() * 1000 + 3000)
            # 聊天板也写入了文字
            check("聊天板出现说话内容", "路人甲: 你好世界" in page.evaluate("document.body.innerText"))

            print("\n[2] 密语：whisper 特殊着色显示，不弹头顶气泡")
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.onMessage('whisper', {type:'whisper', fromId:9, fromName:'神秘人',
                    toName:sc.self.name, text:'来PK', incoming:true});
            }""")
            page.wait_for_timeout(100)
            body = page.evaluate("document.body.innerText")
            check("密语显示 [密语] 前缀", "[密语]" in body and "神秘人" in body and "来PK" in body,
                  body[-120:])
            check("密语是紫色字", page.evaluate("""() => {
                const els = [...document.querySelectorAll('*')].filter(e =>
                    e.children.length===0 && (e.textContent||'').includes('来PK'));
                return els.some(e => (e.style.color||'').includes('160') || (e.style.color||'').includes('a020'));
            }"""))

            print("\n[3] 在线列表按钮开窗 → 发 onlineList → 渲染")
            opened = page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(e => e.title === '在线玩家');
                if (b.length) { b[0].click(); return true; }
                return false;
            }""")
            check("在线玩家按钮存在且可点", opened)
            page.wait_for_timeout(120)
            check("点击发 onlineList", any(x["t"] == "onlineList" for x in sent()))
            emit("onlineList", {"type": "onlineList", "players": [
                {"name": "老大", "level": 40, "job": 0, "map": "比奇省"},
                {"name": "老二", "level": 25, "job": 1, "map": "沃玛森林"},
                {"name": "老三", "level": 9, "job": 2, "map": "新手村"},
            ]})
            page.wait_for_timeout(150)
            body = page.evaluate("document.body.innerText")
            check("在线列表显示名字/等级/地图", "老大" in body and "Lv.40" in body
                  and "比奇省" in body, body[-160:])
            check("显示职业", "战士" in body and "法师" in body)

            print("\n[4] 查看装备：userState 弹窗 + 13 槽 tooltip")
            emit("userState", {"type": "userState", "id": 77, "name": "富有的玩家",
                "level": 33, "job": 0, "sex": 0, "hair": 1,
                "nameColor": 255, "dress": 10, "weapon": 20,
                "items": [
                    {"slot": 0, "name": "布衣", "looks": 5},
                    {"slot": 1, "name": "裁决之杖", "looks": 88},
                    {"slot": 7, "name": "力量戒指", "looks": 120},
                ]})
            page.wait_for_timeout(180)
            body = page.evaluate("document.body.innerText")
            # 原版 DUserState1：名字居中在窗口顶部（不再有自造的「XX 的装备」标题栏）
            check("装备窗顶部显示对方名", "富有的玩家" in body)
            check("等级/职业显示", "Lv.33" in body and "战士" in body)
            check("13 槽物品 tooltip", page.evaluate("""() => {
                const ts = [...document.querySelectorAll('[title]')].map(e => e.title);
                return ['布衣','裁决之杖','力量戒指'].every(n => ts.some(t => t && t.includes(n)));
            }"""))
            # 原版保真：人物半身像 + 装备穿身上 + DSW 槽位 + 关闭键，而不是自造方格列表
            check("原版 DUserState1 布局", page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const w = sc.tradePartyUI._inspectWin;
                if (!w || w.style.left !== '568px') return false;
                // 人物半身像 @ (38,52)；13 槽用 DSW 坐标（戒指L 168,215 / 护身符 42,254）
                const pos = [...w.children].map(e => e.style.left + ',' + e.style.top);
                return ['38px,52px', '168px,215px', '42px,254px', '8px,39px'].every(p => pos.includes(p));
            }"""))

            print("\n[5] Ctrl+右键玩家发 queryUserState")
            query_sent = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                // 命中判定依赖 sprite 图层（真实实体有，合成实体没有）；
                // 这里临时替换命中函数返回目标玩家，专注验证「Ctrl+右键 → 发消息」接线。
                const orig = sc._actorAtScreen.bind(sc);
                sc._actorAtScreen = (mx, my, type) => (type === 'player')
                    ? {id:88, type:'player', name:'被查看者', dead:false}
                    : orig(mx, my, type);
                const r = sc._canvas.getBoundingClientRect();
                sc._canvas.dispatchEvent(new MouseEvent('mousedown', {
                    button:2, ctrlKey:true, bubbles:true, cancelable:true,
                    clientX: r.left + 100, clientY: r.top + 100,
                }));
                sc._actorAtScreen = orig;
                return window.__sent;
            }""")
            check("Ctrl+右键发 queryUserState",
                  any(x["t"] == "queryUserState" and x["m"].get("id") == 88 for x in query_sent),
                  str(query_sent))
            # 不带 Ctrl 的右键不应触发查看（落到「跑」）
            plain_right = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                const orig = sc._actorAtScreen.bind(sc);
                sc._actorAtScreen = (mx, my, type) => (type === 'player')
                    ? {id:88, type:'player', name:'被查看者', dead:false}
                    : orig(mx, my, type);
                const r = sc._canvas.getBoundingClientRect();
                sc._canvas.dispatchEvent(new MouseEvent('mousedown', {
                    button:2, ctrlKey:false, bubbles:true, cancelable:true,
                    clientX: r.left + 100, clientY: r.top + 100,
                }));
                sc._actorAtScreen = orig;
                return window.__sent;
            }""")
            check("普通右键不发 queryUserState（仍是跑）",
                  not any(x["t"] == "queryUserState" for x in plain_right),
                  str(plain_right))

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
