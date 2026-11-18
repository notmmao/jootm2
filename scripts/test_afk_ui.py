#!/usr/bin/env python3
"""浏览器端验证：离线修炼面板 + afkReport 弹窗的真实渲染（Playwright，Python 3.9）。

服务端仍强制用 3.10 启动（本项目约定），本脚本自身用装了 Playwright 的
D:/Python39/python.exe 运行：

    D:/Python39/python.exe scripts/test_afk_ui.py

用临时 server.properties 把 afk.minLevel 降为 1、清空 afk.excludedMaps，
让新冒烟角色（1 级、出生图）就能设定离线修炼计划。
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY310 = r"D:/Python310/python.exe"
DB = ROOT / "data" / "_tmp_afk_ui.db"
CFG = ROOT / "data" / "_tmp_afk_ui.properties"

# 降门槛 + 清空黑名单图：冒烟角色 1 级、出生图也能开计划
CFG.write_text(
    "afk.minLevel=1\n"
    "afk.excludedMaps=\n",
    encoding="utf-8")


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


def insert_report(db_path: Path, char_name: str) -> None:
    """给角色预置一条未下发的离线修炼结算报告（登录时服务端会推送 afkReport）。"""
    sql = ("INSERT INTO afk_reports(character_id, started_at, ended_at, minutes,"
           " exp_gained, kills, gold_delta, deaths, reason, notice)"
           " VALUES((SELECT id FROM characters WHERE name=?), ?, ?, 60,"
           " 1200, 34, 500, 0, '修炼时长已用完，离线修炼结束', '（含重启补偿 60 分钟）')")
    for attempt in range(20):     # 服务端可能正持写锁，重试直到成功
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute(sql, (char_name, int(time.time() * 1000) - 3600_000,
                               int(time.time() * 1000)))
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError:
            time.sleep(0.25)
    raise RuntimeError("写入 afk_reports 失败（数据库持续锁死）")


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
    env["JOOTM2_CONFIG"] = str(CFG)
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index",
         "--no-bots"],
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
        # 预建账号与角色：绕开登录场景「注册成功自动登录」与 3s 开门动画前
        # 消息丢失的问题，浏览器只走 login → 选角 → 公告 → 进游戏。
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for name in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[name])
            pre.drain(16)
        pre.close()
        insert_report(DB, char)
        errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("""() => {
                window.__msgs = [];
                window.__afkMsgs = [];
                window.__conn.on('*', m => {
                    if (m.type === 'afkState' || m.type === 'afkReport')
                        window.__afkMsgs.push(m);
                });
            }""")
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
            page.wait_for_selector("#afkBtn", timeout=25000)
            check("进入游戏且「离线」按钮已渲染", True)

            print("\n=== afkReport 登录推送弹窗 ===")
            page.wait_for_selector("#afkReportPanel", timeout=10000)
            rep_text = page.inner_text("#afkReportPanel")
            check("afkReport 弹窗已弹出", True)
            check("弹窗列出修炼分钟/经验/击杀/金币/死亡",
                  all(k in rep_text for k in ("60 分钟", "经验 +1200", "击杀 34", "金币 +500", "死亡 0")),
                  rep_text.replace("\n", " | "))
            page.click("#afkReportOk")
            page.wait_for_selector("#afkReportPanel", state="detached", timeout=5000)
            check("确认后弹窗关闭", True)

            print("\n=== 面板打开 / info 渲染 ===")
            page.click("#afkBtn")
            page.wait_for_selector("#afkPanel", state="visible", timeout=5000)
            check("点击「离线」打开面板", True)
            page.wait_for_function(
                "document.querySelector('#afkBalance') && "
                "document.querySelector('#afkBalance').textContent.includes('240')",
                timeout=10000)
            check("info 回包渲染余额（新号赠送 240）",
                  "240" in page.inner_text("#afkBalance"),
                  page.inner_text("#afkBalance"))
            check("渲染今日已用/每日上限",
                  "0 / 360" in page.inner_text("#afkUsed"),
                  page.inner_text("#afkUsed"))
            check("渲染当前地图且允许离线修炼",
                  "允许离线修炼" in page.inner_text("#afkMap"),
                  page.inner_text("#afkMap"))

            print("\n=== 设定计划 / 服务端回读 ===")
            page.fill("#afkMinutes", "60")
            page.select_option("#afkPreset", "efficient")
            page.click("#afkSetBtn")
            page.wait_for_function(
                "document.querySelector('#afkSetBtn').textContent === '取消计划'",
                timeout=10000)
            start_msgs = page.evaluate(
                "window.__afkMsgs.filter(m => m.type === 'afkState' && m.action === 'start')")
            last = start_msgs[-1] if start_msgs else {}
            check("start 回包 ok=true", last.get("ok") is True,
                  json.dumps(last, ensure_ascii=False)[:120])
            check("服务端回读 plan 不为空",
                  isinstance(last.get("plan"), dict) and last["plan"].get("minutes", 0) >= 10,
                  json.dumps(last.get("plan"), ensure_ascii=False))
            check("面板按钮已切换为「取消计划」", True)

            print("\n=== 取消计划 ===")
            page.click("#afkSetBtn")
            page.wait_for_function(
                "document.querySelector('#afkSetBtn').textContent === '设定计划'",
                timeout=10000)
            stop_msgs = page.evaluate(
                "window.__afkMsgs.filter(m => m.type === 'afkState' && m.action === 'stop')")
            last = stop_msgs[-1] if stop_msgs else {}
            check("stop 回包 ok=true 且 plan 为空",
                  last.get("ok") is True and last.get("plan") is None,
                  json.dumps(last, ensure_ascii=False)[:120])

            print("\n=== 失败原因显示（minutes=1 触发时长不足）===")
            page.fill("#afkMinutes", "1")
            page.click("#afkSetBtn")
            page.wait_for_function(
                "document.querySelector('#afkReason') && "
                "document.querySelector('#afkReason').textContent.length > 0",
                timeout=10000)
            reason = page.inner_text("#afkReason")
            check("ok:false 的 reason 显示在面板里", "10 分钟" in reason, reason)
            page.wait_for_timeout(300)

            if errors:
                print("  [页面 JS 异常] " + " | ".join(errors[:3]))
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
        try:
            CFG.unlink()
        except OSError:
            pass

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n结论: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
