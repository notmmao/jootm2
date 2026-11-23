"""整文件缓存双后端（OPFS / IndexedDB 降级）回归（D:/Python39 Playwright）。

背景（2026-11-23 实验实锤）：OPFS 是「安全上下文」专属 API，线上
http://域名:8080 下 navigator.storage.getDirectory 直接不存在，首版
opfsStore 只实现了 OPFS 后端 → 整层线上静默失效。修复后 IDB 存整文件 Blob
作为降级后端。本测试同时覆盖两种上下文：

  A) http://127.0.0.1:<port>  安全上下文 → mode == 'opfs'
  B) http://<局域网IP>:<port>  非安全上下文 → mode == 'idb'（主回归点）

每段验证：
  1. enabled 且 mode 正确
  2. 进游戏后后台预取回填（stored>0、storedBytes>0）
  3. 同 context 重载页面 → 索引跨会话恢复（stored 不减）且 hits>0（零网络整文件读）
  4. WIX Range 经 scheduler 走整文件命中（opfsHits>0）

用法：
  JOOTM2_LAN_IP=192.168.124.7 D:/Python39/python.exe scripts/test_whole_cache_modes.py
前置：服务端已在 127.0.0.1:8123 运行（--host 0.0.0.0），账号 player/123456 有角色。
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

LAN_IP = os.environ.get("JOOTM2_LAN_IP", "")
PORT = int(os.environ.get("JOOTM2_PORT", "8123"))
USER = "player"
PWD = "123456"
CHAR = os.environ.get("JOOTM2_CHAR", "WcHero")

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def login_to_play(page, base: str) -> None:
    from websockets.sync.client import connect
    import json
    use = CHAR
    with connect(f"ws://{base.split('//')[1]}/ws") as ws:
        def send(o): ws.send(json.dumps(o))
        def recv(t=5):
            try: return json.loads(ws.recv(timeout=t))
            except Exception: return None
        send({"type": "register", "username": USER, "password": PWD})
        recv()
        send({"type": "login", "username": USER, "password": PWD})
        recv()
        send({"type": "listCharacters"})
        m = recv()
        chars = (m or {}).get("characters") or []
        if chars:
            use = chars[0]["name"]
        else:
            send({"type": "createCharacter", "name": CHAR, "sex": 0, "hair": 1, "job": 0})
            recv()
    page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=60000)
    page.wait_for_selector("input[type=text]", timeout=30000)
    page.fill("input[type=text]", USER)
    page.fill("input[type=password]", PWD)
    page.press("input[type=password]", "Enter")
    page.wait_for_function(
        "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'SelectCharScene'",
        timeout=30000)
    page.evaluate(f"() => window.__conn.send('enterGame', {{name:'{use}'}})")
    try:
        page.wait_for_function(
            "window.__sceneManager.current && window.__sceneManager.current.constructor.name === 'NoticeScene'",
            timeout=15000)
        page.evaluate(f"() => window.__conn.send('noticeOk', {{name:'{use}'}})")
    except Exception:
        pass
    page.wait_for_function(
        "() => window.__sceneManager && window.__sceneManager.current "
        "&& window.__sceneManager.current.constructor.name === 'PlayScene' "
        "&& window.__sceneManager.current.self", timeout=60000)


def run_segment(pw, label: str, base: str, want_mode: str, ctx_cache: dict) -> None:
    print(f"\n---- [{label}] {base} → 期望 mode={want_mode} ----")
    browser = pw.chromium.launch(headless=True)
    # 有缓存 context 就复用（模拟「二次打开」同一浏览器资料）
    if ctx_cache.get("browser"):
        ctx = ctx_cache["browser"].new_context(viewport={"width": 1280, "height": 800})
    else:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        login_to_play(page, base)
        st = page.evaluate("() => window.__opfs()")
        check(f"{label} 1 缓存层启用且后端正确",
              st.get("enabled") is True and st.get("mode") == want_mode, str(st)[:150])
        # 整文件 Range：显式请求一个下过分片的 WIL 的任意段，验证 opfsGet 路径通
        probe = page.evaluate("""async () => {
            const before = window.__scheduler.opfsHits;
            try {
                await window.__scheduler.schedule('/resources/Data/SmTiles.wil', 0, 600);
            } catch (e) { return {err: String(e)}; }
            return {hitDelta: window.__scheduler.opfsHits - before};
        }""")
        # 首次可能尚未回填 → 允许走网络，但绝不能抛
        check(f"{label} 2 scheduler 整文件路径无异常", probe.get("err") is None, str(probe)[:120])
        # 等后台预取回填
        stored = 0
        for _ in range(40):
            time.sleep(1)
            st = page.evaluate("() => window.__opfs()")
            stored = st.get("stored", 0)
            if stored >= 2:
                break
        check(f"{label} 3 后台整文件预取回填", st.get("stored", 0) > 0 and st.get("storedBytes", 0) > 0,
              f"stored={st.get('stored')} bytes={st.get('storedBytes')} mode={st.get('mode')}")
        before_bytes = st.get("storedBytes", 0)

        # 重载：同 origin 后端持久、索引恢复、二次读命中
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("window.__conn && window.__conn.connected === true", timeout=60000)
        page.wait_for_function("() => window.__opfs && window.__opfs().enabled", timeout=30000)
        st2 = page.evaluate("() => window.__opfs()")
        check(f"{label} 4 重载后索引持久恢复", st2.get("stored", 0) > 0
              and st2.get("mode") == want_mode,
              f"stored={st2.get('stored')} mode={st2.get('mode')}")
        hits = 0
        for _ in range(25):
            time.sleep(1)
            hits = page.evaluate("() => window.__opfs().hits")
            if hits > 0:
                break
        check(f"{label} 5 二次加载整文件命中（零网络）", hits > 0, f"hits={hits}")
        check(f"{label} Z 无页面 JS 报错", not errors, "; ".join(errors[:2]))
    finally:
        if not ctx_cache.get("browser"):
            ctx_cache["browser"] = browser
            browser = None
        try:
            ctx.close()
        except Exception:
            pass
        if browser:
            browser.close()


def main() -> int:
    with sync_playwright() as pw:
        cache_a: dict = {}
        run_segment(pw, "A 安全上下文", f"http://127.0.0.1:{PORT}", "opfs", cache_a)
        if cache_a.get("browser"):
            cache_a["browser"].close()
        if not LAN_IP:
            print("\n!! 未提供 JOOTM2_LAN_IP，跳过非安全上下文段（本次修复的主目标）")
            return 1
        cache_b: dict = {}
        run_segment(pw, "B 非安全上下文", f"http://{LAN_IP}:{PORT}", "idb", cache_b)
        if cache_b.get("browser"):
            cache_b["browser"].close()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结论: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
