"""OPFS 整文件缓存 + 载入失败重试 回归（D:/Python39 Playwright）。

前置：服务端已在 127.0.0.1:8098 运行（D:/Python310，默认库即可）。

覆盖：
1. 首开页面：登录场景正常、无 JS 报错；OPFS 可用（Chromium 支持）
2. 等待后台整文件预取：__opfs().stored > 0（下过分片的 WIL 被整文件回填）
3. 同 context 重载页面：OPFS 目录持久（IndexedDB 也持久，但 384MB 分片缓存
   装不下 1.2GB 全量素材——整文件缓存才是「载入一遍后直接读」的主路径）；
   二次加载 scheduler.opfsHits > 0
4. 载入失败重试：临时把某 WIL 请求掐坏（路由 502 两次后放行）——资源最终
   仍能解出（重试 2 次 + MapActor 5 秒过期重试兜底），不留永久空白
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8098"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- 1) 首开 ----
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "window.__conn && window.__conn.connected === true", timeout=60000)
        page.wait_for_function(
            "window.__sceneManager && window.__sceneManager.current "
            "&& window.__sceneManager.current.constructor.name === 'LoginScene'",
            timeout=60000)
        page.wait_for_function("() => !!window.__opfs", timeout=15000)
        op0 = page.evaluate("() => window.__opfs()")
        check("1a 首开登录场景正常", True)
        check("1b OPFS 受支持且已启用", op0.get("enabled") is True, str(op0)[:120])

        # ---- 2) 等后台预取（warmInBackground 会拉 WIX/开门帧等 → 分片下载完
        #         触发 opfsPrefetch 整文件回填；给它 45 秒）----
        stored = 0
        for _ in range(45):
            time.sleep(1)
            st = page.evaluate("() => window.__opfs()")
            stored = st.get("stored", 0)
            if stored >= 3:
                break
        st = page.evaluate("() => window.__opfs()")
        check("2 后台整文件预取回填 OPFS", st.get("stored", 0) > 0,
              f"stored={st.get('stored')} bytes={st.get('storedBytes')} "
              f"prefetches={st.get('prefetches')} pending={st.get('pending')}")

        # ---- 3) 重载：同 origin 的 OPFS 持久，整文件命中零网络 ----
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "window.__conn && window.__conn.connected === true", timeout=60000)
        page.wait_for_function("() => !!window.__opfs && window.__opfs().enabled",
                               timeout=30000)
        st = page.evaluate("() => window.__opfs()")
        check("3a OPFS 跨页面重载持久（索引恢复）", st.get("stored", 0) > 0,
              f"stored={st.get('stored')} bytes={st.get('storedBytes')}")
        # 登录场景空闲预热会重拉 WIX/MAP 等整文件 → loadWholeBytes 直接命中 OPFS
        op_hits = 0
        for _ in range(20):
            time.sleep(1)
            op_hits = page.evaluate("() => window.__opfs().hits")
            if op_hits > 0:
                break
        check("3b 二次加载整文件命中 OPFS（零网络）", op_hits > 0,
              f"hits={op_hits}")
        sched2 = page.evaluate("() => window.__scheduler.stats()")
        check("3c scheduler 正常出数（未抛错）", sched2 is not None,
              f"req={sched2.get('totalRequests')} idb={sched2.get('cacheHits')} "
              f"opfs={sched2.get('opfsHits')}")

        # ---- 4) 失败重试：对某 WIL 的越界 Range 请求，route 先掐两次 502
        #         再放行 → retries≥2 且最终成功 resolve（瞬时故障自愈）。
        #         第二个 probe 全程 502 → 重试耗尽后 reject（不卡死）。----
        state = {"n": 0}
        state2 = {"n": 0}

        def route_handler(route):
            url = route.request.url
            rng = route.request.headers.get("range", "")
            if "Mon1.wil" in url and rng.startswith("bytes=900000000"):
                if state["n"] < 2:
                    state["n"] += 1
                    route.fulfill(status=502, body="")
                    return
            if "Mon2.wil" in url and rng.startswith("bytes=900000000"):
                state2["n"] += 1
                route.fulfill(status=502, body="")
                return
            route.continue_()

        page.route("**/resources/**", route_handler)
        probe = page.evaluate("""async () => {
            const before = window.__scheduler.retries || 0;
            let err = null;
            try {
                await window.__scheduler.schedule('/resources/Data/Mon1.wil',
                    900000000, 900000040);
            } catch (e) { err = String(e && e.message || e); }
            return { retried: (window.__scheduler.retries || 0) - before, err };
        }""")
        check("4a 瞬时失败自动重试后成功", (probe.get("retried") or 0) >= 2
              and probe.get("err") is None,
              f"probe={probe} attempts={state['n']}")
        probe2 = page.evaluate("""async () => {
            let err = null;
            try {
                await window.__scheduler.schedule('/resources/Data/Mon2.wil',
                    900000000, 900000040);
            } catch (e) { err = String(e && e.message || e); }
            return err;
        }""")
        check("4b 持续失败重试耗尽后 reject（不卡死）", bool(probe2),
              str(probe2)[:60])
        page.unroute("**/resources/**")

        check("Z 无页面 JS 报错", not errors, "; ".join(errors[:2]))
        browser.close()
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结论: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
