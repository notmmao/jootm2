## §110 移动端触屏控制移植自幸存者分支（2026-11-18，用户要求）

**用户要求**：把幸存者分支的手机/平板触屏优化平移到 pyserver 主线；**只在检测到移动触屏设备时才出现虚拟控制 UI**，桌面端必须零干扰。

**设备检测（关键差异）**：幸存者版用 `ontouchstart || maxTouchPoints>0`，带触摸屏的笔记本会误触发；主线改为严格判据：
1. URL `?touch=1` / `?touch=0` 强制覆盖（测试与触屏笔记本手动开启用）；
2. 主判据 `matchMedia('(pointer: coarse)').matches && navigator.maxTouchPoints > 0`；
3. 兜底移动端 UA + 触点/`ontouchstart`。
判据为 false 时 `TouchControls` 构造函数**直接返回**：不建 DOM、不装监听、不加 `body.touch`。

**实现**：
- 新文件 `frontend/input/TouchControls.mjs`（零引擎改动）：浮动摇杆（Pointer Events + `setPointerCapture`，只跟第一根 pointerId，触点经 `viewport.toLocal()` 换算），输出模拟量 `{dx,dy,mag}`；8 方向映射 `[2,3,4,5,6,7,0,1]`，死区 0.24，推力 ≥0.72 自动跑。右侧 9 个按钮：攻击/拾取/药/技1~3/背包/地图/菜单。
- `frontend/index.html`：viewport 禁缩放 + `viewport-fit=cover`；`body.touch` 禁长按选择/回弹；`#screen`/`#touch-ui` 用 `touch-action:none`，`#gui` 保留 `pan-y`；竖屏横屏提示。
- `frontend/scene/PlayScene.mjs`：`enter()` 装配实例（`window.__touchControls` 调试钩子）；`render()` 主循环加 `_tickTouchMove(now)`；`onExit()` 销毁并清 DOM/body class/window 钩子。**摇杆不本地位移**，而是转成 `dir` 复用既有 `move` 包与 `_lastMoveAt` 节流（`WALK_INTERVAL=540`/`RUN_INTERVAL=360`），与自动导航/锁定目标互斥、触发托管手动接管。按钮回调全部接到既有方法：`_touchAttack`（最近怪锁定追击/空挥）、`_touchPick`（脚下或 1 格内）、`_castFromSlot(1..3)`、`_useQuickSlot(0)`、`_toggleBag`、`_cycleMapView`、`_closeTopWindow || _toggleSystemMenu`。

**验证**：新脚本 `scripts/test_touch_controls.py` **33/33**：桌面无 UI / 移动自动出 UI / `?touch=0` 关闭 / `?touch=1` 强开 / 8 方向 / 走跑判定 / 真实指针事件发包 / 手机 scale≈0.65 下坐标换算 / 7 类按钮接线 / 场景退出清理 / 无 JS 异常。回归：`test_input_shortcuts` 9/9、`test_bigmap_nav` 19/19、`test_autopilot_ui` 13/13、`test_window_close` 12/12、`test_multiplayer_ui` 16/16、`test_pyserver` 128/0、`test_tick_systems` 180/0。截图 `data/screenshots/touch_mobile.png`（844×390 横屏）、`touch_desktop_forced.png`（?touch=1，800×600 逻辑）。

**注意**：① 纯前端改动，Ctrl+F5 强刷即可，无需重启服务端。② 触屏笔记本默认不显示虚拟 UI，需加 `?touch=1`。③ 虚拟按钮与原版 HUD 下半部图标有少量视觉叠压（移动端底部空间有限），功能互不干扰；聊天输入在触屏下仍依赖点击原版输入框。④ `docs/autopilot-hud.png` 在回归中被 `test_autopilot_ui` 重生成，属主线独立工作，未纳入本次提交。

**提交**：按 CLAUDE.md 标签拆分 `[前端]` 实现 + `[测试]` 专项脚本 + `[文档]` 本节。

