# jootm2 交接归档 d

> 从 HANDOFF-PYSERVER.md 归档的历史轮次（2026-11-13 重构，逐字保留、只读不改）。
> 内容：§35~§38：音效全量对齐 / 第 10~12 轮反馈（遮挡半透明·刷怪密度·MISS 音·矿洞视野等）

---

## §35 音效全量对齐：解捆像喝药声（2026-11-12）

用户反馈第 9 条：随机传送包解捆的**声音像喝药**，并要求顺带检查其他音效。

### 35.1 根因：不分 StdMode 一律播喝药音

原版 `SoundUtil.pas:313`：

```pascal
procedure ItemUseSound(StdMode: Byte);
begin
  case StdMode of
    0: PlaySound(s_click_drug);              // 108 点药声
    1, 2: PlaySound(s_eat_drug);             // 107 喝药声
  else
    ;                                        // ← 其余全部静默
  end;
end;
```

我们此前在双击背包 / 快捷栏使用时**无条件** `sound.play(SFX.eatDrug)`，
于是解捆包（StdMode 31）、技能书（4）、卷轴（3）全成了喝药声。
新增 `itemUseSound(stdMode)` 照抄 case，返回 `0` 表示原版这里没声音，调用方 `if (sfx) play(sfx)`。

顺带修好两处同源问题：
- 快捷栏格子 `data-sfx` 原来写死 108（药品点击音），空格也一样 → 空格显式 `'0'`，有物品按 `itemClickSound(stdMode)`；
- 全局点击处理里 `Number(dataset.sfx) || SFX.button` 会把 `'0'` 当 falsy 退回默认按钮音 → 改成 `id === 0` 直接 return。

### 35.2 其它音效逐项对照（本轮补齐）

| 场景 | 原版依据 | 修前 | 修后 |
|---|---|---|---|
| 怪物走/待机叫 | `Appr*10+200+1` | 无 | 每步 1/8 概率播，资源缺失自动静默 |
| 怪物攻击 | `+2` | 无 | 播 |
| 怪物挨打 | `+4` | 无 | 播 |
| 怪物死亡 | `+5` | 无 | 播（如稻草人 Appr27 → 475） |
| 人物攻击武器音 | `Actor.pas:2273` `case (m_btWeapon div 2)` | 只有短/剑/长三档 | 补齐木棒/刀/斧/锤/拳（50-57） |
| 人物受击「武器音」 | `:2356` 注意是**两次** `div 2` | 无 | `weaponStruckSound`（60-65），照抄原版双重除法的写法 |
| 人形受击「身体/护甲音」 | `:2296-2309` 按攻击者武器 + 自己 `m_btDress div 2 == 3`（重甲） | 无 | `struckBodySound` → 70-73 / 80-83；攻击者非玩家时保持 72 |
| 战士特攻人声 | `:2416-2459` 攻杀 `s_yedo_man/woman`(130/131)、刺杀 132、半月 133、烈火 137 | 只有特攻音，无武器音、无 yedo | 先武器音再人声 |
| 脚步音 | `:2169-2257` 按 `btArea/wMidImg/wFrImg` 分类 + `:2679` 左右交替、跑动 +2 | 只有地板 1-4 | `footstepBase()` 实现后两层（地面/草地/石/木/房间），左右交替 + 跑动 +2 |
| 升级音 | `ClMain.pas:6734` DrawEffectHum(8) → `s_meltstone`(101) | 无 | `stats` 里 `prevLevel` 上升时播 |
| 玩家施法音 | 10000 + magicId*10（magicId 1 → 10010） | 拍脑袋的近似映射（137/8301…） | 按 `10000 + magicId*10` 直出 |
| 自己挨打 | `s_manstruck/womanstruck`(138/139) | 有 | 保留，另按 HP 下降兜底 |
| 自己死亡 | `s_mandie/womandie`(144/145) | 有 | 保留 |
| 主场景 BGM | `PlayScn.pas:414` `s_main_theme = 102` | 进游戏后无 BGM | `enterGame` → `setBgm('play')` |
| 商店行 / NPC 选项点击 | `FState.pas:5177 / 5326` `s_glass_button_click`(105) | 无 | `data-sfx=105` |
| 物品落地 | **原版没有任何调用点** | 播了一个音 | 删除整个 `itemDrop` 分支 |

### 35.3 有意偏离

- **脚步音第一层没还原**：原版 `btArea*10000 + wBkImg - 1` 需要每格的 `btArea`，
  本项目的 1.80 map tile 第 5 字节被我们拿去当「对象图库号」了，取不到 `btArea`。
  只实现 `wMidImg` / `wFrImg` 两层覆盖 + `bng` 两个特判，覆盖绝大多数常见地砖（草地/石路/木桥/室内）。
- **魔法音只按 `10000+magicId*10`**：原版还有 `m_nMagicFireSound` / `m_nMagicExplosionSound`
  两个按技能配置的字段（服务端下发），本项目 WS 的 `spellCast` 只带 `magicId`，暂不展开。

### 35.4 验证

事件流断言（注入 WS 消息，记录 `play()` 实际编号）：

| 场景 | 期望 | 实际 |
|---|---|---|
| 普通攻击命中 | 武器音 + 怪挨打 664 | `[52, 664]` |
| 攻杀剑术 | 武器音 + yedo 130 + 怪挨打 | `[52, 130, 664]` |
| 怪打自己 | 怪攻击 662 + 身体音 72 + 自己挨打 138 | `[662, 72, 138]` |
| 女玩家打重甲男 | 拳 57 + 重甲拳 83 + 男挨打 138 | `[57, 83, 138]` |
| MISS | 只有武器音，无受击声 | `[]` |
| 自己死亡 / 女玩家死亡 | 144 / 145 | `[144]` / `[145]` |
| 稻草人死亡 | Appr27 → 475 | `[475]` |
| 施法 magicId 1 / 11 | 10010 / 10110 | `[10010]` / `[10110]` |
| 升级 | 101 | `[101]` |
| 怪物 idle | 含 661 | `idle_seen` 含 661 |

解捆 E2E（真实双击）：StdMode=31 的传送包 → **无任何音效**；同一操作路径下药品 StdMode=0 → 108。

永久回归：`node scripts/test_sfx_map.mjs`（纯 Node 直接 import 音效模块，断言
`itemUseSound([0,1,2,3,31])`、`itemClickSound` 分支、各 SFX 编号）。

另跑：`test_pyserver` / `test_tick_systems` / `test_all_messages --live 8080` 全通过；`node --check` 无语法错。

### 35.5 遗留

- 怪物叫声只在**移动事件**里按 1/8 概率触发，静止的怪不会「待机叫」；原版 `RunActSound` 的待机帧也会发声。
- `footstepBase` 的石头/洞穴区间是从 `bng` 反推的，若发现某张图脚步不对，直接补区间即可。

---

## §36 遮挡半透明 / 脚步音 / 掉落闪光 / 治愈术 / 矿洞地图（2026-11-12 第 10 轮反馈）

用户反馈 5 条：树/城墙图层不对（人物一直在最上面，过城门尤其明显）、点鼠标就响脚步（人被挡住没动也响）、
掉落物没有闪光、道士治愈术太强（回血多、恢复不打断进攻节奏）、(664,214) 进矿洞卡「正在加载地图」。

### 36.1 树/城墙遮挡：最后一遍重画自己必须是**半透明**（关键发现）

根因在 §32.4：原版 `PlayScn.pas:1361-1383` 确实会把 `g_MySelf` / `g_FocusCret` / `g_MagicTarget`
在行循环后再画一遍，但我们画成了**实心**。原版这一遍 `DrawChr(..., blend=TRUE, FALSE)`，
`Actor.pas:1983 DrawEffSurface` 里 `blend=TRUE` → `DrawBlend(..., 0)`，
`cliUtil.pas:956` `blendmode 0 = Color256Mix`（**50% 调色板混合 = 半透明**；mode 1 才是加色）。

所以原版的真实效果是：**人被树/城墙/城门挡住时，人物以约 50% 透明度「透」在最上面**——
未遮挡时半透明重画叠在自己身上 = 颜色不变（0.5s + 0.5s = s），所以平时看不出差别。
旧实现实心重画 → 「人物一直站在树/城墙上」。

修法：`MapActor.render` 的 selfSnap / focusSnap 重画都包 `ctx.globalAlpha = 0.5`。
顺带补上 focusSnap（原版 `g_FocusCret` 同样重画，旧实现只高亮不重画）。

### 36.2 脚步音：坐标没变不播

服务端 `_move` 被挡时也 `broadcast_move`（坐标不变，客户端当纯转向用），
前端 `case 'move'` 只要 id 是自己就播脚步 → 点鼠标被挡也响。
`SoundManager` 加 `_lastMovePos` 跟踪：坐标与上次相同（纯转向/被挡）直接 break；
换图（changeMap）清空，避免吞掉换图后第一步。真移动时左右脚交替仍由 stepFlip 负责。

### 36.3 掉落物闪光：FLASHBASE=410（1.80 保留）

原版 `PlayScn.pas:1417-1438`：每 `g_dwDropItemFlashTime`(MShare.pas:412 = 5s) 把 `BoFlash` 置 TRUE，
之后每 20ms 走一帧 `g_WMainImages[FLASHBASE(410)+step]`，step 0..9 共 10 帧，
`DrawBlend(..., 1)`（Color256Anti 加色）画在所有 actor 之后（最上层）。
实测 1.80 `Prguse.wil` 410-418 正是这组星光（8×7 → 32×30 → 8×7，offset 自带居中），419 为 1×1 收尾。

实现：`MapActor.render` 末尾遍历 groundItems，`_flash` 状态机（5s 触发 / 20ms 一帧 / 10 帧），
`_getTex('prguse', 410+step)` + `globalCompositeOperation='lighter'`，位置 `sx(g.x)+tex.px, sy(g.y)+tex.py`。

### 36.4 治愈术：数值与节奏

| 项 | 原版 | 旧实现 |
|---|---|---|
| 威力 | `GetAttackPower(GetPower(MPow) + LoWord(SC)*2, (HiWord(SC)-LoWord(SC))*2+1)`（Magic.pas:301） | `magic_power`（掺 MC 掷点）+ **scMax**\*2 |
| GetPower | `ROUND(n/(btTrainLv+1)*(btLevel+1)) + defPower + Random(defMaxPower-defPower)`，btTrainLv 恒 3（LocalDB.pas:382） | /4*(lv+1) 近似但用 Java round |
| 回血池 | `m_nIncHealing` 上限 300（ObjBase.pas:4527 RM_MAGHEALING），每 tick 只掉 `m_nPerHealing=5` | 一致 |
| tick 间隔 | `600 - min(400, Level*10)` ms（ObjBase.pas:3785） | 固定 500ms |

- 新增 `heal_power()` / `delphi_round()`（Delphi Round 是**五取偶**：Round(10.5)=10，Java Math.round=11）。
  群体治愈术（magic_id 29，MagBigHealing）同公式一并修正。
- 「二段恢复」其实是原版行为：回血池每次滴 5 点，不是一次跳满。
- 节奏：`_castMagic` 加 `if (!this._isIdle()) return;` + 本地立刻 `_action='spell'`
  （对应原版 ClMain.pas:2421 `CanNextAction` 闸 + `g_dwMagicDelayTime = 200 + Delay`），
  施法动画 360ms 内攻击/移动被锁住——恢复会打断进攻节奏了。

### 36.5 矿洞地图 D401：resources.json 大小写

诊断文件（E:\download\jootm2-diag-*.json）直接给出答案：`error: 地图 D401 未在 resources.json 中`。
服务端 `gamedata._base()` 把文件名**统一小写**登记（`D401.map` → `"d401"`），而 MapInfo 里 id 是 `D401`，
前端 `mapUrl()` 精确匹配落空 → `loadMap` 抛错 → 永远卡「正在加载地图…」。

修法：`resource.mjs` 建 `_mapsLower` 小写索引，`mapUrl()` 先精确再小写兜底；
`PlayScene` 的 onEnter / changeMap 加载失败时把 `_mapLoadError` 画在屏中央（不再无限「正在加载」）。

### 36.6 验证

- E2E（Playwright，登录→进游戏）：
  - `mapUrl('D401')` → `/resources/Map/D401.map`，`loadMap('D401')` → 200×200 ✓
  - 脚步音事件流：首移 `[1]`、同格转向/被挡 `[]`、真移动 `[2]`（左右脚交替）✓
  - 闪光：状态机 step 0→10→off；drawImage 钩子 600ms 计数 36 次；物品区域近白像素 795→1014、峰值 239→255 ✓
  - 截图：TestHero 在大树冠后以半透明「透」出来（正是原版效果）✓
  - 无 pageerror / console.error
- 回归：`test_pyserver` 73/0、`test_tick_systems` 93/0、`test_all_messages --live` 29/0、`test_sfx_map` 84/0。
- 治愈术单测：delphi_round(3.5)=4/2.5=2/10.5=10；治愈术 lv0-3 = 11..30（原版公式 10..31 区间内）。

---

## §37 建角发型 / 升级不回满 / 药水缓回 / 刷怪密度 / 地图名 / GM（2026-11-12 第 11 轮反馈）

### 37.1 女角色光头（#15）

原版建角发送的是 `shair := IntToStr(1 + Random(5))`（`IntroScn.pas:1427`）——**随机 1..5，
从来不是 0**；而客户端 `Actor.pas:3186` 只在 `hair*2 > 1`（即 hair >= 1）时才画头发
（`m_nHairOffset := HUMANFRAME * (hair*2 + sex)`，hair=0 → offset = -1 不画）。
我们前端建角默认 `hair: 0` 且允许 0..9 循环，默认值正好落在「光头」上。

- `SelectCharScene.mjs`：默认/循环范围改为 1..5（对齐原版），文字显示改为直接显示 hair 值。
- `service.py::_enter_game`：老存档 `hair <= 0` 的角色进游戏时迁移成 `1 + Random(5)`。

### 37.2 升级不回满（#16）

原版 `HasLevelUp → RecalcLevelAbilitys`（`ObjBase.pas:1938-1939`）只做
`if HP > MaxHP then HP := MaxHP` 的**向下钳制**，没有任何 `:= MaxHP` 赋值——升级不会回满。
旧实现 `p.hp, p.mp = p.max_hp, p.max_mp` 是错的，改为 `min(p.hp, p.max_hp)` 钳制。

### 37.3 药水「慢慢恢复」（#17）

原版 `ObjBase.pas:23335-23368`：StdMode 0 的药水**不**即时加血——
`Inc(m_nIncHealth, StdItem.AC)` / `Inc(m_nIncSpell, StdItem.MAC)` 写进回血池；
例外是 Shape 1（疗伤药）走 `IncHealthSpell(AC, MAC)` 立即加（`ObjBase.pas:3615`）。
池由回血 tick 消耗（`ObjBase.pas:3782-3850`）：

- tick 间隔 `600 - min(400, Level*10)` ms；
- 每 tick 每池消耗 `Level div 10 + 5`（HP/MP 同），治愈术池恒 5；
- HP 满 → 清空 inc_health/inc_healing；MP 满 → 清空 inc_spell。

`_regen` 重写为三池合一 tick；`_consume` 改写池语义（StdMode 1=肉 2=毒药：消耗但无回血）。
回血池为会话内状态，未持久化（原版有 btIncHealth 存档字段，量小影响可忽略，见 §37.6）。

### 37.4 刷怪密度（#18）

旧实现 `spawn_monsters` 把每个 MonGen 条目的数量钳到 `min(count, 3)`——MonGen.txt 里
天然洞穴一层（D011）单条就是 30/45 只，钳完只剩 3 只，密度差 10 倍以上。已去掉钳制，
按 `entry.count` 全量刷。重生对齐原版 ZenTime：列 7（分钟）×60s（`LocalDB.pas:1237
dwZenTime := Str_ToInt(sData,-1)*60*1000`），每条 entry 数量补满才停（`UsrEngn.pas:1158-1172`：
活怪数 < nCount 且距上次刷新超过 ZenTime → 一次补齐差额）。
验证：D011 刷 122 只（= MonGen 有效条目 count 总和；「山洞蝙蝠0」无模板跳过）。
全服怪物数从启动 ~30 涨到 3500+。

### 37.5 换图后地图名不变（#19）

`PlayScene.mjs case 'changeMap'` 之前**没有更新 `this.snapshot`**，`_renderHud` 读的是
onEnter 时的旧快照 map.title → 永远显示第一张图的名字。加 `this.snapshot = msg` 即修。
验证：@移动 0 → D011，标题从「比奇省」变为「天然洞穴一层」。

### 37.6 GM 账号与 @命令（#20）

- `config.GM_ACCOUNTS = frozenset({"player"})`（登录名白名单，语义对齐原版
  `m_btPermission >= 6`）；登录时记 `session.account_name`，进游戏设 `p.is_gm`，
  stats 里下发 `gm` 标记。
- 命令（`service._say` 里 `@` 前缀拦截，不广播；对齐原版 `ProcessUserLineMsg`）：
  `@移动 [地图] x y`、`@金 n`、`@等级 n`、`@制造 物品名 [数量]`、`@刷怪 怪名 [数量]`、
  `@满`、`@帮助`。
- 前端：大地图 Ctrl+点击 → 逆变换算格子坐标 → 发 `@移动`（服务端再校验 is_gm）；
  面板提示按 `self.gm` 显示「GM: Ctrl+点击传送」。
- 已知边界：gold 不在常规 stats 里（原版是 GoldChanged → SM_GOLDCHANGED 单独下发），
  `@金` 用 `send_stats(extras={"gold"})` 补发。

### 37.7 验证

- 离线定点 15/15（`data/_unit_batch11.py`，已删）：hair 迁移、GM 判定、升级不回满、
  吃药不即时回血 + 池写入 + tick 消耗、@金/@等级/@移动/@制造、D011 数量与 ZenTime。
- E2E 8/8（Playwright）：GM 标记、@金、@移动、大地图几何与 Ctrl+传送、刷怪总量、
  换图标题更新、无 pageerror。
- 回归：`test_pyserver` 73/0、`test_tick_systems` 93/0、`test_all_messages --live` 29/0
  （「消耗品恢复 HP」断言改为「消耗品写入回血池」）、`test_sfx_map` 84/0。

---

## §38 第 12 轮反馈：MISS 音效 / 矿洞视野 / 怪物名 / 洞穴换图（2026-11-12）

### 38.1 #21 MISS 不是「完全没声音」，是缺挥空武器音

旧实现 `SoundManager.mjs` 的 attack 分支写成
`if (!msg.noAttackAnim && !msg.miss && atk != null)`，MISS 直接跳过攻击音再 `break`，
所以一声都没有。原版把「攻击动作」和「命中」拆成两条消息：

- 服务端先**无条件** `SendRefMsg(RM_HIT, …)`（`ObjGuard.pas:49`、`ObjMon.pas:684/814/886`），
  再调 `_Attack(wHitMode, Target)`；命中失败只是不发 `RM_STRUCK`。
- 客户端 `Actor.pas:2270-2283` 的 `SM_HIT` 分支无条件按 `m_btWeapon div 2`
  设 `m_nWeaponSound`（挥武器音），`RunActSound` 在动作帧播出。

所以 **MISS 有挥空声、没有受击弯腰音/惨叫**。改成
`if (!msg.noAttackAnim && atk != null)`，`if (msg.miss) break;` 保留（跳过受击方）。
`noAttackAnim` 是我们自造的「纯受击」广播，仍要跳过。

验证：注入 `attack(miss)` → `[53]`（武器音）；同路径命中 → `[53, 474]`（怪挨打音）。

### 38.2 #22 矿洞视野（DrawFog / ApplyLightMap）

原版链路：

| 环节 | 依据 |
|---|---|
| 服务端 `SM_NEWMAP` / `SM_CHANGEMAP` 第 4 参数 `cdir = DayBright()` | `ObjBase.pas:5618 / 5796` |
| 客户端 `DarkLevel := cdir; g_boViewFog := DarkLevel <> 0` | `PlayScn.pas:2388-2390`、`ClMain.pas:4812-4815` |
| `DayBright()`：DARK→1 / DAYLIGHT→0 / bright=1→0 / bright=3→1 / 其余→2 | `ObjBase.pas:4283-4293` |
| `g_nGameTime` 按真实小时分四档 | `FrnEngn.pas:85-91` |
| 三级暗度 HeavyDark/LightDark/Dengun | `cliUtil.pas:594-599 DrawFog` |
| 人物/光源周围的光圈 fogmask | `PlayScn.pas:745-775 ApplyLightMap`、`DrawLightEffect` |
| fog 画在 ObjSurface 上、血条之前 | `PlayScn.pas:1471-1474` |

本项目此前只有昼夜图标（`_setDayBright`），完全没有 fog → 全亮。本轮：

- `gamedata.parse_map_info` 解析 `[map title] FLAG...` 尾部标志（`DARK/DAY/MINE/...`）；
- `GameWorld.is_underground()` / `dark_level()` 按 DayBright 语义算 0/1/2；
- `map_snapshot` 下发 `darkLevel` + `underground`，`dayChanging` 广播同时带暗度；
- `day_bright_for_hour()` 从「均分四段」改成原版 `FrnEngn.pas` 的分档表
  （5-10/16-22→1、11/23→2、4/15→0、0-3/12-14→3）；
- `MapActor._drawFog()`：整屏暗色 + `destination-out` 径向渐变挖出人物周围圆形亮区，
  `darkLevel` 1/2/3 → alpha 0.86/0.56/0.26、半径 132/190/250；画在精灵之后、血条之前；
- 前端 `_applyBrightness()` 统一处理 enterGame / changeMap / dayChanging。

**有意偏离**：原版 `D401 比奇矿区` 只有 `MINE` 标志、没有 `DARK`，严格 `DayBright()`
白天是全亮的；`D011 天然洞穴` 更是没有任何标志。按用户「矿洞应有视野」的实测记忆，
把 `MINE`/`DARK` 标志以及标题含「洞/矿/穴/墓/窟」的地图并入 `is_underground()` 恒暗；
`DAY` 恒亮，其余地表随昼夜。这是配置语义近似，改判定即可回到严格 DayBright。

验证：截图对比暗化开 / 关，画面平均亮度 **18 → 63**；HUD（DOM 层）与血条不受影响。

### 38.3 #23 「僵尸3 / 僵尸20」= Monster.DB 变体编号泄漏

权威依据：`M2Share.pas:3630 FilterShowName` —— 从名字里第一个数字或 `-` 处截断；
`ObjBase.pas:2654 GetShowName` 调它，`ClientQueryUserName`（`:2638`）下发 `SM_USERNAME`。
Monster.DB 里同名怪有多个变体（外观/命中/行走参数不同），转成本项目的
`Monster.csv` 时为了 key 唯一加了数字后缀（273 个名字里 144 个带尾数字；
「僵尸」10 个变体、「跳跳蜂」9 个……），`MonGen.txt` 也用带后缀的名字索引模板。

修：`world.filter_show_name()` + `Monster.brief_json()` 下发包掉后缀；
玩家名字**不剥**（`Player.brief_json` 原样）。现在 D401 里显示「僵尸」「洞蛆」。

### 38.4 #24 洞穴换图：服务端没错，错在前端时序与在途消息

先排除服务端：离线模拟 `D401 (76,14) --dir4--> (76,15)` 踩门，
`changeMap` 正确带 `{name:D411, title:"北部矿区", miniMapIndex:14, darkLevel:1}`；
E2E GM 传送 D411 时 HUD 也正确变成「北部矿区 59:8」。真正的问题有三个：

1. **点位比例取错对象**：小地图/大地图用 `snapshot.map.width/height` 去除 `tex.width/height`
   得到「像素/格」。换图瞬间快照已经是新图、底图还是上一张（或反过来），点位整体偏移。
   原版公式是 `mx = x*48 div 32`（1.5 像素/格 x）、`my = y*32 div 32`（1 像素/格 y），
   每张 mmap 都按该地图格数生成（实测 120 张洞穴 mmap 全部符合：D401 200×200 → 300×199、
   D411 100×100 → 152×99、D011 400×400 → 600×399）。改为把 `kx/ky` **绑定在纹理上**
   （mmap 加载时记录，兜底缩略图记各向同性 scale），大地图 Ctrl+点击的逆变换同步改。
2. **底图换得太晚**：`_loadMiniMapTexture` 原先放在 `mapActor.enter().then()` 里，
   地图瓦片预取期间底图还是旧的。改为 `changeMap` 一到就立即换，并在纹理/地图就绪后
   重绘已打开的大地图。
3. **在途 move 把人弹回**：`changeMap` 到达客户端前发出的 `move` 仍在途，会落在
   **新地图**继续走；而 `D401 (76,15) → D411 (59,8)` 与回程门 `D411 (60,7) → D401 (77,14)`
   紧挨着，多走一步就弹回比奇矿区 —— 正是用户报告的「名字还是比奇矿区，坐标 (77,14)」。
   全库 2434 条门里有 **113 条**落点紧邻回程门（祖玛阁自身循环门最多）。
   加 `Player.map_change_at` + `service._move` 350ms 短锁，等效原版
   `SM_CHANGEMAP` 后的 `CancelAction` 停顿。

**其它洞穴核查**：`MiniMap.txt` 登记的 120 张 D/E 类地图，mmap 尺寸与
`格数×1.5 / 格数×1` 全部吻合；只有 `D718/D719` 共用 index 57 且没有 `.map`
文件（原版配置如此，进不去）。210 个门目标地图没登记 MiniMap.txt（多为室内小屋），
走兜底缩略图，属原版行为。

### 38.5 验证

| 项目 | 结果 |
|---|---|
| Playwright E2E（本轮 `data/_e2e_batch12.py`，已清理） | **13/13**，无 pageerror |
| 新增永久回归 `scripts/test_map_view.py` | **34/0** |
| `test_pyserver` / `test_tick_systems` | 73/0、93/0 |
| `test_all_messages --live 8080` | 29/0 |
| `test_sfx_map` | 84/0 |
| 走门实测 | `D401 76,15 → D411 59,8`，HUD「北部矿区 59:8」，kx/ky = 1.52/0.99 |
| 视野像素 | 暗 18 / 亮 63（关闭 fog 后） |

### 38.6 遗留

- fog 只做了人物中心一个光圈，没还原 `m_LightMap` 的多光源（火把、蜡烛、魔法光），
  也没有「免蜡」（`g_boForceNotViewFog`）。
- `dayChanging` 只在服务端 tick 每分钟检查一次，切小时不是瞬时。
- 地下判定用 MapInfo 标志 + 中文标题关键字，属于配置语义近似；
  若某张图误判，改 `GameWorld.is_underground()` 的判定集合即可。

