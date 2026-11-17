#!/usr/bin/env python3
"""浏览器端验证：交易/组队 UI 渲染与按钮发消息（Playwright，Python 3.9）。

服务端用 3.10 启动（项目约定），本脚本用装了 Playwright 的 3.9 运行：

    D:/Python39/python.exe scripts/test_trade_party_ui.py

验证点：
- 编组按钮打开队伍面板；收到 partyInfo 显示队员/血条；队伍聊天回车发 partyChat。
- 收到 tradeInvite 弹确认框，接受发 tradeAccept；tradeStart 开窗；tradeState
  渲染双方物品/金币；锁定按钮发 tradeConfirm；tradeEnd 关窗。
- 交易按钮弹名字输入框，输入后发 tradeRequest。
UI 消息一律经 PlayScene.onMessage 合成（服务端协议另由 test_trade_party.py 覆盖）。
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
DB = ROOT / "data" / "_tmp_trade_party_ui.db"


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

            # 拦截 conn.send，记录 UI 发出的业务消息
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({t, m});
            }""")
            sent = lambda: page.evaluate("window.__sent")
            emit = lambda t, m: page.evaluate("([t,m]) => window.__sceneManager.current.onMessage(t,m)", [t, m])

            print("\n[1] 编组按钮打开队伍面板，收到 partyInfo 显示队员与血条")
            # 找 tip=编组的 HUD 按钮并点击
            opened = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                const btns = [...document.querySelectorAll('.d-control,div')]
                    .filter(e => e.title === '编组');
                if (btns.length) { btns[0].click(); return true; }
                return false;
            }""")
            check("编组按钮存在且可点", opened)
            emit("partyInfo", {"type": "partyInfo", "leaderId": 1, "members": [
                {"id": 1, "name": "队长甲", "level": 7, "job": 0,
                 "hp": 30, "maxHp": 50, "online": True, "map": "0"},
                {"id": 2, "name": "队员乙", "level": 5, "job": 1,
                 "hp": 8, "maxHp": 40, "online": False, "map": "0"},
            ]})
            page.wait_for_timeout(200)
            body = page.evaluate("document.body.innerText")
            check("队员名/等级显示", "队长甲" in body and "Lv.7" in body, body[:80])
            check("离线标记显示", "[离线]" in body)
            check("血条按比例绘制", page.evaluate("""() => {
                const bars = [...document.querySelectorAll('div')].filter(d =>
                    (d.style.background || '').includes('200, 52, 42'));
                return bars.length >= 2 && parseFloat(bars[0].style.width) === 60;
            }"""), page.evaluate("""() => [...document.querySelectorAll('div')]
                .filter(d => (d.style.background||'').includes('200, 52, 42'))
                .map(d => d.style.width).join(',')"""))

            print("\n[2] 队伍聊天回车发 partyChat；邀请按钮发 partyInvite")
            page.evaluate("""() => {
                const inp = document.querySelector('input[placeholder="队伍聊天 (回车发送)"]');
                if (inp) { inp.value = '下墓'; inp.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); }
            }""")
            page.wait_for_timeout(100)
            check("回车发 partyChat", any(x["t"] == "partyChat" and x["m"].get("text") == "下墓" for x in sent()))

            print("\n[3] 收到 partyInvite 弹确认；拒绝/接受发对应消息")
            emit("partyInvite", {"type": "partyInvite", "fromId": 9, "fromName": "玩家丙"})
            page.wait_for_timeout(150)
            check("组队邀请弹窗出现", "邀请你加入队伍" in page.evaluate("document.body.innerText"))
            page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(d => d.textContent === '接受' && d.style.cursor === 'pointer');
                if (b.length) b[b.length-1].click();
            }""")
            page.wait_for_timeout(100)
            check("接受发 partyAccept", any(x["t"] == "partyAccept" for x in sent()))

            print("\n[4] 交易按钮弹名字框 → 发 tradeRequest")
            page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(d => d.title === '交易');
                if (b.length) b[0].click();
            }""")
            page.wait_for_timeout(120)
            check("发起交易框出现", "发起交易" in page.evaluate("document.body.innerText"))
            page.evaluate("""() => {
                const inp = [...document.querySelectorAll('input')].find(i => i.placeholder === '' && i.parentElement && i.parentElement.innerText.includes('发起交易'));
                if (inp) { inp.value = '附近玩家'; }
                const ok = [...document.querySelectorAll('div')].filter(d => d.textContent === '邀请' && d.style.cursor === 'pointer');
                if (ok.length) ok[ok.length-1].click();
            }""")
            page.wait_for_timeout(100)
            check("输入名字发 tradeRequest",
                  any(x["t"] == "tradeRequest" and x["m"].get("name") == "附近玩家" for x in sent()),
                  str(sent()[-3:]))

            print("\n[5] tradeInvite 弹窗 → 接受发 tradeAccept")
            emit("tradeInvite", {"type": "tradeInvite", "fromId": 9, "fromName": "卖方丁"})
            page.wait_for_timeout(120)
            check("交易请求弹窗出现", "想与你交易" in page.evaluate("document.body.innerText"))
            page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(d => d.textContent === '接受' && d.style.cursor === 'pointer');
                if (b.length) b[b.length-1].click();
            }""")
            page.wait_for_timeout(100)
            check("接受发 tradeAccept", any(x["t"] == "tradeAccept" for x in sent()))

            print("\n[6] tradeStart 开窗 + tradeState 渲染双方物品金币")
            emit("tradeStart", {"type": "tradeStart", "selfName": "我", "otherName": "卖方丁"})
            emit("tradeState", {"type": "tradeState",
                "self": {"name": "我", "gold": 300, "confirm": False,
                         "items": [{"makeIndex": 1, "name": "金创药", "looks": 0}]},
                "other": {"name": "卖方丁", "gold": 1000, "confirm": True,
                          "items": [{"makeIndex": 2, "name": "木剑", "looks": 5}]}})
            page.wait_for_timeout(200)
            body = page.evaluate("document.body.innerText")
            check("交易窗口标题与金币显示", "交易" in body and "1,000" in body, body[:120])
            # 原版交易格只画图标、名称靠 tooltip（DDGridGridMouseMove），
            # 本项目同款：物品名在格子 title 属性而非可见文字。
            check("双方物品名（tooltip）显示", page.evaluate("""() => {
                const ts = [...document.querySelectorAll('[title]')].map(e => e.title);
                return ts.includes('金创药') && ts.includes('木剑');
            }"""))

            print("\n[7] 锁定按钮发 tradeConfirm；点自己物品发 tradeRemoveItem")
            page.evaluate("""() => {
                const b = [...document.querySelectorAll('div')].filter(d => d.textContent.includes('锁定') && d.style.cursor === 'pointer');
                if (b.length) b[b.length-1].click();
            }""")
            page.wait_for_timeout(80)
            check("锁定发 tradeConfirm", any(x["t"] == "tradeConfirm" for x in sent()))
            page.evaluate("""() => {
                const c = [...document.querySelectorAll('div')].filter(d => d.title === '金创药' && d.style.cursor === 'pointer');
                if (c.length) c[0].click();
            }""")
            page.wait_for_timeout(80)
            check("点自己物品发 tradeRemoveItem", any(x["t"] == "tradeRemoveItem" for x in sent()))

            print("\n[8] tradeEnd 关窗")
            emit("tradeEnd", {"type": "tradeEnd", "reason": "success"})
            page.wait_for_timeout(120)
            check("交易窗关闭", "锁定 / 确认" not in page.evaluate("document.body.innerText"))

            check("无 JS 运行时异常", not errors, str(errors[:3]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结果：{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
