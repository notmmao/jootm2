# 在线托管 AutoPilot 计划

> 一期范围：只做在线托管，保持简单。实施进度见文末。

## 一句话
玩家在线时把「刷怪 / 升级 / 捡宝」交给系统代打；账号登出、退出角色或断线立即停止，绝不离线升级。

## 功能定义
- 定位：手动玩法的补充，不是离线挂机。怀旧的打怪、买卖、交易仍由玩家自己做。
- 开关：游戏内一个按钮 / 快捷键，开启后显示一行 HUD「托管中 · 按任意键接管」。
- 手动优先：任何手动操作（移动 / 攻击 / 拾取 / 用药）立即暂停托管，需再次点击才恢复。
- 策略：管理员预置默认档（安全 / 均衡 / 效率）+ 按等级段练级路线，纯配置；玩家选档。
- 自动范围：选怪 → 寻路 → 攻击 → 拾取 → 喝药 → 背包满回城 → 死亡时停止。
- 不做：LLM / 密钥 / sidecar、断线自动重连与自动恢复、每小时经验上限、加速时钟、教练台 / 旁观页。

## 生命周期（硬约束）
- AutoPilot 挂在 WsSession / Player 内存对象上，由 `service.tick()` 驱动，**绝不落库**。
- 停止点唯一：`service.py:197 on_disconnect`；登出(`_logout:562`)、退回选角(`_back_to_select:571`)、断线(`net.py:516 finally`) 全部汇合于此，进入即清空。
- 断线期间不补经验；重登后必须由玩家显式再点一次托管。
- 可持久化：玩家选了哪一档（偏好）。不可持久化：托管中、当前目标、动作计时（运行状态）。

## 实施要领
1. **先修半开连接**（`net.py:_ws_loop`）：服务端每 15s 主动发 WS ping（浏览器原生自动回 pong，前端零改动），连续 45s 无任何帧即 `aclose()` → 走 `on_disconnect`。不修则 TCP 半开时服务端以为人还在线，「断线即停」是假的。
2. **共用时间锁**：托管走与手动完全相同的 540ms 走路 / 350ms 换图 / 攻击冷却，全部经现有 handler，不加速、不绕过校验。
3. **手动接管以服务端为准**：收到手动 move / attack / pickup / useItem 即清 AutoPilot；前端本地同步停发，双保险。
4. **新增 WS 类型要同步三处**：`MIGRATED_TYPES` / `_handlers` / `scripts/test_all_messages.py`（`service.py:158-162` 强校验，漏一处启动即 RuntimeError）。
5. 单 asyncio loop + 100ms tick，禁同步阻塞与线程池；托管玩家 session 必须非 None，否则该图刷怪休眠；同角色禁双开已有 `service.py:326`。

## 顺序与验收
- 步骤 0 半开连接修复 → 造「不关 socket 也不发帧」的连接，验证超时后连接被关、在线数归零、persist 触发；`test_all_messages.py` 回归不退化。
- 步骤 1 默认策略档 + AutoPilot 内核（绑会话、不落库、时间锁、手动暂停、on_disconnect 清理）。
- 步骤 2 游戏内开关 + 极简 HUD。
- 整体验收：开启 → 自动打怪升级 → 按任意键立即接管 → 退出角色 / 断线后经验不再变化；断线重登不自动恢复托管。

## 进度（全部完成）
- 步骤 0：`net.py` 半开连接探活（15s ping / 45s 无帧清场，前端零改动）；`test_ws_idle_timeout.py` 验证清场与不误踢。
- 步骤 1：新增 `pyserver/autopilot.py`（三档策略 + 等级段路线 + BFS 寻路 + 选怪/喝药/拾取/回城/熔断）；纯内存绑会话、共用 540ms 与攻速节拍、动作全走现有 handler、`on_disconnect` 清理；`service.py` 加 `autopilot` 消息、`/api/autopilot` 只读端点、`@托管` GM 命令。
- 步骤 2：`PlayScene.mjs` 加 P 快捷键 + 开关按钮 + 三档下拉 + 一行状态条；手动动作/鼠标点击立即接管；档位偏好只存 localStorage，重进游戏恒为未开启。
- 验收：`test_all_messages` 91 / `test_autopilot` 25 / `test_autopilot_ws` 16 / `test_autopilot_ui` 8，均 0 FAIL；真实挂机 60s：1→2 级、9 杀、HP 满、未意外停机；截图 `docs/autopilot-hud.png`。
- 后续（本期不做）：LLM 策略编辑、跨图自动导航、断线自动重连、加速时钟测试壳。
