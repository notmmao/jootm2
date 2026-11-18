# 离线修炼 WS 协议（前端对接用）

> 服务端已实现（`pyserver/afk.py` + `service.py::_afk`），本协议已定型。
> 前端实现见 PlayScene 离线修炼面板；测试参考 `scripts/test_autopilot_ui.py` 的
> Playwright 模式（Playwright 装在 `D:/Python39/python.exe`）。

## 客户端 → 服务端：`afk`（单消息三动作）

```json
{"type": "afk", "action": "info"}
{"type": "afk", "action": "start", "minutes": 240, "preset": "balanced"}
{"type": "afk", "action": "stop"}
```

- `minutes`：计划修炼分钟数（下线后生效）。
- `preset`：策略档，取值与在线托管一致：`safe`（安全）/ `balanced`（均衡）/ `efficient`（效率）。

## 服务端 → 客户端：`afkState`（start/stop/info 的回包）

```json
{
  "type": "afkState", "ok": true, "reason": "", "action": "info",
  "balance": 240,          // 修炼时长余额（分钟）
  "claimToday": 0,         // 今日已领取次数（玛法领路人）
  "claimPerDay": 1,        // 每日限领次数
  "claimable": true,       // 今日是否还可领取
  "claimMinutes": 240,     // 单次领取时长
  "poolCap": 720,          // 余额上限
  "usedToday": 0,          // 今日已用修炼分钟
  "dailyLimit": 360,       // 每日上限
  "dailyRemaining": 360,   // 今日剩余可开分钟
  "minLevel": 15,          // 开启等级门槛
  "excludedMaps": ["0"],   // 黑名单地图（禁开列表）
  "maxAgents": 10,         // 全服离线代理上限
  "perMapLimit": 2,        // 每图并发上限
  "running": 0,            // 当前全服离线代理数
  "plan": null,            // 待生效计划 {"minutes":240,"preset":"balanced"} 或 null
  "active": false          // 是否正在离线修炼中（对本人恒 false，见时已是离线态）
}
```

失败时 `ok:false` + `reason` 中文提示（等级不足/地图禁止/余额不足等），面板直接展示。

## 服务端 → 客户端推送：`afkReport`（登录时下发，可能多条）

```json
{
  "type": "afkReport",
  "reports": [
    {"started_at": 1731900000000, "ended_at": 1731903600000, "minutes": 60,
     "exp_gained": 1200, "kills": 34, "gold_delta": 500, "deaths": 0,
     "reason": "修炼时长已用完，离线修炼结束", "notice": "（含重启补偿 60 分钟）"}
  ]
}
```

- 同一内容还会以 `[离线修炼] ...` 系统聊天消息发一份（聊天框可见）。
- 前端收到 `afkReport` 弹一次汇总弹窗即可（分钟数 ≤0 的条目是纯通知，如重启续挂）。

## 语义要点（面板文案需要表达）

1. **计划在下线后生效**：设定计划后角色仍在线正常玩；断线/登出瞬间自动开始修炼。
2. **地图限定**：只允许在当前地图开启（面板显示「当前地图：xxx」；黑名单图禁开）。
   不做跨图选择（v2 补给闭环才引入跨图导航）。
3. **余额**：找比奇城玛法领路人领取（每日 1 次、每次 4 小时、上限 12 小时；新号送 4 小时）。
4. 开启失败常见原因：等级 < `minLevel`、当前图在 `excludedMaps`、余额 <10 分钟、
   今日 `dailyRemaining` 不足、全服/每图名额满。

## UI 要求

- 入口：托管状态条旁加「离线」按钮（与现有托管按钮风格一致，不新增快捷键）。
- 面板内容：余额 / 今日已用+上限 / 当前地图与是否允许 / 策略档下拉（复用托管三档）/ 
  时长输入（分钟，默认 240）/ 设定计划（或取消计划）按钮 / 简要说明文案。
- DOM 覆盖层遵守项目约定：**不要 z-index:-1**；面板普通流式覆盖即可，参照大地图面板
  `#bigMapPanel` 的做法。
- 测试：新增 `scripts/test_afk_ui.py`（Playwright，`D:/Python39/python.exe`，
  参考 `scripts/test_autopilot_ui.py` 起服与连 WS 的方式）：
  打开面板 → info 回包渲染余额 → 设定计划（start ok）→ 服务端回读 plan 不为空 →
  取消计划 → afkReport 推送弹窗渲染。
