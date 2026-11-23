"""实验：换图时资源是否重复下载（对公网实例 http://139.224.24.88:8080）。

复现用户场景：比奇城外（tiles 已加载）→ 进比奇矿区 D401 → 出矿区回比奇。
测量点：
  A. 每段期间实际发出的 /resources/** 网络请求数与字节（response 监听）；
  B. 同 URL 被请求 >=2 次的清单（重复下载直接证据）；
  C. scheduler / OPFS / IDB 命中统计增量；
  D. 期间是否有 JS 报错。

用法（Playwright 只在 3.9）：
  D:/Python39/python.exe scripts/debug_map_switch_cache.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

from playwright.sync_api import sync_playwright

BASE = os.environ.get("JOOTM2_BASE", "http://139.224.24.88:8080")
USER = os.environ.get("JOOTM2_USER", "player")
PWD = os.environ.get("JOOTM2_PWD", "123456")
CHAR = os.environ.get("JOOTM2_CHAR", "TestHero")

# ---- 网络观测（按“窗口”分桶：enter=进矿区, leave=出矿区回比奇）----
net = {"n": 0, "bytes": 0, "urls": defaultdict(int), "win": "boot"}
win_stats = {}


def snapshot(label):
    win_stats[label] = {
        "n": net["n"], "bytes": net["bytes"],
        "urls": dict(net["urls"]),
        "sched": None, "opfs": None,
    }


def on_response(resp):
    if "/resources/" not in resp.url:
        return
    try:
        size = int(resp.headers.get("content-length") or 0)
    except Exception:
        size = 0
    net["n"] += 1
    net["bytes"] += size
    net["urls"][resp.url.split("?")[0].rsplit("/", 1)[-1]] += 1


def sched_stats(page):
    try:
        return page.evaluate("() => ({s: window.__scheduler.stats(), o: window.__opfs && window.__opfs()})")
    except Exception as e:
        return {"err": str(e)}


def gm_move(page, target):
    page.evaluate(
        "(t) => window.__conn.send('say', { text: t })", target)


def wait_play(page, timeout=60):
    page.wait_for_function(
        "() => window.__sceneManager && window.__sceneManager.current "
        "&& window.__sceneManager.current.constructor.name === 'PlayScene' "
        "&& window.__sceneManager.current.self", timeout=timeout * 1000)


def main() -> int:
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", on_response)

        # ---- 登录进游戏 ----
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("window.__conn && window.__conn.connected === true",
                               timeout=60000)
        page.wait_for_selector("input[type=text]", timeout=30000)
        page.fill("input[type=text]", USER)
        page.fill("input[type=password]", PWD)
        page.press("input[type=password]", "Enter")
        try:
            page.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
                timeout=20000)
        except Exception:
            # 免密 token 可能直接把登录页跳过？打印当前场景
            cur = page.evaluate("() => window.__sceneManager.current && window.__sceneManager.current.constructor.name")
            print("!! 未到选角场景，当前：", cur)
            raise
        page.evaluate(f"() => window.__conn.send('enterGame', {{name:'{CHAR}'}})")
        try:
            page.wait_for_function(
                "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'",
                timeout=15000)
            page.evaluate(f"() => window.__conn.send('noticeOk', {{name:'{CHAR}'}})")
        except Exception:
            pass
        wait_play(page, timeout=180)
        print("已进入游戏（比奇省），等首屏稳定 12s …")
        time.sleep(12)
        st0 = sched_stats(page)
        snapshot("start")
        map0 = page.evaluate("() => window.__sceneManager.current.snapshot.map")
        print("当前地图:", map0)

        # ---- 进入比奇矿区 D401 ----
        net["win"] = "enter"
        n_before = net["n"]
        gm_move(page, "@移动 D401 175 106")
        time.sleep(10)   # 换图 + 预取窗口
        enter = {k: v for k, v in win_stats.items()}
        snap_enter = {"n": net["n"] - n_before, "bytes": net["bytes"]}
        st1 = sched_stats(page)
        dup_enter = {u: c for u, c in net["urls"].items()}
        win_stats["enter"] = {"req": net["n"], "bytes": net["bytes"],
                              "sched": st1, "urls": dup_enter}
        pos = page.evaluate("() => {const w=window.__sceneManager.current; return {map: w.snapshot.map.name, x:w.self.x, y:w.self.y};}")
        print("换图后:", pos, f"本段新增网络请求 {net['n'] - n_before}")

        # 再等一会看后台预取（opfsPrefetch 2.5s 延迟 + 串行）
        time.sleep(15)
        st1b = sched_stats(page)
        win_stats["enter_after"] = {"req": net["n"], "bytes": net["bytes"], "sched": st1b}

        # ---- 出矿区回比奇外面（重复访问已看过的 tiles）----
        n2_before = net["n"]
        b2_bytes = net["bytes"]
        gm_move(page, "@移动 0 330 270")
        time.sleep(10)
        st2 = sched_stats(page)
        win_stats["leave"] = {"req": net["n"] - n2_before,
                              "bytes": net["bytes"] - b2_bytes,
                              "sched": st2}
        pos = page.evaluate("() => {const w=window.__sceneManager.current; return {map: w.snapshot.map.name, x:w.self.x, y:w.self.y};}")
        print("回比奇:", pos, f"本段新增网络请求 {net['n'] - n2_before}")
        time.sleep(12)
        st2b = sched_stats(page)
        win_stats["leave_after"] = {"req": net["n"], "bytes": net["bytes"], "sched": st2b}

        browser.close()

    # ---- 汇总 ----
    print("\n===== 汇总 =====")
    print("进矿区段：", win_stats["enter"]["req"] - win_stats["start"]["n"], "请求")
    print("  sched:", json.dumps(win_stats["enter"]["sched"]["s"], ensure_ascii=False))
    print("  opfs :", json.dumps(win_stats["enter"]["sched"]["o"], ensure_ascii=False))
    dups = {u: c for u, c in sorted(win_stats["enter"]["urls"].items(), key=lambda x: -x[1]) if c >= 2}
    print("  同URL重复请求(≥2次):", json.dumps(dups, ensure_ascii=False))
    print("出矿区回比奇段：", win_stats["leave"]["req"], "请求")
    print("  sched:", json.dumps(win_stats["leave"]["sched"]["s"], ensure_ascii=False))
    print("  opfs :", json.dumps(win_stats["leave"]["sched"]["o"], ensure_ascii=False))
    print("回比奇后 12s:")
    print("  sched:", json.dumps(win_stats["leave_after"]["sched"]["s"], ensure_ascii=False))
    print("  opfs :", json.dumps(win_stats["leave_after"]["sched"]["o"], ensure_ascii=False))
    print("全程 URL 请求次数 top:", json.dumps(
        {u: c for u, c in sorted(net["urls"].items(), key=lambda x: -x[1])[:12]}, ensure_ascii=False))
    print("总请求:", net["n"], "总字节(压缩后≈):", net["bytes"])
    if errors:
        print("JS 报错:", errors[:5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
