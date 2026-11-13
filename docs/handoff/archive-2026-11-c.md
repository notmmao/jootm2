# jootm2 交接归档 c

> 从 HANDOFF-PYSERVER.md 归档的历史轮次（2026-11-13 重构，逐字保留、只读不改）。
> 内容：§31~§34：现场导出诊断工具与材质根因修复 / 尸体生命周期 / 商店·EXP·遮挡·掉线 / 稻草人尸体帧

---

## §31 「还是太卡 / 黑条还在」：现场导出工具 + 材质加载根因修复（2026-11-12）

用户反馈两条：①朋友仍觉得卡；②地图黑条（暗色带）反复出现，多次修复未解决。
第②条要求「加个快捷键，按下就保存当时内部状态 + 截图」，并尝试局部刷新观察效果；
另外要研究同类浏览器传奇（cn.stateofmir.com）的前端缓存/渲染/刷新做法。

**结论先说：黑条的根因是「材质下载量」，不是绘制。** 一屏地面只有 ~230 张图、
真实字节约 1MB，但旧实现按 512KB 固定分片取，为一张 6KB 的地砖要下 512KB；
200KB/s 公网下进图 20 秒仍解不出 1 张地面图，于是整屏是 `FLOOR_FALLBACK` 的平坦暗色
（用户眼里的「黑条/黑块」）。修完同一场景 200KB/s 冷启动 13.4s 放行且地面 100% 真实，
60KB/s 也能到 88%（其余用真实地砖样本铺，不再是纯色块）。

### 31.1 新增现场导出与自救（`frontend/core/diag.mjs` + `perf.mjs`）

| 快捷键 | 作用 |
|---|---|
| **Ctrl+F9** | 导出 `jootm2-diag-<时间>.json`：帧耗时分布（p50/p95/max，按 update/render/floor/obj/ent/build/map/fx/hud/blit 分段）+ 地面层逐格状态 + **黑块游程** + 纹理失败/在飞清单 + 缓存与带宽统计 + 两条 PNG（当前画面、地面层离屏画布） |
| **Ctrl+F10** | 局部刷新：丢弃地面层离屏缓存、清掉失败纹理占位后重拉，只重建当前视口 |
| **Ctrl+F11** | 深度刷新：在局部刷新基础上清掉全部未完成占位（含卡住不返回的） |
| **Ctrl+F8** | 性能 HUD（fps / 各阶段 p50 / 未画格数 / 纹理 pending·failed） |

为什么必须「逐格 + 游程」：只截图分不清是「没画」还是「画黑了」。导出时直接读地面层
画布像素，按 48×32 逐格求平均亮度，把连续黑格合并成 `{gy, from, to, len}`，
并附该格的 `bng/mid/obj` 索引、所属 2×2 大图索引、三张纹理的就绪状态。
`page.images.floor` 是整张离屏画布，能看出暗区到底在地面层还是叠加层。

`perf` 是常驻的环形缓冲（300 帧），热路径只有几次 `performance.now()`，无对象分配。

### 31.2 材质加载：512KB 分片 → 精确区间 + 相邻合并

旧 `wil.mjs` 的 `_chunkEnd()` 对齐到「≥ start+512KB 的第一个图头」，一张 6KB 地砖要
下 512KB。改为：

- 每张图的字节范围 = `[offset[i], _nextOff(offset[i]))`（`_nextOff` = 二分找下一张**非空**图）；
- `WIL.prefetch(indices)` 按偏移排序后把 **gap ≤ 8KB** 的图并成一段，段内逐张解包；
- 段末尾多要 `RANGE_SLACK = 512` 字节（个别 WIL 的图数据比「下一张偏移」长出几字节，
  严格截断会解不出来；旧 512KB 分片掩盖了这个问题）。

顺带修掉一个潜伏 bug：`_unpackOne` 判位深原来用 `offsetList[index+1] || fileSize`，
下一张是空图（offset=0）就会退化成「文件末尾」，把 8 位图误判成 16 位 RGB565。改用 `_nextOff`。

实测（进游戏首屏，200KB/s 冷缓存）：

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 进图放行 | 21.0s | 13.4s |
| 放行时地面真实格 | 0 / 1178 | 1178 / 1178 |
| 放行时地面字节 | ~20MB（超量 12x） | 264KB（Tiles 214KB + SmTiles 49KB） |

### 31.3 加载编排：地面独占带宽 + 优先级 + 掐掉在飞请求

1. 进图时 `cancelWarm()` + `cancelPreload()`：后台预热不再领新任务。
2. `scheduler.abortPending()`：连**已在飞**的请求一起 abort。原因是登录阶段那 10 张
   800×600 开门帧（≈4.8MB）占满了全部 6 条连接，光「不再领新任务」没用。
3. `scheduler.schedule(url, start, end, priority)`：地面纹理插队到队首。
4. `MAX_CONCURRENT` 20 → **8**：大于 6 只是把请求塞进浏览器自己的队列，调度器失去插队能力。
5. 地面 bucket 先发并等齐（15s 上限），对象 bucket 在地面放行后才发（6s 尽力）。
6. `WIL.getTexture` 失败不再抛异常而是返回 `null`（一堆 `.then()` 调用点没有 catch，
   会变成 "Uncaught (in promise)" 且让该格永远停在加载中）。
7. `prefetch()` 遇到「WIL 里已解好的图」也要回调 `onTex`——否则调用方刚建的空占位
   永远填不上，表现就是「走一段路后整片地面停在借来的像素上」。

### 31.4 黑条的两个绘制侧隐患（顺带修掉）

1. **兜底色被当成真实地砖**：旧代码对「bng/mid 索引都是 -1」的格直接
   `fillRect(FLOOR_FALLBACK)` 并标 `cells=1`（永不重画）；邻格借像素时又允许从
   `cells != 0` 的格借——于是**一格黑方块会被邻格复制成整条黑带**，还会沿行走路径蔓延。
   现在 `cells` 分四态（0 未画 / 1 真实 / 2 借像素 / 3 确定无内容），**只允许从 1 借**，
   3 态不参与每帧扫描（`f.open` 只统计 0/2，快速返回才有效）。
2. **大画布自拷贝**：`f.ctx.drawImage(f.canvas, ...)` 会先快照整张 1760×957 画布，
   单次 ≈4ms。滚动时几十格各来一次 = 100~200ms 顿挫；`Ctrl+F10` 刷新 530 格 = **2190ms**。
   现在借像素只从 48×32 样本池贴（≈2µs），并新增 `_seedSamples()`：从窗口内已就绪的
   地砖合成最多 24 个样本，保证「整屏未就绪」时铺的也是真实地砖花色。

### 31.5 地面窗口几何收窄（顺带）

原来窗口 `±10` 格冗余 → 38×31=1178 格、画布 1760×957，而可视区只有 17×14。
多出来的 15 列每格都会被绘制、也都会单独发一个纹理请求（实测进图时 95 个请求排队）。
现在 `floorWindowRect()` 与预取共用同一几何：26×23=598 格、画布 1248×736，
`_prefetchVisible` 的地面集合、`_prefetchAround` 的移动预取都严格覆盖窗口。

几何校验（含 ±47px/±31px 平滑卷动与 4 格对齐余量）：可视左界 `gx ≥ cx-8`、
右界 `gx ≤ cx+9`、上界 `gy ≥ cy-6`、下界 `gy ≤ cy+8`；窗口 `wx0 ∈ [cx-15, cx-12]`、
宽 26 → 覆盖 `[cx-15, cx+10]`；`wy0 ∈ [cy-13, cy-10]`、高 23 → 覆盖 `[cy-13, cy+9]`。

### 31.6 其他每帧开销

- `_syncFloor` 改成 9 参数 `drawImage` 只贴可见区块（软件渲染下整张 1248×736 贴进
  800×445 是全尺寸逐像素）。
- 名字宽度 `measureText` 结果缓存（每实体每帧一次同步测量）。
- 掉落物直接传 `Map`，不再每帧 `[...values()]`；`byRow`/`dropsByRow` 每帧 `clear()` 复用。
- 解包按**时间片**让出主线程（>6ms 就 `setTimeout(0)`），不再「每 8 张」：
  一张 800×600 对象图要 3~5ms，连解 8 张就是 30ms 掉帧，是走路「一顿一顿」的来源。

### 31.7 定量验证

| 场景 | 结果 |
|---|---|
| 本地 60fps，有 GPU | frame p50 **1.3ms** / p95 3.0ms，0 帧 > 20ms；floor 0ms、obj 0.3ms、ent 0.1ms、map 0.5ms、blit 0.8ms |
| **软件渲染**（`--disable-gpu --disable-accelerated-2d-canvas`） | frame p50 **1.4ms** / p95 4.2ms，120 帧 **0 帧 > 20ms**，dt p50 16.6ms（说明瓶颈不在 2D 光栅化） |
| 200KB/s + 冷缓存 | 进图 13.4s，放行时地面 1178/1178 真实，走 12 步后 0 borrow |
| 80KB/s + 冷缓存 | 进图 13.8s，地面 598/598 真实（对象 94 张仍在补），无黑块 |
| 60KB/s + 冷缓存 | 地面 1041/1178（88%），其余用真实地砖样本铺，无纯色块（改造前是 0/1178 全屏暗色） |
| `Ctrl+F9` 导出 | 3.2MB JSON，含 `perf.raw` 90 帧 + `map.dimCells` + 两条 PNG，`pageerror` 0 |
| `Ctrl+F10` 局部刷新 | 全屏重建 598 格 **13.7ms**（改造前 2190ms） |
| 回归 | `test_pyserver` 73/0、`test_tick_systems` 72/0、`test_all_messages --live 8081` 28/0 |

复跑探针：`D:/Python39/python.exe data/stripe_probe.py [--kbps=200]`（`data/` 未入库）。

### 31.8 同类实现调研

`docs/BROWSER_MIR_CLIENT_NOTES.md`：cn.stateofmir.com 用 **PixiJS v8 + WebGL2**，
素材**服务端预转换**（`maps/<id>.bin.gz` 用 `DecompressionStream` 解），
纹理页是 **8 位索引（R8/RED）+ 调色板 uniform**（显存 1/4、换色只换 uniform），
chunk 用内容哈希 + `immutable`，入口文件 `no-cache`+ETag，`version.json` 轮询提示刷新，
`gameAhead()/prepareGame()` 在登录阶段就按地图预取，并有「加载步骤看门狗 + `/play/err` 上报」。

我们采纳了「按需精确区间」「优先级/切场景前清管道」「现场导出」三条；
不迁 WebGL（2D 语义要精确对齐 Delphi 的 blend/ceBright，且软件渲染下每帧只有 1.4ms）。

### 31.9 遗留

- 公网要生效需重新 build 镜像 + 推 ACR（`§22` 流程）。
- 15s 地面等待上限仍在：慢到 60KB/s 以下时放行后靠样本池铺，不是真实地砖。
- `Ctrl+F9` 的 PNG 用 `toDataURL('image/png')` 全质量，JSON 约 3MB；如需更小可改 webp。

---

## §32 尸体生命周期 + 挖肉随机次数 + 绘制顺序 + 血条颜色（2026-11-12）

用户报 4 条，全部要求「对照 Delphi 原版确认后再改」：

1. 钉耙猫死后不消失（怪不动、血条为空），**移动后**才消失；并问原版死后是否有尸体。
2. 鹿的尸体会盖住人（**人面向上时**盖，朝其他方向不盖）。
3. 挖鹿尸体**总要挖两次**才出肉，且第二次必出肉，中间只有人跪在那。
4. 头顶血条与左下血球**有时不一致**（截图对比）。

结论：4 条全中，且**全部是「原版行为没对齐」，不是 bug**——除第 3 条的用户描述本身有误
（不是 2 次，是随机 3~10 次，均值 4.49；用户感知成「两次」是因为前几次只有跪下的反馈，
没有进度提示）。

### 32.1 尸体保留 3 分钟，且所有怪都留

原版依据 `ObjBase.pas:3769`：

```pascal
if (GetTickCount() - m_dwDeathTick > g_Config.dwMakeGhostTime {3 * 60 * 1000}) then
  MakeGhost();   // → DisappearA → RM_DISAPPEAR → 客户端 SM_DISAPPEAR
```

`g_Config.dwMakeGhostTime` 默认 **3 分钟**（不是旧实现写的 60s），且**不限动物**。

旧实现两个缺陷叠加：

| 缺陷 | 后果 |
|---|---|
| 非动物怪 `corpse_until = 0` | `_sight_actors` 立刻把它剔出下发集合，但**从不广播 `entityRemove`** → 客户端那具尸体永远留着 |
| 60s 而非 180s | 动物的尸体也短了 1/3 |

「不动、血条为空」的直接原因：客户端 `entities` 里那具尸体 `_corpse=false`、
`maxHp=32` 但 `hp=0` → 画出**空血条**；走动后 `update_sight` 重建视野才补发移除。

修法：

```python
# world_extra.py
CORPSE_MS = 180_000
m.corpse_until = now + CORPSE_MS       # kill_monster：所有怪，不只 animal

# world.py  tick() 里新增
self._expire_corpses(now)   # 到期 map.remove + actors.pop + broadcast entityRemove
```

`death` 消息改为统一 `corpse: True`，`animal` 只决定要不要提示「可以挖肉」：

```python
o = {"type": "death", "id": m.id, "corpse": True, "animal": animal}
```

客户端 `PlayScene` 的 `death` 分支对所有怪都置 `_corpse = true`、`alive = false`、
播倒地帧；只有 `msg.animal` 时才打那句挖肉提示。

### 32.2 重生 = 新建 actor（不能复用实例）

旧 `_respawn_dead` 复用同一个 `Monster` 实例重置 `alive/hp/x/y`。一旦尸体要留 3 分钟，
「15 秒重生」必然把尸体顶掉——两者互斥。原版每次重生都是**新对象、新 id**。

```python
m = Monster(id=self.new_id(), name=t.name, x=a.spawn_x, y=a.spawn_y, dir=4,
            hp=t.hp, max_hp=t.hp, mp=t.mp, max_mp=t.mp,
            level=t.level, race=t.race, race_img=t.raceImg, appr=t.appr,
            template=t, spawn_x=a.spawn_x, spawn_y=a.spawn_y,
            spawn_map=home, respawn_interval_ms=a.respawn_interval_ms)
m.exp = t.exp
self.add_actor(m, home)
self.broadcast_entity_add(m)
```

旧尸体继续躺在 `actors` 里，由 `_expire_corpses` 按自己的 `corpse_until` 收走。
`_expire_corpses` 里顺手把 `pending_respawn/respawn_at` 清零，避免「尸体过期」与
「重生计时」互相干扰。

### 32.3 挖肉：随机磨损 + 600ms 间隔 + 骷髅不删

原版 `ObjBase.pas:17478-17495`：

```pascal
n10 := Random(16) + 5;
Dec(m_nBodyLeathery, n10);
if m_nBodyLeathery <= 0 then begin
  m_boSkeleton := True;
  ApplyMeatQuality;
  SendRefMsg(RM_SKELETON);   // 变骷髅，但不删 actor
  TakeBagItems;
  m_nBodyLeathery := 50;
end;
```

- 初值 `m_nBodyLeathery := 50`（`:1286`）→ 出肉需 **3~10 次**（均值约 4）。
  用户说的「两次必出」是采样偏差：他自己也只在「有肉」时才注意次数。
- `:17465-17470` `if dwCheckTime < g_Config.dwTurnIntervalTime then Exit`，
  `M2Share.pas:2121` 默认 **600ms** → 连点无效。
- `:17497` 每次挖都刷新 `m_dwDeathTick` → **持续挖肉会延长尸体保留**。

实现：`Monster.body_leathery`（新字段，默认 50）+ `Player.last_butch_at`。
`_butch` 里间隔不足直接 `return`；扣 `random.randint(5, 20)`；每次刷新 `corpse_until`；
`> 0` 时广播 `{"type": "butch", "meat": False}` + 提示「正在挖肉……」；
`<= 0` 时 `skeleton = True` + 重置 50 + 广播 `meat: True`，**不发 `entityRemove`**。

前端 `butch` 分支新增骷髅切换（`RM_SKELETON`）：

```js
if (corpse && msg.meat && !corpse.skeleton) {
  corpse.skeleton = true;
  corpse._action = 'death';
  corpse._dieUntil = 0;
}
```

骷髅帧**不带方向**（`Actor.pas:2124` `GetDefaultFrame`：
`if m_boDeath then if m_boSkeleton then Result := pm.ActDeath.start`，无 `dir`），
所以 `ActorSprite` 里 `const cf = seg.start + (e.skeleton ? 0 : dir)`。

### 32.4 尸体盖人：行循环之后必须重画一次自己

`PlayScn.pas:1361-1383`：画完按行的所有 actor 之后，把 `g_MySelf`（以及
`g_FocusCret` / `g_MagicTarget`）**再画一遍** → 自己永远在最上层。

旧实现只做「尸体 `unshift` 到最前 + 按 y 分桶」，同桶有效但**跨行失效**：
人朝上时包围盒向上延伸落进尸体所在行，下一行的桶整个在后面画 → 盖住。

`MapActor.draw` 改造：

```js
const hudQueue = ...;                 // 每帧复用
let selfSnap = null;
// 行循环内
this._drawEntity(ctx, e, anchorX, anchorY);
if (e.maxHp || e.name) hudQueue.push(e, anchorX, anchorY);
if (e.isSelf) selfSnap = [e, anchorX, anchorY];
// 行循环之后
if (selfSnap) this._drawEntity(ctx, selfSnap[0], selfSnap[1], selfSnap[2]);
for (let i = 0; i < hudQueue.length; i += 3) {
  this._drawHeadHud(ctx, hudQueue[i], hudQueue[i + 1], hudQueue[i + 2]);
}
```

必须抽出 `_drawEntity`，否则「重画自己」会丢掉隐身 alpha / `isFocus` 高亮 / 中毒染色。
名字与血条**不能跟着实体画**，要排队到「自己重画」之后——原版也是独立一遍
（`DrawScrn.pas:296-344`），否则后一行的树/怪会盖住前一行的血条。

### 32.5 血条统一红色，尸体不画

`DrawScrn.pas:306` `if (actor.m_boOpenHealth ...) and not actor.m_boDeath`；
只有 `HEALTHBAR_BLACK=0` / `HEALTHBAR_RED=1`（`Data/Prguse2.wil`：帧 0 = 80×51、
帧 1 = 84×54），**没有蓝色变体**。

旧代码给自己用 `#0ea5e9`（蓝）、给怪用红 → 用户看到的「不一致」就是这个。
现在统一 `#ef4444`，`dead || hp <= 0` 不画，百分比 `Math.round` 之后再换像素。

### 32.6 定量验证

| 项 | 结果 |
|---|---|
| 挖肉次数采样（300 次，离线） | min 3 / max 7 / **avg 4.49**，符合 `Random(16)+5` 对 50 的期望 |
| `test_tick_systems` | **80 PASS / 0 FAIL**（原 72，新增 `test_corpse_lifecycle` 8 条：3 分钟保留 / death 带 corpse / body_leathery 重置 / 重生新 id / 旧尸体仍在 / 重生广播 / 到期移除广播） |
| `test_pyserver` | 73 PASS / 0 FAIL |
| `test_all_messages --live 8081` | **29 PASS / 0 FAIL**（原 28 项里 2 FAIL：断言写死「挖 2 次」，改成循环 12 次 + 绕开 600ms 间隔） |
| E2E 鹿（真实 WS + Playwright） | 点 5 次出肉，`skel: true`；尾部聊天 `正在挖肉…… ×3 → 挖到了肉。` |
| E2E 多钩猫（非动物） | 死亡后 `corpse: true, action: death` 且**仍在实体表里**（旧实现会立刻消失） |
| 绘制顺序（单帧钩子） | `self` 出现 2 次、最后一次是所有实体中最后一个 = True；尸体进 HUD 队列 0 条 |
| 半血截图 | 头顶红条约 50%，与左球一致 |

`node --check` 通过 `MapActor.mjs` / `PlayScene.mjs` / `ActorSprite.mjs` / `diag.mjs`。

### 32.7 新增诊断字段

`window.__diag.report().hud`（修「血条与血球不一致」这类现场取证用）：

```js
{ selfHp, selfMaxHp, selfMp, selfMaxMp,
  entHp, entMaxHp, sameObject,   // sameObject=false 说明 HUD 读的不是同一个对象
  hudKey, texReady, job, level }
```

`entities.list` 每项新增 `corpse / skeleton / action` 三个字段。

### 32.8 遗留

- `CORPSE_MS` 写死 180_000；原版是 `!Setup.txt` 可配的 `dwMakeGhostTime`，
  以后接配置系统时改成读配置。
- 挖肉没有「进度」反馈（原版也没有），用户仍可能误判成「没生效」。若要改善体验需
  自造提示，属于偏离原版，等用户明确要求再做。
- 公网生效需重新 build 镜像 + 推 ACR（`§22` 流程）。

---

## §33 商店价格/物品说明 · EXP 条与等级 · 桥上遮挡 · 掉线处理（2026-11-12）

用户 4 条反馈（承接 §32），仍要求「先对照原版再改」。

### 33.1 商店：价格能看、说明看不到（结论 + 补齐）

原版读的是 `FState.pas:5077 DMenuDlgDirectPaint`（买列表）与 `:5360 DSellDlgDirectPaint`（卖窗口）：

| 位置 | 原版显示 | 改前 | 改后 |
|---|---|---|---|
| 买列表表头 | `物品列表` / `价格` / `持久` | 只有前两列 | 补 `持久`（`DMenuDlgDirectPaint` `TextOut(SX(249))`） |
| 买列表行 | 名字 / `价格 金币` / 持久（`Grade=-1` → `-`） | 名字 + 价格 | 补 `-`（商店货物没有耐久，`pcg.Grade` 恒为 -1） |
| 分类项 | `pg.SubMenu >= 1` 时在名字后画 `#31` 箭头，点它 `SendGetDetailItem` 进子目录 | 无（我们的 `[goods]` 没有子目录概念） | 未做——服务端 `goods()` 无 sub 字段，硬加是编造 |
| 卖窗口标题 | `价格: ` + `g_sSellPriceStr` | `卖: ` + 物品名，且永远没有价格 | `价格: N 金币`（问价后回填） |
| 卖价 | 拖入物品后 `CM_MERCHANTQUERYSELLPRICE` → `SM_SENDBUYPRICE`，`Recog>0` 显示 `N 金币`，否则 `???? 金币` | 没有问价消息，玩家卖之前不知道能卖多少 | 新增 `querySellPrice` → `sellPrice` |

卖价公式也对了一次：`ObjNpc.pas:2126 TMerchant.GetSellItemPrice = Round(nPrice / 2.0)`
（Delphi `Round` 是银行家舍入，Python `round` 行为一致）。价格算出来 ≤ 0 时原版**不成交**
（`ClientSellItem` 的 `if nPrice > 0`），我们原来 `max(1, …)` 硬卖 1 金币，改成提示
「这个物品卖不出价钱。」

**「买东西能看到说明么（攻击力/需要等级）」→ 原版看不到。** 依据：
- 买列表只有 `name / price / stock` 三个字段（`ClientGetSendGoodsList` 解 `name/sub/price/stock`）；
- 客户端**没有**商店货物的 StdItem 数据，所以也无法画 tooltip；
- 唯一的「详情」是 `CM_USERGETDETAILITEM`，它返回的是**子目录里的商品列表**（还是名/价/持久），
  不是物品说明；
- 想看攻击力/需要等级，原版的做法是**买下来放进背包**，再用鼠标悬停（`GetMouseItemInfo`）。

要在商店里直接显示说明属于**偏离原版的增强**，等用户明确要求再做。

### 33.2 EXP 条 / 等级 / 负重条

**根因一：经验语义错了。** 原版 `ObjBase.pas:1844 GetExp`：

```pascal
Inc(m_Abil.Exp, dwExp);
if m_Abil.Exp >= m_Abil.MaxExp then begin
  Dec(m_Abil.Exp, m_Abil.MaxExp);   // 只扣一次 → 一次加经验最多升一级
  Inc(m_Abil.Level);
  HasLevelUp(m_Abil.Level - 1);      // MaxExp := GetLevelExp(新等级)
end;
```

`m_Abil.Exp` 是**本级内**经验。旧实现当成累计总经验（`level_for_exp` 按累计和反查等级），
于是客户端拿「累计 1183」比「本级 400」——面板出现「当前经验 1183 / 升级经验 400」，
EXP 条恒满。

改造：
- `add_exp` 按原版：`p.exp += n`；`>= p.max_exp` 时 `exp -= max_exp`、`level += 1`、重算属性；
- `Player.max_exp` 做成 **property**（`exp_for_level(level)`），等级一变自动跟着变——
  旧实现是在 `to_player_json` 里算一次，之后永远是登录时的旧值；
- `send_stats` 补齐 `maxExp / weight / maxWeight`（原版 `SM_ABILITY` 一次带全），
  `bagUpdate` 也带 `weight / maxWeight`（拾取后负重条要动）；
- 旧存档迁移 `normalize_exp(level, exp)`：`exp < exp_for_level(level)` 说明已是新语义、
  原样返回，否则按累计逐级扣减。实测 `(4, 1183) → (5, 183)`，登录后 E2E 读到
  `level 5 / exp 273 / maxExp 600 / weight 23 / maxWeight 55`，EXP 条 35px（273/600×76≈34.6）、
  负重条 32px（23/55×76≈31.8）。

**根因二：等级/血球空白是「总闸」卡住。** 前端原来用一个全局 `_hudTexReady` 同时卡住
`_renderVitals` 和 `_renderLevelNumber`：只要 `_buildHud` 那批 `getTexture` 有一个没 settle
（进图时 `scheduler.abortPending()` 会把在飞请求 abort），血球和等级就**永久空白**——
正是用户说的「血球不准时 lv 也为空」。原版没有这种总闸，它对每个元素单独判：

```pascal
d := g_WMainImages.Images[7];
if d <> nil then ...      // FState.pas:3813
```

改成 `_ensureHudTex(lib, idx)`：有图就画，没图就补拉一次并登记，拉到后强制重绘
（失败 1s 后重试）。现在 HUD 是「渐进可用」而不是「全有或全无」。

**「EXP 下面的条是什么」= 负重条。** `FState.pas:3813-3856`：等级数字在 (660, 496)，
经验条 (666, 527)，负重条 (666, 560)，**两条用的是同一张图 `g_WMainImages.Images[7]`**，
只是右侧按 `Exp/MaxExp`、`Weight/MaxWeight` 裁切。

### 33.3 桥上行走时桥面盖住身体：obj 层是**两遍**画的

原版 `PlayScn.pas` 把 obj 层拆成两遍：

| 遍 | 位置 | 条件 | 效果 |
|---|---|---|---|
| 第一遍 | 行循环**之前**，紧接地面 | `if (DSurface.Width = 48) and (DSurface.Height = 32)` | 1 格见方的对象（**桥板**、地毯）永远在所有 actor 之下 |
| 第二遍 | 与 actor 按行交错 | `if (DSurface.Width <> 48) or (DSurface.Height <> 32)` | 树/墙/牌楼这类高对象按行遮挡 |

旧实现两遍并成一遍（全塞在行循环里），桥板被当成「大树」按行遮挡：从下往上走时人画在
桥所在行的前一行 → 桥压在身上；反过来走则不压。

实测该坐标的贴图尺寸（浏览器里直接问图库要纹理，`数据/0.map`）：

```
(350,510) obj=8168  48×32   ← 桥板
(350,509) obj=8197  48×96   ← 桥栏杆/立柱
(348,511) obj=8193  48×128
(351,509) obj=8166  48×32
...  共 9 块 48×32 桥板 + 3 个 48×96/128 立柱
```

修法：`MapActor.render` 在 `_syncFloor` 之后加一遍只画 `_isSmallObj(tex)`（尺寸正好 48×32）
的对象，行循环里跳过它们。验证：把镜头移到 (350,509)（桥板上方一格，正是会盖住的位置），
用 `drawImage` 钩子记录一帧出图顺序 → `bridgeIdx=0, selfIdx=13, selfDrawnAfterBridge=true`；
截图里人物完整站在桥上，栏杆仍按行正常遮挡。

### 33.4 掉线：原版就地停住，浏览器端改成「提示 + 回登录」

原版 `ClMain.pas:3326 CSocketDisconnect` 只做 `g_boServerConnected := FALSE`：

```pascal
if (g_ConnectionStep = cnsLogin) and not g_boSendLogin then begin
  FrmDlg.DMessageDlg('网络连接异常，程序将退出…', [mbOk]);
  Close;
end;
if g_SoftClosed then begin g_SoftClosed := FALSE; ActiveCmdTimer(tcReSelConnect); end;
```

也就是说**游戏中掉线原版什么都不做**，界面就地停住——但我们的音频模块在 `onClose`
里 `setBgm('intro')`，于是出现「听着登录音乐、画面还在原地不动」的诡异状态。

处理（按用户要求）：`main.mjs` 新增 `watchDisconnect(conn, sm)`，在 `onClose` 里
- 只有在**游戏内**（当前场景不是 `LoginScene`）才动作，否则会「连不上→onclose→回登录→再连」死循环；
- 弹一个居中的「连接已断开 / 即将返回登录界面…」提示（挂在 `#stage` 上，跟着整体缩放）；
- 1.6s 后走已有的 `onReturnLogin`：关旧连接 → 新建 WebSocket → 切回登录界面，顺带把重连做掉。

### 33.5 验证

| 项 | 结果 |
|---|---|
| `test_tick_systems` | **93 PASS / 0 FAIL**（新增 §[12] 经验语义 13 条：旧存档换算 / 加经验不越级 / 一次只升一级 / max_exp 跟随等级 / stats 带 maxExp·weight·maxWeight） |
| `test_pyserver` | 73 PASS / 0 FAIL |
| `test_all_messages --live 8080` | 29 PASS / 0 FAIL |
| E2E 登录进游戏 | `level 5 / exp 273 / maxExp 600 / 23-55`，`levelPixels=14`、`vitalsPixels=8740`（都非空），无 console error |
| E2E 桥上 | `selfDrawnAfterBridge=true`，截图人物完整站在桥板上 |

### 33.6 遗留

- 商店「分类子目录」（`SubMenu>0`）未做：服务端 `goods()` 不产出 sub 字段，要做得先扩 NPC 脚本语义。
- 商店里没有物品说明，属原版行为；要加是增强，等用户确认。
- 掉线提示是自造的（原版就地停住），文案/时长可再调。

---

## §34 稻草人死后留火：尸体帧取错了段（2026-11-12）

用户反馈：稻草人（比奇省）被打死后「爆炸，但会留下最后一帧」，截图里桥上一团黑色 + 火焰。

### 34.1 根因：尸体帧公式和原版差了一段

原版 `Actor.pas:2115 TActor.GetDefaultFrame`（`AxeMon.pas` 里各类重写版一致）：

```pascal
if m_boDeath then begin
   if m_boSkeleton then
      Result := pm.ActDeath.start                       // 骷髅：ActDeath 首帧，**不带方向**
   else Result := pm.ActDie.start
                + m_btDir * (pm.ActDie.frame + pm.ActDie.skip)
                + (pm.ActDie.frame - 1);                 // 普通尸体：ActDie **末帧**，带方向
end;
```

旧实现两种都写成 `ActDeath.start + dir`。多数怪恰好也像尸体（`ActDeath` 段本来就是尸体图），
但稻草人（`Monster.csv`: Race 83 / RaceImg 18 / Appr 27）不是：

| 项 | 值 | 说明 |
|---|---|---|
| body 图库 | `mon3`（`aGetMonImg`: `27 div 10 = 2` → WMon3） | |
| 外观 offset | `2520`（`GetOffset`: `npos * 360`，`27 mod 10 = 7`） | |
| 动作表 | `MA14`（`GetRaceByPM`: RaceImg 18） | |
| ActDie | `start 260 / frame 10 / skip 0` → 2780..2859（8 方向 × 10 帧） | 倒下 → 散成骨头 |
| ActDeath | `start 340` → **2860..2869** | 这 10 帧在美术上是**火焰**（`DEATHFIREEFFECTBASE = 2860`，`AxeMon.pas:14`） |

所以「尸体」= `2860 + dir` = 火焰的第 dir 帧 → 死后原地留一团火，直到 makeGhost。

实测帧（`mon3`，浏览器里 `getTexture()` 直接导出）：

```
2789 / 2809 / 2839   → 散落骨头（ActDie 末帧，8 方向各一张）  ← 正确的尸体
2860                 → 小火花       2865 → 大火球（用户截图里那团）
2439 / 2469          → 钉耙猫倒地（MA14 同理）
mon17 629 / 659      → 鹿侧躺
```

### 34.2 顺带补：稻草人死亡时的火焰叠加（原版本来就有）

`AxeMon.pas:810 THuSuABi.LoadSurface`（客户端 `PlayScn.pas:2173` 按 RaceImg 18 创建该类）：

```pascal
if m_boUseEffect then
   EffectSurface := FrmMain.WMon3Img.GetCachedImage(
       DEATHFIREEFFECTBASE + m_nCurrentFrame - m_nStartFrame, ax, ay);
```

`DrawChr` 里身体画完之后：

```pascal
if m_boUseEffect then
   if EffectSurface <> nil then
      DrawBlend (dsurface, dx + ax + m_nShiftX, dy + ay + m_nShiftY, EffectSurface, 1);
```

只有 `SM_NOWDEATH`（当场看到死亡）才置 `m_boUseEffect := TRUE`（`CalcActorFrame` 开头重置为 FALSE）。
也就是说「稻草人死后爆炸」是**死亡动画 10 帧逐帧叠火焰**，播完就该正常显示尸体——
旧实现没有这层叠加，直接把火焰帧当尸体，才变成「一直烧着」。

实现：`ActorSprite._frameSpec` 在 `die` 动作且 `raceImg === 18` 时追加
`{lib:'mon3', index: 2860 + rel, blend:'lighter'}`；`MapActor._drawEntity` 支持 `layer.blend === 'lighter'`。
叠加层**不做 `_lastByLib` 回退**（退到身体纹理会出怪影），纹理没到就这一帧不画。

**有意偏离**：原版 `LoadSurface` 有 60 秒节流（`if GetTickCount - m_dwLoadSurfaceTime > 60*1000`），
加上 `m_boUseEffect` 进尸体阶段不复位，实际原版会把某一帧火焰**冻结在尸体上直到 3 分钟后 makeGhost**。
我们按「意图」实现成逐帧播完即止，不还原这个节流副作用。

### 34.3 验证

| 项 | 结果 |
|---|---|
| 帧索引（浏览器内 `_frameSpec`） | 稻草人尸体 dir0/2/5 → `2789 / 2809 / 2839`；鹿 → `629 / 659`；钉耙猫 → `2439 / 2469` |
| 死亡动画 | 身体 `2780` + 火焰 `2860`（`blend=lighter`），第 9 帧 → `2789` + `2869` |
| E2E 注入（登录 → 进游戏 → 注入 appr 27 实体） | `layers corpse: ['56x50']`（骨头堆）；`layers die: ['100x90', '84x94 blend=lighter']`，截图中桥边空地可见爆火 |
| 真实场景 | 视野内稻草人正常站立；注入体走同一渲染路径 |

### 34.4 遗留

- `_dieUntil` 仍写死 1400ms（MA14 实际 10×120 = 1200ms），多停 200ms 但末帧≈尸体帧，看不出来。
- 骷髅分支对 Appr 27 会取到 `2860`（火焰）——原版同样如此；稻草人 Race 83 不可挖肉，正常玩不会触发。

---

