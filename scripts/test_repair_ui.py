#!/usr/bin/env python3
"""浏览器端验证：NPC 修理投放框 + 武器幸运 tooltip（Playwright，Python 3.9）。

用户反馈「NPC 的修复 UI 有问题，没有地方放装备」→ 验证 @repair / @s_repair
会开 DSellDlg 投放框，背包物品能放进圈、能问价、能点 OK 发 repairItem；
以及「幸运+1 体现在哪」→ 验证物品说明里出现「幸运+N」。

服务端用 3.10 启动，本脚本用装了 Playwright 的 3.9 运行：

    D:/Python39/python.exe scripts/test_repair_ui.py
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
DB = ROOT / "data" / "_tmp_repair_ui.db"


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
        results.append((name, bool(cond), detail))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    port = free_port()
    base = "http://127.0.0.1:%d" % port
    for p in DB.parent.glob(DB.name + "*"):
        try:
            p.unlink()
        except OSError:
            pass
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
                "window.__sceneManager.current && window.__sceneManager.current.self && "
                "Array.isArray(window.__sceneManager.current.self.items)", timeout=15000)

            # 拦截 conn.send，注入一件磨损武器（带 lucky=2 验证 tooltip）
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent = [];
                sc.conn.send = (t, m) => window.__sent.push({t, m});
                sc.onMessage('bagUpdate', {type: 'bagUpdate', gold: 5000,
                    items: [{makeIndex: 200001, index: 2, name: '测试木剑',
                             stdMode: 5, looks: 0, weight: 2, lucky: 2,
                             durability: 1000, durabilityMax: 4000,
                             dc: 2, dcMax: 5}],
                    quickSlots: [0,0,0,0,0,0]});
            }""")

            # ---- 1. @repair 开投放框 -------------------------------------
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                sc.onMessage('npcSay', {type: 'npcSay', npcId: 1, name: '铁匠',
                    text: '修理什么？', shop: 'repair'});
            }""")
            state = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                return {panel: !!sc.shopPanel, spot: !!sc.shopSpotEl,
                        mode: sc.shopRepairMode,
                        price: sc.shopPriceEl && sc.shopPriceEl.textContent,
                        bag: sc.bagPanel && sc.bagPanel.style.display,
                        left: sc.bagPanel && sc.bagPanel.style.left};
            }""")
            check("修理窗口已开（shopPanel 存在）", state["panel"], str(state))
            check("投放框存在（有地方放装备）", state["spot"], str(state))
            check("模式 = repair", state["mode"] == "repair", str(state["mode"]))
            check("价格行显示「修理费」", "修理费" in (state["price"] or ""),
                  str(state["price"]))
            check("背包自动打开在右侧 475px",
                  state["bag"] == "block" and state["left"] == "475px", str(state))

            # ---- 2. 物品说明显示幸运（回答「幸运+1 体现在哪」）------------
            desc = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc._showItemDesc({name: '测试木剑', stdMode: 5, weight: 2,
                    lucky: 2, durability: 1000, durabilityMax: 4000,
                    dc: 2, dcMax: 5});
                return sc.bagDescEl ? sc.bagDescEl.textContent : '';
            }""")
            check("武器说明里显示「幸运+2」", "幸运+2" in desc, desc)

            # ---- 3. 拿起背包物品 → 点投放框 → 问价 -----------------------
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                const cell = sc.bagGridEl.querySelector('[data-make-index="200001"]');
                cell.click();                 // 单击 = 拿起
                sc.shopSpotEl.click();        // 点圈 = 放进待修物品
            }""")
            sent = page.evaluate("window.__sent")
            check("放入投放框后发 queryRepairCost",
                  any(s["t"] == "queryRepairCost" and s["m"]["makeIndex"] == 200001
                      and s["m"]["mode"] == "repair" for s in sent), str(sent))
            check("放进圈后物品图标位置存在（shopSellItem）",
                  page.evaluate("!!window.__sceneManager.current.shopSellItem"), "")

            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                sc.onMessage('sellPrice', {type: 'sellPrice', npcId: 1,
                    makeIndex: 200001, price: 123});
            }""")
            price = page.evaluate("window.__sceneManager.current.shopPriceEl.textContent")
            check("问价结果显示「修理费: 123 金币」",
                  "修理费" in price and "123" in price, price)

            # ---- 4. 点 OK → 发 repairItem；repairResult → 清圈 ------------
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                const ok = Array.from(sc.shopPanel.children).find(
                    el => el.style.left === '85px' && el.style.top === '150px');
                ok.click();
            }""")
            sent = page.evaluate("window.__sent")
            check("点 OK 发 repairItem(mode=repair)",
                  any(s["t"] == "repairItem" and s["m"]["makeIndex"] == 200001
                      and s["m"]["mode"] == "repair" for s in sent), str(sent))

            page.evaluate("""() => {
                window.__sceneManager.current.onMessage('repairResult',
                    {type: 'repairResult', ok: true, makeIndex: 200001});
            }""")
            cleared = page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                return {item: sc.shopSellItem, spot: sc.shopSpotEl.innerHTML};
            }""")
            check("修理完成后投放框清空",
                  cleared["item"] is None and cleared["spot"] == "", str(cleared))

            # ---- 5. 特殊修理也开框且带 s_repair 模式 ----------------------
            page.evaluate("""() => {
                const sc = window.__sceneManager.current;
                window.__sent.length = 0;
                sc.onMessage('npcSay', {type: 'npcSay', npcId: 1, name: '铁匠',
                    text: '特殊修理', shop: 's_repair'});
                const cell = sc.bagGridEl.querySelector('[data-make-index="200001"]');
                cell.click();
                sc.shopSpotEl.click();
            }""")
            sent = page.evaluate("window.__sent")
            check("@s_repair 投放后发 queryRepairCost(mode=s_repair)",
                  any(s["t"] == "queryRepairCost" and s["m"]["mode"] == "s_repair"
                      for s in sent), str(sent))
            check("特殊修理模式已记录",
                  page.evaluate("window.__sceneManager.current.shopRepairMode")
                  == "s_repair")

            check("浏览器无 pageerror", not errors, str(errors[:3]))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    failed = [r for r in results if not r[1]]
    print(f"\n合计 {len(results) - len(failed)} PASS / {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
