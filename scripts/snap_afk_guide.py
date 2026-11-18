#!/usr/bin/env python3
"""离线修炼指导文档的配图截图（一次性工具，非回归）。

用 D:/Python39/python.exe 运行（Playwright 在 3.9）：
    D:/Python39/python.exe scripts/snap_afk_guide.py

产出 docs/images/afk-guide/*.png：
  entry.png    游戏主界面（托管条 + 「离线」按钮）
  panel.png    离线修炼配置面板
  report.png   登录时的离线修炼报告弹窗
  npc.png      玛法领路人对话（领取离线修炼补助菜单）
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
OUT = ROOT / "docs" / "images" / "afk-guide"
DB = ROOT / "data" / "_tmp_snap_afk.db"
CFG = ROOT / "data" / "_tmp_snap_afk.properties"

CFG.write_text("afk.minLevel=1\nafk.excludedMaps=\n", encoding="utf-8")
OUT.mkdir(parents=True, exist_ok=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def insert_report(db_path: Path, char_name: str) -> None:
    sql = ("INSERT INTO afk_reports(character_id, started_at, ended_at, minutes,"
           " exp_gained, kills, gold_delta, deaths, reason, notice)"
           " VALUES((SELECT id FROM characters WHERE name=?), ?, ?, 120,"
           " 4820, 96, 1650, 1, '修炼时长已用完，离线修炼结束', '')")
    for attempt in range(20):
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute(sql, (char_name, int(time.time() * 1000) - 7200_000,
                               int(time.time() * 1000)))
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError:
            time.sleep(0.25)
    raise RuntimeError("写入 afk_reports 失败")


def shot(page, name: str) -> None:
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def main() -> int:
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    for p in DB.parent.glob(DB.name + "*"):
        p.unlink()
    env = os.environ.copy()
    env["JOOTM2_CONFIG"] = str(CFG)
    proc = subprocess.Popen(
        [PY310, "-m", "pyserver", "--host", "127.0.0.1", "--port", str(port),
         "--db", str(DB), "--no-resource-index", "--no-bots"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    try:
        end = time.time() + 30
        while time.time() < end:
            try:
                urllib.request.urlopen(base + "/api/status", timeout=2)
                break
            except Exception:
                time.sleep(0.2)
        sys.path.insert(0, str(ROOT / "scripts"))
        from smoke_ws import WS
        # GM 账号 player：@移动 传送必需（普通账号 GM 命令被拒）
        steps = {
            "register": {"type": "register", "username": "player", "password": "123456"},
            "login": {"type": "login", "username": "player", "password": "123456"},
            "listCharacters": {"type": "listCharacters"},
            "createCharacter": {"type": "createCharacter", "name": "向导截图",
                                "job": 0, "sex": 0, "hair": 1},
        }
        pre = WS("127.0.0.1", port, "/ws", timeout=0.5)
        for name in ("register", "login", "listCharacters", "createCharacter"):
            pre.send(steps[name])
            pre.drain(16)
        pre.close()
        char = steps["createCharacter"]["name"]
        insert_report(DB, char)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1024, "height": 800})
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function(
                "window.__conn && window.__conn.connected === true", timeout=30000)
            page.evaluate("() => window.__conn.send('login', {username: 'player', password: '123456'})")
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

            # 1. 登录报告弹窗（先截，截完关闭）
            page.wait_for_selector("#afkReportPanel", timeout=10000)
            shot(page, "report")
            page.click("#afkReportOk")

            # 2. 主界面（托管条 + 离线按钮）
            page.wait_for_timeout(300)
            shot(page, "entry")

            # 3. 离线修炼面板
            page.click("#afkBtn")
            page.wait_for_selector("#afkPanel", state="visible", timeout=5000)
            page.wait_for_function(
                "document.querySelector('#afkBalance') && "
                "document.querySelector('#afkBalance').textContent.length > 2",
                timeout=10000)
            page.fill("#afkMinutes", "240")
            page.wait_for_timeout(200)
            shot(page, "panel")

            # 4. 玛法领路人对话：GM 传送到 NPC 旁，点 NPC
            page.fill("#afkMinutes", "10")  # 还原，避免留下奇怪状态（截图已拍完）
            page.click("#afkBtn")           # 关面板
            page.click("#screen")           # 聚焦画布
            page.evaluate("""() => {
                const s = window.__sceneManager.current;
                s.chatInput.style.display = 'block';
                s.chatInput.focus();
            }""")
            # 玛法领路人 @main 有 checklevel 3 门槛，先升到 15 级再传送
            page.keyboard.type("@等级 15")
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            page.keyboard.type("@移动 0 278 610")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            # 用场景实体数据换算 NPC 锚点，再用 _actorAtScreen 逆向扫描
            # 找出能命中 NPC 判定框的精确屏幕点（纯查询，不触发移动）
            page.evaluate("""() => {
                const s = window.__sceneManager.current;
                let sx = 0, sy = 0;
                for (const e of s.entities.values()) {
                    if (e.type === 'npc') {
                        sx = s.mapActor.screenX(e.x);
                        sy = s.mapActor.screenY(e.y);
                        break;
                    }
                }
                let hit = null;
                outer:
                for (let mx = sx - 60; mx <= sx + 60; mx += 6) {
                    for (let my = sy - 110; my <= sy + 40; my += 6) {
                        if (s._actorAtScreen(mx, my, 'npc')) { hit = [mx, my]; break outer; }
                    }
                }
                const r = document.getElementById('screen').getBoundingClientRect();
                const k = r.width / document.getElementById('screen').width;
                window.__npcClick = hit ? [r.left + hit[0] * k, r.top + hit[1] * k] : null;
            }""")
            pos = page.evaluate("window.__npcClick")
            print("npc hit point:", pos)
            page.evaluate("""() => {
                window.__sent = [];
                window.__npcSay = [];
                const orig = window.__conn.send.bind(window.__conn);
                window.__conn.send = (t, d) => { window.__sent.push([t, d]); return orig(t, d); };
                window.__conn.on('npcSay', m => window.__npcSay.push(m));
            }""")
            if pos:
                page.mouse.click(pos[0], pos[1])
            page.wait_for_timeout(900)
            say = page.evaluate("window.__npcSay")
            print("npcSay count:", len(say))
            shot(page, "npc")
            print("npc dialog open:", len(say) > 0)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
