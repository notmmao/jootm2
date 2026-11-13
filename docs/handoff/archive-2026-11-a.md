# jootm2 交接归档 a

> 从 HANDOFF-PYSERVER.md 归档的历史轮次（2026-11-13 重构，逐字保留、只读不改）。
> 内容：§10~§20：音频链路 / 渲染防闪烁 / 选角与地面缓存 / 保真度补齐 / 第 1~8 轮实测反馈 / 1.76·1.80 资源差异分析

---

## 10. 音频链路（方案 B 全量，2026-11-09 追加）

### 10.1 设计前提

服务端和 WS 协议**零改动**：两个后端都不下发音效 id，但都已支持 `.wav/.mp3/.ogg` 的 MIME + Range 206 + CORS；`Connection._dispatch` 支持 `'*'` 通配 handler，所以音效完全可以由前端按 WS 消息本地映射，Java 版也能直接用。

`resources/Wav/sound.lst` 就是原版 Delphi `SoundUtil.pas::LoadSoundList` 读的编号表（GBK，1001 行：714 条有效 / 15 条指向不存在的文件 / 252 条空值 / 20 行注释），`s_*` 常量可直接套用。723 个 WAV 全是标准 PCM，ffmpeg 4.3.1 自带 libmp3lame；全量转码后 **189.1 MB → 13.57 MB（13.9×）**，8 并发耗时 19.8s。

### 10.2 文件与命令

| 文件 | 作用 |
|---|---|
| `scripts/convert_audio.py` | 离线构建：解析 GBK 编号表、去重转码、生成索引；幂等按 mtime |
| `frontend/audio/audio_map.json` | **提交进仓库**的 70.6 KB 索引（不含二进制） |
| `frontend/audio/SoundManager.mjs` | 前端播放内核 + 事件映射 + BGM + 开关/音量 |
| `scripts/test_audio.mjs` | Node 22 零依赖前端回归（18/18） |
| `resources/Audio/*.mp3` | 723 个衍生资源，`.gitignore` 已忽略 `resources/`，**不提交** |

```bash
python scripts/convert_audio.py                       # 全量转码（幂等，已转且源未变则跳过）
python scripts/convert_audio.py --force               # 忽略时间戳全部重转
python scripts/convert_audio.py --check --probe 20    # 校验产出 + ffprobe 抽样解码
python scripts/convert_audio.py --explain 103         # 查编号 103 的 mp3 / wav URL 与文件大小
python scripts/convert_audio.py --self-test           # 12/12，临时目录自检（含真实 ffmpeg 转码）
node scripts/test_audio.mjs                          # 18/18，mock DOM/Audio 的前端链路
```

编码参数：短音效 `-ac 1 -ar 22050 -b:a 64k -codec:a libmp3lame`；>= 1 MB 的 BGM/长音频 `-ar 44100 -b:a 96k` 保留原声道。`ENCODER_VERSION` 变更会自动让旧产物全部失效重转。

### 10.3 索引与命名

`audio_map.json` = `{version, source, base: "/resources/", audioDir: "Audio/", sounds{id:{mp3,wav}}, bgm{key:{mp3,wav}}, files{wav文件:mp3文件}, missing[15], stats{}}`。

每个**唯一 wav 只转一份 mp3**：被编号表引用的用「引用它的最小编号」命名（`103.wav → Audio/103.mp3`），未引用的用文件名 slug（`Field2.wav → Audio/field2.mp3`）；多个编号指向同一 wav 时在 `sounds` 里指向同一份文件。BGM 不在 `sound.lst` 里，按 Delphi `bmg_*` 常量 + 磁盘真实文件名单独编 7 个语义键（intro / select / field / gameover / theme / heroLogin / heroLogout）。

### 10.4 前端链路

- `main.mjs` 里 `sound.install(conn)`：注册 `'*'` 监听、全局 capture 点击、`pointerdown`/`keydown` 首次手势解锁、异步加载索引；`window.__sound` 是调试入口。
- `play(id, {gap, key, volume})`：编号 → `/resources/Audio/<id>.mp3`；`<audio>` 元素池 + `cloneNode()` 并发，同类 80ms 节流、脚步统一 key 250ms 节流、并发语音 ≤ 24；mp3 404/解码失败自动回退 `Wav/<原文件>`，索引 404 时退化到 `resource.mjs::wavUrl(id)`。
- BGM 独立 `<audio>`：登录界面 intro、登录后/选角 select、**进游戏 play（原版 `s_main_theme` = 编号 102，43.9 s，每 46 s 重播、曲间静音 2.1 s，非无缝 loop）**、连接关闭 → intro；其余键（`field`/`gameover`/`theme`/`heroLogin`/`heroLogout`）用 `__sound.setBgm(key)` 手动切。原版依据与本次修正见 §15。
- 开关与音量存 `localStorage`（`mir2.sound.enabled/volume/bgmVolume`，默认开 / 0.8 / 0.45）；右上角「声音」按钮 `_toggleSound()` 已接 `sound.toggle()`。
- 全局点击命中 `data-sfx` 属性或 `cursor:pointer` 热区就播按钮音；背包格 / 装备格按 `stdMode` 带 `data-sfx`，快捷栏格带 108，喝药播 107。

### 10.5 事件 → 音效编号

| 事件 | 编号 | 原版常量 |
|---|---|---|
| 按钮点击 / 背包物品点击 | 103 / 按 stdMode：108,111,112,113,114,115,116,118 | `s_norm_button_click` / `ItemClickSound` |
| 快捷栏点击 / 使用药品 | 108 / 107 | `s_click_drug` / `s_eat_drug` |
| 自己移动（走 / 跑） | 1,2 / 3,4 交替 | `s_walk_ground_l/r`、`s_run_ground_l/r` |
| 自己攻击 | 52；`swordHit` 7→56、12→132、25→133、26→137 | 攻杀/刺杀/半月/烈火 |
| 自己受击 / 死亡 | 138 / 144 | `s_man_struck` / `s_man_die` |
| 他人攻击（环境音） | 50 | `s_hit_short` |
| 施法 | 火系 137；雷/冰 8301/8302/8226；辅助 101；其它 118 | 近似表 `MAGIC_SFX` |
| 金币增加 / 掉落金币 | 106 | `s_money` |
| 其它物品掉落 | 92 | `s_drop_stonepiece` |
| 传送现身 / 换图 | 110 | `s_spacemove_in` |

### 10.6 踩过的坑

1. **autoplay 策略**：首次用户手势前 `play()` 会抛 `NotAllowedError`。用 `once` 的 pointerdown/keydown 解锁 + 静音 WAV data URI 试播，登录按钮是天然解锁点。
2. **编号表细节**：GBK 编码、`;;` 注释、252 个空值、15 个指向缺失文件的编号要跳过并记日志；`130/132/133/137` 指向 `M7-1.wav`/`M12-1.wav` 这类**非数字文件名**，`wavUrl(132)` 找不到，必须走索引。
3. **BGM 大小写/空格**：`Game over2.wav` 与 `Game-over2.wav` 是两份文件，Delphi 常量用空格版；脚本按磁盘实际文件名（大小写不敏感）解析，不要硬编码。
4. **ffmpeg 临时文件**：临时名若叫 `103.mp3.part` 会报 `Unable to find a suitable output format`；改成 `103.part.mp3` 并显式 `-f mp3`，成功后 `os.replace` 原子落盘。
5. **Range 206 分片不缓存**（§6.1 第 3 项）对音频不适用：mp3 交给 `<audio src>` 由浏览器自己处理，不走前端 scheduler，正好绕开这个缺陷。
6. **原版玩家施法音**来自 Actor 的 `m_nMagicStartSound` 等字段，本项目 `spellCast` 只带 `magicId`，所以 `MAGIC_SFX` 是**可调整的近似映射**，不是逐技能还原。

### 10.7 晚上自测清单（用户）

1. 服务在 8080（`python -m pyserver`），浏览器**硬刷新 Ctrl+F5**（`.mjs` 是 `no-cache`，但保险起见）。
2. F12 Console 应出现 `[sound] 索引已加载：714 个编号 / 7 首 BGM / 15 条缺失`。
3. 首次点击/按键后应出现 `[sound] 浏览器音频已解锁`，并听到登录 BGM（`Log-in-long2`）。
4. 控制台直接验证：`__sound.status()`、`__sound.play(103)`（按钮）、`__sound.play(106)`（金币）、`__sound.setBgm('field')`。
5. 登录成功 → 选角 BGM；进游戏 → 编号 102 主场景主题曲（约 44 秒一轮、曲间停约 2 秒，原版行为）；走两步、打一下怪、喝药、捡金币、点背包，逐项听声。可用 `__sound.setBgm('field')` 切回 191 秒的 `Field2` 长曲做 A/B 对比，各曲对照表见 §15.5。
6. 右上角「声音」按钮是**开关**：默认开启，所以**第一次点击是关闭**，再点一次恢复；日志会提示。音量用 `__sound.setVolume(0.5)` / `__sound.setBgmVolume(0.2)`。
7. 回退链路：临时移走 `resources/Audio/103.mp3`，点按钮应自动改播 `Wav/103.wav`；把 `frontend/audio/audio_map.json` 移走则退化到 `resources.json` 的 `wavUrl()`（非数字文件名音效会缺）。测完记得恢复。
8. HTTP 自查：`/audio/audio_map.json` → 200 `application/json`；`/resources/Audio/103.mp3` → 200 `audio/mpeg`；带 `Range: bytes=0-99` → 206 + `Content-Range`。

### 10.8 已知边界

- 15 个缺失编号（291/292/294/295/943/1503/1922/2103/2141/2151/9224/9234/10110/10111/10521）静默跳过。
- 玩家受击/死亡固定用男声 138/144：WS 消息没带性别，要女声改 139/145。
- 怪物叫声已实现（按 `Appr×10+200+偏移` 公式，见 §13.1「怪物叫声 MonSound」）；逐技能精细施法音仍为 `MAGIC_SFX` 近似映射（WS 仅带 `magicId`，非逐技能 `m_nMagicStartSound`）。
- **未做浏览器端真听 E2E**：本次验证是脚本自检 + Node mock + HTTP 实测，听感与场景切换由用户晚上自测确认。

---

## 11. 渲染防闪烁（2026-11-09 追加）

### 11.1 现象与实测

用户反馈「动画刷新时闪屏严重」。用 Playwright 无头 Chromium 连续抓帧（把 800×600 下采样成 100×75 / 200×150，逐帧比较平均亮度、黑像素比例、相邻帧像素差）复现，修复前：

| 阶段 | 平均亮度 | 亮度跳变 | 中心区最大差异 | 动画帧缺失 | HUD `clearRect` |
|---|---|---|---|---|---|
| 刚进游戏 1.5 s | 0.4 → 144.9 | 2 次（+90.4 / +54.1） | 0.894（89% 像素变） | 72/480 | 1 |
| 稳定静止 1.5 s | 110.2 恒定 | 0 | 0.004 | 0/460 | 0 |
| 移动 1.5 s | 103.4 → 110.3 | 0 | 0.169 | 10/470 | 8 |

结论：静止画面本身不闪，闪集中在 **加载期整屏由黑变亮且瓦片逐块冒出**、**动画切帧时纹理未就绪**、**移动时 HUD 每帧被清空重画** 三个时机。

### 11.2 四个根因

1. **直接在可见 canvas 上「清屏 → 逐块重绘」**：`PlayScene.render` 每帧 `fillRect(0,0,800,600)` 黑底，再画 200+ 瓦片与实体；GPU 合成可能把「清屏后尚未画完」的中间态呈现出来。
2. **纹理全按需懒加载**：`MapActor._getTex` / `ActorSprite._texture` 未就绪返回 `null` 就不画，首屏瓦片一块块出现、动画换帧时角色整人消失一帧；且失败占位永不重试，某些格子会永久黑块。
3. **异步加载期间仍每帧清黑**：`PlayScene.onEnter` 是 `async`，`mapActor.enter()` 完成前 `map` 为 null，`render` 依旧每帧清黑屏。
4. **HUD 的 async 清空空窗**：`_renderVitals()` / `_renderLevelNumber()` 先同步 `clearRect`，再 `await lib.getTexture(...)`；`_hudKey` 含 `x/y`，移动时每帧变化 → 每帧清空两个 DOM canvas，纹理未缓存时留下空白帧。

### 11.3 改动（4 个前端文件，服务端 / WS 协议零改动）

| 文件 | 改动 |
|---|---|
| `frontend/main.mjs` | 新增 `backCanvas`（800×600，`alpha:false`）；主循环 `render(backCtx)` → `ctx.drawImage(backCanvas,0,0)` 一次性 blit，浏览器永远只看到完整帧 |
| `frontend/map/MapActor.mjs` | `enter(mapId, cx, cy, onProgress)` 先 `map=null`，`loadMap` 后 `_prefetchVisible()` 一次性预取首屏地面/小地砖/对象（含动画对象全帧），全部就绪或 6 s 超时才挂 `map`；`_getTex` 失败占位 5 s 后可重试；新增 `_texVersion` |
| `frontend/actor/ActorSprite.mjs` | `getLayers` 逐层回退 `_lastByLib`（最近成功纹理），全缺时回退 `_lastLayers`，绝不整人消失；`_texture` 顺带预取同一图库后续 6 帧（动画帧连续编号） |
| `frontend/scene/PlayScene.mjs` | 加载期不清屏、保留上一帧并叠「正在加载地图资源 x/y…」提示；HUD 提前构建与地图预取并行；`_hudKey` 去掉 `x/y`（地图名/坐标改纯 DOM 文本、仅在变化时写）；`_buildHud` 预取 prguse 4/5/6/30..39 后置 `_hudTexReady`，`_renderVitals` / `_renderLevelNumber` 改**同步**读 `WIL.textures`，清空与重画在同一调用栈完成 |

### 11.4 修复后验证（同一套抓帧脚本）

- 加载期：黑屏带进度提示 → **一次性**切换到完整地图（仅 1 次亮度跳变，约第 21 帧 / 0.35 s），不再逐块冒出。
- 稳定 / 移动：亮度跳变 **0**；强制走路动画 288 次 `getLayers` → **0 次 null**（修复前 10/470）；HUD `clearRect` 在移动期间 **0 次**（修复前 8 次）。
- 首屏地图纹理 **231/231 全就绪**；血球 canvas 8740 个非透明像素、等级数字有内容；无 console / page error。
- 双缓冲未冻结画面：强制平移视口 2 格后可见 canvas **62% 像素变化**；帧间隔稳定 16.7 ms。
- 回归全绿：全部 `frontend/**/*.mjs` 语法检查、`test_audio.mjs` 18/18、`test_pyserver` 48 PASS、`test_all_messages` 18/18、`test_tick_systems` 53/0、`verify_resources --self-test` 10/10、`convert_audio --check --probe 20` 723/723 + 20/20 可解码、`git diff --check` 通过。

### 11.5 复测命令与边界

诊断脚本放在 `data/`（`.gitignore` 忽略，不提交），依赖本机 `F:\funasr\.venv\Scripts\python.exe` 的 Playwright 与系统 Chrome，用 `player` / `123456` / `TestHero` 自动登录抓帧：

```bash
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe.py        # 亮度/差异 + idle.png / walk.png
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe2.py       # 高分辨率 + 动画帧缺失 / HUD 清空计数
"F:/funasr/.venv/Scripts/python.exe" data/doublebuffer_check.py   # 验证 blit 每帧生效
```

- 浏览器必须 **Ctrl+F5 硬刷新** 才能加载新的 ES module。
- 预取让进游戏多约 0.3~0.5 s（显示加载进度，不再是黑屏）；6 s 超时兜底，超时未就绪的格子交给 `_getTex` 按需重试。
- 双缓冲每帧多一次 800×600 blit，GPU 开销略增，换的是「无中间态」。
- 静态地面层离屏缓存当时**未做**（每帧逐块画 200+ 次 `drawImage`）——**已在 §12 完成**，改为按世界格原子的离屏滚动缓存，每帧只贴一次。
- 真实带 GPU 浏览器的观感 E2E 仍需用户确认；本次是无头 Chromium 的定量对比。

---

## 12. 选角闪烁与移动阴影跳变（2026-11-09 第三轮，追加）

### 12.1 用户反馈与实测定位

用户反馈：「好一些，不过人物选择还是闪烁厉害；树和建筑的阴影部分移动时颜色还是会跳变」。用 4 个新诊断脚本定位（都在 `data/`，`.gitignore` 忽略）：`flicker_probe3.py`（选角 110 帧 + 地图 idle/moving 140 帧）、`shadow_probe.py`（追踪固定世界格 RGB + bng/mid/obj 三层就绪状态）、`floor_scroll_check.py`（强制平移 12 格 + 往返像素对比）、`aligned_residual_check.py`（补偿位移后的逐帧残差）。

- 选角界面首圈 110 帧里 `_chrTex` 返回 null **6 次**：10 帧站立动画每 300 ms 换一帧，首圈每帧都要重新发 Range 请求，未就绪就跳过 `drawImage` → 人物一帧帧消失。全部就绪后稳定（`chrNull=0`）。
- 地图静止时固定世界格颜色 **0 跳变**、三层 `readyChanges=0`，说明已加载的格子没问题；问题在**新进入视野的格子**：修复前移动期 `_getTex` 返回 null 59 次，`tiles/957`、`objects/8105`、`objects6/1110` 等都是「首次加载缺失」。
- 原绘制是「先所有 `bng` 大地砖、再所有 `smtiles` 小地砖」两遍循环、各自 `_getTex` 各自就绪就画 → 同一格会先显示**亮地砖**、随后突然叠上**暗阴影 / 树影**，这就是「阴影颜色跳变」；走出首屏预取范围（±10 格）后必现。
- 补充排除：8 位调色板 alpha 只有 0 / 255（无半透明阴影色），所以跳变不是 alpha 混合，而是**整块贴图延迟出现/缺失**；`shiftX/shiftY` 已 `Math.round` 整数化，瓦片绘制坐标也是整数，不是亚像素插值。

### 12.2 根因

1. **选角**：`SelectCharScene._chrTex` 懒加载且无帧回退；背景 `Prguse[65]` 也在 `render` 里首次触发异步加载，加载期每帧清黑。
2. **地面逐层异步到位**：`tiles`（96×64 的 2×2 大图）与 `smtiles`（48×32 阴影/装饰）分两遍循环，没有「按格原子提交」概念。
3. **不做移动增量预取**：`_prefetchVisible` 只在 `enter()` 跑一次，走远后新格子全靠 render 内按需加载。
4. **对象层同理**：动画对象当前帧未就绪时整棵树/建筑消失一帧；`lighter` 混合用全局赋值再还原，异常路径可能泄漏；`getLib()` 返回空时占位永远不标记失败（如 `objects6/1040`）。
5. 视野内只有 1 个动画对象（`(307,637)`，`blend=true`，10 帧），且在屏幕外；动画帧切换不是本次跳变主因。

### 12.3 改动（2 个前端文件，服务端 / WS 协议零改动）

| 文件 | 改动 |
|---|---|
| `frontend/ui/SelectCharScene.mjs` | `_preload()` 预取背景 + `_ensurePortraits()` 按实际角色 job/sex 一次并发预取 10 站立帧 + 冻结帧（无角色时预取武士男/女两组）；`_wanted` / `_checkPortraits()` 门控动画帧推进；`_chrTex(idx, slot)` 逐槽回退最近成功帧、失败 5 s 重试；背景未就绪时**不清屏、保留上一帧 + 加载提示**；`_prgUrl` 加并发去重，避免同一张图重复 PNG 编码 |
| `frontend/map/MapActor.mjs` | 新增**地面层离屏滚动缓存** `_ensureFloor` / `_floorTex` / `_paintFloorCells` / `_syncFloor`：1760×957 画布以世界格窗口为坐标，`cells[]` 标记每格已完整绘制；每格只在 bng 象限 + mid **全部就绪**时一次性画入，视口滚动时把旧内容按格差平移复用、只补新行列；每帧只 `drawImage` 一次（原 200+ 次）。新增 `_maybePrefetch` / `_prefetchAround`：中心移动 ≥4 格时按格原子预取 ±(halfW+6) / ±(halfH+8)，24 格一批、批间让出主线程、单请求 8 s 兜底。新增 `_getObjFrame`：动画对象当前帧未就绪回退该对象已加载的最近帧；`lighter` 改 `ctx.save()/restore()`；`getLib` 为空时明确标记 failed |

窗口冗余 `PX=10, PY=8` 是必须的：`ox = floor((800-48)/2) = 376`、`oy = floor((445-32)/2) = 206`；若 `DX/DY > 0`，屏幕左/上缘会露黑底。实测 `PX=4` 时 `DX=+88`，截图出现黑色竖条；改 10 后 `DX=-200 / DY=-146 / right=1560 / bottom=811`，完整覆盖 800×600。

### 12.4 修复后验证

- **像素级等价**：静止截图 `map_after2.png` 与修复前 `map_idle.png` **MD5 完全相同**，证明 bng 象限裁剪 + 滚动缓存没有改变正常画面。
- 选角：`chrNull 6 → 0`；立绘缓存 22/22 全就绪；`SELECT full` 亮度跳变 0、hard diff max 0.0005。
- 移动期 `top null keys` 从「多个首次加载缺失」变成只剩 `objects6/1040`、`objects6/1055` 两个**永久空图**（1.80 客户端 WIL 里就是空索引），不再有新格子的临时缺失。
- 强制平移 **12 格**：`painted 1178/1178`、无黑块/错位、截图目视正常；往返 12 格后与初始画面像素差 **0.23%**（滚动复用无累积误差）。
- 双缓冲仍生效：强制平移后可见 canvas **61.6%** 像素变化；静止帧差 0；`flicker_probe2` 加载期跳变 1、settled/moving 0、动画缺帧 0/188。
- 对齐平移残差：静止 **0.00**；连续平滑卷动 avg **1.59**、8 个尖峰（25~33）周期约 0.4 s，来自角色/动画对象**正常帧切换**，不是纹理加载突变。
- 回归全绿：全部 `frontend/**/*.mjs` 语法检查、`test_audio.mjs` 18/18、`test_pyserver` 48 PASS、`test_all_messages` 18/18、`test_tick_systems` 53/0、`verify_resources --self-test` 10/10、`convert_audio --check --probe 20` 723/723 + 20/20 解码、`git diff --check` 通过。

### 12.5 复测命令与边界

```bash
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe3.py         # 选角 + 地图 idle/moving 抓帧
"F:/funasr/.venv/Scripts/python.exe" data/shadow_probe.py            # 固定世界格 RGB / 三层就绪状态
"F:/funasr/.venv/Scripts/python.exe" data/floor_scroll_check.py      # 强制平移 12 格 + 往返像素对比
"F:/funasr/.venv/Scripts/python.exe" data/aligned_residual_check.py  # 对齐平移残差
```

- 浏览器必须 **Ctrl+F5 硬刷新**（ES module 有缓存）。探针用 `player` / `123456` / `TestHero` 自动登录；该角色当前在 `(312,605)`，向东是墙，所以平移验证脚本用**强制改 `_viewX`** 而不是发 `move` 消息。
- 地面缓存画布 1760×957 ≈ 6.7 MB，`cells` 38×31=1178 格；换图时整个丢弃重建。
- 增量预取范围 ±(halfW+6) / ±(halfH+8)，每批 24 格并让出主线程，不会卡渲染；极端快速长距离传送仍可能看到边缘格子短暂黑色，但只会「黑 → 完整」一次，不会再「亮 → 暗」两次变色。
- 树/建筑的**对象层**仍每帧按行绘制以保持遮挡关系，未缓存；动画对象帧切换（每 200 ms）是原版行为，观感上仍会有轻微明暗变化。
- 真实带 GPU 浏览器的观感仍需用户确认；本轮全部是无头 Chromium 定量对比 + 截图像素比对。

---

## 13. 保真度补齐（2026-11-09 第四轮，追加）

独立 Agent 对照原版 Delphi `GameOfMir/Client`（与 `GameSrv`，若含）做了系统性差异评估，结论是**高保真移植**：动画帧表/帧推进、调色板/透明/加色、地图三层渲染与深度排序、命中/伤害/暴击数值公式、音效编号与 BGM 均已对齐。本轮只修评估中标注为「中风险、观感/数值偏差」的 3 项（+1 个边界一致性），均为局部改动。

### 13.1 三项修复

| 项 | 根因（Delphi 对照） | 改动 | 文件 |
|---|---|---|---|
| **命中帧时序** | 原版在攻击动作第 2~3 帧（武器接触点，约 170ms，`HA.hit` 6 帧×85ms）才结算命中；此前 `attack` 消息一到达就触发目标受击/伤害飘字/命中特效，观感「先爆后砍」 | 新增 `HIT_FRAME_MS=170`；目标侧 `struck`+伤害飘字+命中特效改 `setTimeout(HIT_FRAME_MS)` 延迟触发（用 `cur=entities.get(targetId)` 取当前坐标，避免目标移动后落点错）；攻击者武器辉光 `_swordHitAt` 顺延 `+HIT_FRAME_MS` | `frontend/scene/PlayScene.mjs` |
| **静态 blend 降级** | 原版 `DrawScrn` 对 `boBlend`（0x80 标志）一律 `lighter` 加色，**与是否动画无关**；此前 `blend` 仅在 `hasAni` 时赋值，导致非动画的火/光类静态贴图丢失辉光 | `const blend = !!t.blend` 与动画解耦；动画帧裁剪改为 `Math.max(1, Math.min(32, t.aniFrame))`，与 `_prefetchAround`/`_getObjFrame` 的 32 帧上限一致（消除 >32 帧对象循环到前 32 帧的缺帧闪烁） | `frontend/map/MapActor.mjs` |
| **等级上限冻结** | `world.py` 早期只抄了 Java `ExperienceTable.NEED_EXPS` 前 65 项，`exp_for_level`/`level_for_exp` 被钳到表尾 → 65 级后经验需求冻结、满级≈65 | `NEED_EXPS` 补全到与 Java **逐值一致**的 101 项（index 0→等级1 … index 100→等级101，末值 2140000000）；`exp_for_level(101)` 校验值不变、升级阈值按原版推进到 101 级 | `pyserver/world.py` |
| **未命中 MISS 表现** | 原版 `SM_STRUCK` 携带 miss 标志，目标位置飘 MISS、不扣血、不播受击；此前服务端 miss 时只给攻击者发系统提示、目标端完全无感（且攻击玩家分支无命中判定、永不 miss） | `_attack_monster` 未命中时额外 `broadcast_around(attack, miss=True)`（不扣血/不 kill/不 send_stats），主击与半月/刺杀第二击两处均补；前端 `attack` 处理：miss 时攻击者不点命中辉光、目标端飘红色 "MISS" 且不播 `struck`；`_spawnDamageNumber` 支持自定义颜色 | `pyserver/handlers_combat.py` + `frontend/scene/PlayScene.mjs` |
| **WIL 末张图位深** | 末张图无 `nextOff`，原回退值 `fileOff+8+width*height` 永远判 8 位；若末张实为 16 位 RGB565 会只读半截而整图花屏 | 末张图 `nextOff` 改用真实 `this.fileSize` 判定位深（与非末张统一），8/16 位均正确 | `frontend/gfx/wil.mjs` |
| **怪物叫声 MonSound** | 原版 `sound.lst` 约定怪物声音索引 = `Appr×10+200`，六态偏移 0 发现/1 站立/2 攻击喊/3 攻击武器/4 挨打/5 死亡；此前缺这份映射，怪物完全无声 | 采用网上多源一致的公式（传奇论坛/博客园/upx8 Wiki 均确认）；前端 `registerEntity` 把实体 `appr` 注册到 `SoundManager`，战斗 `attack`/`death` 消息按公式播放 **攻击(+2)/挨打(+4)/死亡(+5)**；索引不在 `audio_map.json` 的怪物自动静默（不 404、不报错）；玩家砍怪只播怪挨打声、怪咬玩家同时播玩家被击+怪攻击喊、怪咬怪双方各发声、冲撞顶怪只播怪挨打声 | `frontend/audio/SoundManager.mjs` + `frontend/scene/PlayScene.mjs` |

### 13.2 验证

- 经验表：`len(NEED_EXPS)=101`；`exp_for_level(1)=100 / (65)=1700000000 / (101)=2140000000 / (200)=2140000000`，与 Java `ExperienceTable` 逐值一致；`level_for_exp` 现循环 1..101 全表，不再停在 65。
- 前端：`node --check` 两个改动文件均通过；Playwright 实烟（自动登录 `player/123456/TestHero` 进图）**无 console/page 错误**，画面非黑占比 92.9%，地图 700×700、271/276 纹理就绪 —— 证明 blend 解耦与命中帧改动不破坏渲染。
- 回归全绿：`test_pyserver` 48 PASS / `test_all_messages` 18/18 / `test_tick_systems` 53/0 / `test_audio` 18/18 / `compileall` 通过 / `git diff --check` 通过。
- 服务已重启（原 PID 29560 停用，新进程 `python -m pyserver` 已就绪，`/api/migration` 46/46，`onlinePlayers=0`），`world.py` 改动已生效。
- MISS 通道 + WIL 末张图：live 回归 18/18 通过（攻击怪物循环重试命中，miss 广播不破坏「造成伤害」断言）；`node --check` 全部前端改动文件通过；WIL 末张图位深改用真实 `fileSize` 判定，8/16 位均正确。
- 怪物叫声：用真实 `Monster.csv`(273 种) + `audio_map.json` 量化，`Appr×10+200+{2,4,5}` 覆盖 → **191 种三音齐全、20 种部分、62 种资源无索引自动静默**；node 直接 import `SoundManager` 跑路由单测 6/6 通过（玩家砍怪/怪咬玩家/怪咬无音怪/怪死亡/无音怪死亡/冲撞顶怪）。

### 13.3 仍属「已知边界、未在本轮处理」（评估认定的低/中风险残留）

- 战斗「0 伤害」仍保留 `max(1,…)`（与 Java 版对齐的简化；原版防御>攻击可显 0 伤害，需另补 0 伤害通道）；**攻击玩家分支仍无独立命中判定（PvP 永不 miss）**——与攻击怪物分支不一致，属 PvP 行为级改动，本轮未动，待用户确认是否对齐原版。MISS 通道已补齐（见 §13.1 第二项）。
- 施法音为 `MAGIC_SFX` 近似映射（火系→137、雷/冰→8301/8302/8226，非逐技能）；受击/死亡固定男声（WS 未带性别）。**怪物叫声已实现**（见 §13.1「怪物叫声 MonSound」），逐技能精细施法音仍近似。
- 网络层无单连接频率限流与 `maxPlayers`/地图人数硬上限（`net.py` 仅队列 512）；冲撞(rush)无专属身位动画；受击姿势比原版多保持 ~300ms。
- 树/建筑的**对象层**仍每帧按行绘制（保遮挡）；动画对象帧切换（每 200ms）是原版行为，观感仍有轻微明暗变化。
- 真实带 GPU 浏览器的命中帧观感、blend 辉光观感、MISS 飘字观感仍需用户确认；本轮是无头 Chromium 定量对比 + 截图像素比对。

### 13.4 提交

见 `git log --oneline -1`（最新一条 `feat(python-server): ` 提交，4 个文件：2 前端 + `world.py` + 本 handoff；怪物叫声轮为 2 前端 + 本 handoff，服务端零改动）。

---

## 14. 进游戏阻断修复：Python 3.10 + slots dataclass + 零参数 super()（2026-11-09 夜，实测反馈）

### 14.1 症状
选择角色 → 弹出公告(notice) → 点「确定」后卡住、进不了游戏；服务端日志：
`[ws] handler noticeOk failed: TypeError: super(type, obj): obj must be an instance or subtype of type`
（`service.py:170` 的 handler 兜底吞掉异常、只打一行摘要，客户端表现为「没反应」）

### 14.2 根因
- **8080 服务实际运行于 `D:/Python310/python.exe`（Python 3.10.11）**；此前所有复现/回归都用 `C:/Users/Mao/.workbuddy/binaries/python/versions/3.13.12/python.exe`，因此一直没复现，历史 live 回归也全绿。
- `world.py` 的 `Actor`/`Player`/`Monster` 都是 `@dataclass(slots=True)`。`slots=True` 会**重建类对象**；Python 3.10~3.12 不修正零参数 `super()` 捕获的 `__class__` cell（仍指向重建前的旧类），3.13 才修好。
- 实测 3.10：`Player.brief_json.__closure__[0].cell_contents is Player` → **False**；于是 `super().brief_json()` 等价于 `super(旧Player, 新Player实例)` → 抛上述 TypeError。`Player.brief_json`（原 278 行）与 `Monster.brief_json`（原 565 行）两处均中招。
- `_notice_ok → _do_enter → map_snapshot / broadcast_entity_add` 都会调 `brief_json()`，异常被吞 → `enterGame` 快照发不出去 → 卡在公告弹窗。

### 14.3 修复
两处零参数 `super().brief_json()` 改为显式 `Actor.brief_json(self)`（跨 3.10~3.13 语义一致，不依赖 `__class__` cell）。仅改 `pyserver/world.py`，协议/前端零改动。
已排查：全库 `super(` 调用只这两处；所有 `@dataclass(slots=True)` 中只有 `Player`/`Monster` 带继承，无其他同类隐患。

### 14.4 验证
- 3.10 直接调 `Player.brief_json()` / `Monster.brief_json()` 均正常；离线 `noticeOk` 全链路发出 `enterGameResult`/`enterGame`/`stats`/广播。
- 双解释器回归：`test_pyserver` 48/48、`test_tick_systems` 53/0（3.10 与 3.13 各跑一遍）。
- 服务用 3.10 重启（`/api/status` 正常、`/api/migration` 46/46），`test_all_messages --live 8080` 18/18。
- **真实 WS 端到端**（账号 `player/123456/TestHero`）：`login → listCharacters → enterGame(→notice) → noticeOk`，收到 `enterGameResult`/`enterGame`/`stats`/`entityAdd`/`move`；`enterGame` 快照含 `player=TestHero`、`nearby=4`、`map=0`，服务端日志无 handler 异常。

### 14.5 运行环境约定（重要）
服务端实际解释器是 **`D:/Python310/python.exe`（3.10.11）**，直接 `python -m pyserver` 即用它。**所有复现、回归、live 测试必须同样用 3.10 跑**；只用 3.13 会掩盖 3.10 专属的语言/标准库差异（本次即因此漏检）。

---

## 15. 进游戏 BGM 对齐原版 Delphi（2026-11-09 夜，用户反馈「登录游戏的音乐貌似也不对」）

### 15.1 原版权威依据（GameOfMir/MirClient Delphi 源码）

| 源码位置 | 原版行为 | 对应资源 |
| --- | --- | --- |
| `SoundUtil.pas` 常量 | `bmg_intro='wav\log-in-long2.wav'`、`bmg_select='wav\sellect-loop2.wav'`、`bmg_field='wav\Field2.wav'`、`bmg_gameover='wav\game over2.wav'`；`s_main_theme=102` | — |
| `IntroScn.pas::TLoginScene.OpenScene` | `PlayBGM(bmg_intro)` | `Log-in-long2.wav`（34.3 s） |
| `IntroScn.pas::TSelectChrScene.SoundOnTimer` | `PlayBGM(bmg_select)` | `sellect-loop2.wav`（43.9 s） |
| `PlayScn.pas::TPlayScene.SoundOnTimer` | **`PlaySound(s_main_theme)`**，源码注释「游戏主场景的背景音乐（长度：43秒）」，首次播放后 `m_MainSoundTimer.Interval := 46*1000` → 每 46 秒重播一次 | **`sound.lst` 第 102 项 `wav\102.wav`（43.9 s，单声道 44.1 kHz）** |
| `DrawScrn.pas::ChangeScene` | 切场景先 `CloseScene`（`SelectChrScene`/`PlayScene` 均 `SilenceSound`）再 `OpenScene` | — |

关键结论：在 `IntroScn.pas` / `PlayScn.pas` / `DrawScrn.pas` / `clEvent.pas` / `ClMain.pas` 中**均没有 `PlayBGM(bmg_field)` 调用点**；`Field2.wav`（191.5 s / 32.2 MB，社区资料称之为「SD 后来放进去的」）不是原版进游戏默认曲。原版主场景音乐是**编号 102 的 43 秒主题曲**，且不是无缝循环，而是 46 秒定时器重播（曲间约 2 秒静音）。

### 15.2 修正前的问题

`SoundManager._onMessage` 的 `enterGame` 分支是 `setBgm('field')` → 播 `Field2.mp3`（191.5 s 无缝 loop）。登录/选角两首是对的，只有**进游戏这一首错**：曲子长 4 倍、旋律不同、且没有原版的曲间停顿。上一轮 §14 修好 `noticeOk` 后用户第一次真正进到游戏，才听到这首曲子并反馈「不对」。

### 15.3 改动（仅 `frontend/audio/SoundManager.mjs`，服务端/协议/索引文件零改动）

- 新增常量 `BGM_PLAY_ID = 102`、`BGM_PLAY_GAP_MS = 2100`（46 s − 43.9 s）。
- `_loadMap()` 从 `sounds['102']` 派生 `bgm.play`（原版主场景音乐本来就是 `sound.lst` 编号，不在 BGM 表里；避免改 `convert_audio.py` 与已提交的 `audio_map.json`）。派生后 BGM 键为 8 个。
- `_startBgm()`：`play` 键不设 `loop`，改用 `onended` + `setTimeout(2100)` 从头重播，复刻原版 46 秒周期；其它键仍是 HTML5 无缝 loop。切歌/关闭声音时清理重播定时器。
- `enterGame` → `setBgm('play')`；`field` 保留为可手动切换的键。

### 15.4 验证

- `node` 直接 import `SoundManager` + mock `Audio`/`fetch` 的真实路由单测 **12/12 通过**：索引派生 `play→102.mp3`、`loginResult`/`characterList`→select、`enterGame`→play 且 `loop=false`、曲终排 2100 ms 重播并从头播放、`setBgm('field')`→Field2 无缝 loop、`toggle()` 清定时器。
- `/resources/Audio/102.mp3` 实测 **HTTP 200 / audio/mpeg / 527300 bytes**（`field2.mp3` 同样 200）。mp3 与原 wav 时长逐一比对一致（`102`: 43.9 s、`Field2`: 191.5 s、`Log-in-long2`: 34.3 s、`sellect-loop2`: 43.9 s），排除转码错文件/变调。
- 按 §14.5 约定用 **Python 3.10** 跑：`test_pyserver` 48/48、`test_tick_systems` 53/0、`test_all_messages --live 8080` 18/18；8080 服务无需重启（纯前端改动）。

### 15.5 曲目对照（供自测 A/B 试听，控制台 `__sound.setBgm('<key>')`）

| key | 文件 | 时长 | 用途 |
| --- | --- | --- | --- |
| `intro` | `log-in-long2.mp3` | 34.3 s | 登录界面（默认）；与 `Game-over2.wav` **MD5 完全相同** |
| `select` | `sellect-loop2.mp3` | 43.9 s | 选角界面（默认） |
| `play` | `102.mp3` | 43.9 s | **游戏内（本次新默认）**，原版 `s_main_theme`，46 秒周期 |
| `field` | `field2.mp3` | 191.5 s | 后期附加长曲（修正前的默认值） |
| `theme` | `main_theme.mp3` | 36.0 s | 社区「经典选角曲」候选 |
| `gameover` | `game_over2.mp3` | 68.6 s | 社区「经典登录曲」候选 |

### 15.6 仍待用户确认（**未硬改**）

若「不对」指的是**登录/选角那两首不是记忆中的经典旋律**：那是资源包内容问题，不是代码映射错误。博客园/17173 的玩家改名方案是把 `Game over2.wav` 当 `Log-in-long2.wav`、`main_theme.wav` 当 `sellect-loop2.wav` 使用（即上表 `gameover` / `theme` 两键）。本轮**不擅自替换默认曲**，因为无原版源码依据；用户可用上表命令试听后指定，再把对应键设为默认即可。

---

## 16. 四项实测反馈（建角选职业 / 连点无动画 / 条带黑条 / 大地图与系统菜单）（2026-11-09 第五轮）

用户一次性反馈 4 项，要求「如果上面功能是缺失，就参考 delphi 版本补上」。核查结论：**4 项都不是「功能不存在」，而是可见性 / 节流 / 缓存 / 入口四类实现缺口**；服务端 `pyserver/**` 与协议本轮零改动，全部改在前端（+ 一处测试断言）。原版依据：`IntroScn.pas::MakeNewChar / SelChrNewJob / SelChrNewm_btSex`、`FState.pas::DCreateChr`、`ClMain.pas` 的 `FormKeyDown`（TAB / ESC / F9-F11）与 `ViewMiniMapStyle`。

### 16.1 四项修复对照

| 用户问题 | 实际根因 | 改动 | 文件 |
| --- | --- | --- | --- |
| **1. 创建人物时如何选职业?** | 按钮与 `createCharacter{job,sex,hair}` 参数一直存在，但 ① `DCreateChr` 背景图用 `z-index:-1`，父容器未建立层叠上下文 → 整张面板（含职业按钮底图）掉到游戏 canvas 后面，只剩几个「按下态」小图标浮在人物身上；② 选中态不保持、无文字状态、点职业后立绘不换 | 背景图改 `z-index:0` + `pointer-events:none`（与 `PlayScene` NPC 对话框的既有做法一致）；新增 `_refreshCreateSelection()` / `_setJob()` / `_setSex()` / `_setHair()`，选中按钮保持按下态、底部显示「职业／性别／发型」、按钮加 `title` 悬停提示；`_ensurePortraits()` 在建角模式预取当前职业/性别的 10 帧站立 + 冻结帧；`render()` 在建角槽实时绘制所选职业/性别立绘；操作提示移到性别与发型按钮之间的空白带（原 `top:131` 会压住面板自带「职业」二字） | `frontend/ui/SelectCharScene.mjs` |
| **2. 快速点击多次，人物坐标还在位移但不播动画** | `_onMouseDown` 每次点击把 `_lastMoveAt` 归零，绕过 `WALK_INTERVAL` / `RUN_INTERVAL` 节流 → 连点灌入 N 条 `move`，服务端无 cooldown 连续推进；镜头匀速追赶要花 N×间隔，固定 600 ms 动画超时却先把动作切回 `stand` | 新增 `_pendingStep` 布尔：距离上次发送不足一个间隔时只挂 1 个待发步骤（N 次连点最多补 1 步），与按住移动共用 `_lastMoveAt` 节流；render 新增 `selfCatchingUp` 判定，镜头仍在追逻辑坐标时不把自身 `walk/run` 切回 `stand` | `frontend/scene/PlayScene.mjs` |
| **3. 地图出现「一条一条、带间隔」的黑条** | `_floorTex()` 把 `failed` 纹理当成 `ready`，`_paintFloorCells()` 随即把该格涂成纯黑并 `cells[idx]=1` **永久锁定**；首屏预取超时 / Range 抖动留下的 failed 占位就是那些间隔黑条，且永远不会恢复 | 兜底底色 `FLOOR_FALLBACK='#1e1a12'`（初始填充不再是 `#000`）；`failed` 超过 5 秒删占位重新 `_getTex()`；未就绪格改 `_borrowFloorCell()` 借用相邻已绘制格像素且**不写 cells=1**，纹理到位后下一帧覆盖；`tiles` 图不足 96×64（象限越界）时整图铺满当前格 | `frontend/map/MapActor.mjs` |
| **4. 如何看大地图？如何打开系统菜单？** | 只有 HUD 按钮，`_onKeyDown` 未绑定 Tab / V / Esc / F9 / F10 / F11，注释甚至写着「不接快捷键」；没有大地图与系统菜单面板 | 按原版补 `Tab` 三态（关→小地图→大地图→关）、`V`（关→半透明→不透明）、`Esc`（先关最上层窗口，无窗口则开系统菜单）、`F9` 背包 / `F10` 状态 / `F11` 技能；新增 DOM 覆盖层大地图（复用 `mmap` 底图，标白=自己／红=怪／黄=NPC／蓝=玩家）与系统菜单；模态面板首次点击只关闭不穿透成移动/攻击；进游戏给一行快捷键提示；`onExit` 清理新增状态 | `frontend/scene/PlayScene.mjs` |

### 16.2 顺带修掉的两个既有缺陷（都是「验证暴露出来的真问题」）

1. **UI 贴图偶发整片空白**：`wil.mjs::createCanvas()` 原先一律返回 `OffscreenCanvas`。实测无头 Chromium 下 `convertToBlob()` 会抛 `NotReadableError: Readback of the source image has failed`，且**同一画布重试 4 次仍失败**、内容全透明；`Prguse[62]/[64]`（确定/关闭按钮）因此曾完全空白，建角窗口的 e2e 也被这个异常卡住。主线程全部改用 `HTMLCanvasElement`（`toDataURL()` 读回稳定、同样可作 `drawImage` 源），仅在无 `document` 的 worker 里保留 `OffscreenCanvas`。诊断数据：修复后 11 张 Prguse 图全部拿到正常像素（`62`: 2505 可见像素 / `64`: 368），`pageerrors: []`。
2. **大地图标题显示成 `0`**：服务端 `snapshot.map.name` 是内部地图编号，中文名在 `map.title`（HUD 左下角用 `title || name`）。`_renderBigMap()` 统一为 `title || name`，现显示「比奇省　(332, 480)」。
3. **回归脚本里的 §15 遗留断言**：`scripts/test_audio.mjs` 仍断言 `enterGame → bgmKey='field'`，与 §15 已提交的 `play`（102 号主场景主题曲）冲突，导致 `17/18`。改为断言 `play` + `loop=false` + `102.mp3`，现 **18/18 PASS**。

### 16.3 验证

- **浏览器端到端**（`D:/Python39/python.exe` + Playwright，账号 `player/123456/TestHero`，8080 live）：**28 项全通过**，含
  - 建角：`_openCreate(1)` 后 9 个按钮齐备、初始高亮武士+男、切「道士/女」后状态文字与高亮同步、`_wanted` 含新职业 10 帧、关闭窗口恢复、面板背景/按钮底图可见、提示文字不再压住面板文字；职业/性别行切换前后像素差 1655 / 1741（可见反馈真实生效）。
  - 连点：10 次快速点击 → 立即发出 `move` **0** 条、`_pendingStep=true`、`_lastMoveAt` 未被归零；镜头差 3 格时 350 ms 后动作仍为 `walk`（`dx=2.39`），把镜头对齐后恢复 `stand`。
  - 黑条：地面缓存 1760×957 / 1178 格，采样纯黑像素占比 **0**；人为把 24 个可见纹理标记为 failed 后强制重绘，纯黑占比仍为 0、1178 格中 63 格保持未锁定（可重绘）、超 5 秒后 `_floorTex` 确实删占位并重新发起加载。
  - 大地图/菜单：Tab 三态、V 半透明、Esc 开关系统菜单、F9 开关背包；大地图标题「比奇省　(332, 480)」；**浏览器无 JS 异常**；截图 `_tmp_bigmap.png` / `_tmp_sysmenu.png` / `_tmp_create.png` 目视确认面板与立绘正常。
- **前端语法**：4 个改动文件 `node --check` 全通过；`node scripts/test_audio.mjs` **18/18**。
- **服务端回归**（按 §14.5 全部用 `D:/Python310/python.exe`）：`test_pyserver.py` **48/0**、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **18/18 + ping→pong PASS**；`/api/migration` 仍 46/46。
- `git diff --check` 通过；`HANDOFF.md` 保持零改动。

### 16.4 已知边界（未硬改）

- 大地图/系统菜单用 DOM 覆盖层实现（原版是画面内绘制的自绘窗口），布局按 800×600 固定坐标；没有做窗口拖拽与分辨率自适应。
- 发型左右箭头只有「按下态」贴图，`Prguse[73]` 底图本身没有对应常态图案，所以不选中时看不见箭头；`hair` 参数服务端已接收但外观未逐发型区分。
- 真实带 GPU 浏览器的建角立绘切换观感、连点手感、黑条是否彻底消失仍需用户 `Ctrl+F5` 自测确认；本轮为无头 Chromium 定量 + 截图像素对比。
- 其余历史边界见 §13.3（PvP 永不 miss、逐技能施法音近似、rush 无专属身位动画等），本轮未动。

## 17. 六项实测反馈（BGM 交换 / 小地图 / 界面缩放 / 商店价格 / 选角文字 / 残留黑条）（2026-11-10 第六轮）

用户连续追加 6 条反馈，逐项定位后一次性落地。`HANDOFF.md` 仍保持零改动。

| 反馈 | 根因 | 改动 | 文件 |
| --- | --- | --- | --- |
| `__sound.setBgm('intro')` 听着像死亡曲、`gameover` 才像开场 | 本资源包 `Log-in-long2.wav` 与 `Game-over2.wav`（连字符）**MD5 完全相同**（34.3 s 死亡曲），空格版 `Game over2.wav`（68.6 s）才是玩家记忆中的开场曲；社区改包也正是把空格版改名当 `Log-in-long2` 用。Delphi `SoundUtil.pas` 常量本身仍是 `bmg_intro='wav\log-in-long2.wav'` | 按用户试听结论**只交换两个语义键指向的文件**，不改 Delphi 常量、不重新转码：`intro → game over2.wav / game_over2.mp3`，`gameover → log-in-long2.wav / log-in-long2.mp3`；同步自检断言与注释 | `scripts/convert_audio.py`、`frontend/audio/audio_map.json`、`scripts/test_audio.mjs`、`frontend/audio/SoundManager.mjs` |
| 小地图半透明和不透明「一样、都看不清」 | 0 号比奇省 700×700 格，101 号 mmap 底图只有 900×599；按写死的 1.5 px/格算，玩家 y=480 → `sy=660 > 599`，裁剪高度 **-61**，底图整块不画，两态只剩几个实体点（实测像素差 `0/19600`） | 改「纹理像素 / 地图格数」归一化换算，裁剪窗口以玩家为中心并**钳到底图边界**；不透明态垫深色底 + 描边，半透明态只降 alpha（`0.68`）让游戏画面透出；关闭时清空画布。大地图点位换算同步用同一口径 | `frontend/scene/PlayScene.mjs` |
| 整个界面能否等比例放大 | 原来 `#stage` 固定 800×600，窗口更大时四周留黑；且 flex 默认会压缩子项 | `#stage` 加 `flex:none` + `transform-origin:center`，`fitStage()` 按 `min(vw/800, vh/600)` 等比缩放（4:3 居中）；新增 `frontend/core/viewport.mjs` 统一换算，所有 `clientX/clientY` 与 `elementFromPoint` 都按 `rect.width/800` 折算；`window.__setUiScale(k)` / `localStorage['jootm2.uiScale']` 可固定倍率 | `frontend/index.html`、`frontend/main.mjs`、`frontend/core/viewport.mjs`（新）、`frontend/scene/PlayScene.mjs`、`frontend/gui/manager.mjs`、`frontend/gui/window.mjs` |
| 所有商店都是 10 块钱 | M2 `Market_Def` 脚本头 `%100` 才是「按 StdItems 原价出售」的倍率，`[goods]` 每行是 `物品名 库存数量 刷新时间`；**Java 版把第二列库存当成单价**直出，Python 直译后一样错，于是武器/首饰店清一色 10 | `NpcScript` 解析脚本头 `%N`（默认 100），`goods()` 价格改 `StdItems.Price × rate%`、第二列落到 `stock`；`_buy_price()` 沿用 goods 价。真实数据实测从「10 种价」变成 **49 种价**（木剑 50、青铜剑 900、金创药 80、强效金创药 540…） | `pyserver/npc_script.py`、`scripts/test_pyserver.py`、`scripts/test_all_messages.py` |
| 选角名字/职业/等级文字有偏移 | Delphi `IntroScn.pas` 的 `BoldTextOut` → Windows `TextOut(x,y)` 以**字顶**为 y；canvas 默认 `textBaseline='alphabetic'`，整行文字上浮约 10 px，正好压在背景虚线框上边框上 | `_text()` 显式 `textBaseline='top'` + `save/restore`，坐标改回 Delphi 原值：左槽 `117,493/522/552`、右槽 `671,495/524/554`（原代码各 +1）；建角预览同步 | `frontend/ui/SelectCharScene.mjs` |
| 地图还是有黑色条纹 | 第一轮 `failed` 锁死已修，但增量预取按 24 格分批提交，会出现**连续一整行纹理都未就绪**的窗口：四个邻格也都没画，`_borrowFloorCell()` 全部退化成 `#1e1a12` 近黑纯色填充，连成暗带；地图外格子仍涂 `#000`；每帧清屏也是 `#000` | 兜底地色调亮为 `#3a3222`；邻格都不可用再从 `f.lastPainted`（最近真正画过地砖的格）借像素；地图外改用地色；PlayScene 清屏改用地色；关闭/清空小地图画布 | `frontend/map/MapActor.mjs`、`frontend/scene/PlayScene.mjs` |

### 17.1 依据与边界

- `[goods]` 列义与 `%N` 倍率来自 M2 商人脚本文档（`Market_Def` 头 `%100` = 原价、`%150` = 1.5 倍；`[goods]` 行是 `物品名 数量 刷新时间`）+ 本仓库 283 个商店脚本每行都有 `%100` 的自证；**不是**凭空改数据。Java 源码 `NpcScriptEngine.goods()` 的 `// 物品名 价格 数量` 注释本身是错的，本轮未改 Java 源码（分支只维护 Python 端）。
- BGM 交换只动索引指向，Delphi 常量、`sound.lst` 编号 49 / 102 与 723 份 mp3 全部未改；如换回原版语义只需交换 `BGM_FILES` 两行。
- 界面缩放是 CSS transform，canvas 逻辑分辨率恒为 800×600，`image-rendering:pixelated` 保留像素味；DOM 文字会随缩放变平滑。缩放上限 4、下限 0.5。
- 黑条属于**纹理分批到达的瞬态**，无头 Chromium 正常帧无法复现；本轮用「人为把连续 4 行标记为未就绪」复现并验证不再出现暗带，真实 GPU 浏览器观感仍需用户 `Ctrl+F5` 自测确认。

### 17.2 验证（全部按 §14.5 用 `D:/Python310/python.exe` 跑服务端/回归，Playwright 用 `D:/Python39/python.exe`）

- 浏览器端到端 **23/23 PASS**：选角 `textBaseline=top` + 六个 Delphi 坐标；`intro/gameover` 索引与 `play=102.mp3`；小地图两态 `nonzero=14400/14400`、平均 alpha `173 vs 255`、关闭态 `0`；地面缓存纯黑像素 `0`，连续 4 行未就绪时纯黑 `0`、深色横带 `0`、20 格保持未锁定；商店面板显示 `50/900/1000/80 金币`；1200×900 缩放为 `[0,0,1200,900]`、鼠标换算 `(400,300)→(266.7,200)`、等价物理点方向一致；640×480 缩放为 `[640,480]`；两页面 `pageerrors: []`。
- `test_pyserver.py` **54/0**（新增 6 条商店列义用例）、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **20/0 + ping→pong PASS**（新增「商店返回多种价格」「首个商品价格 = StdItems.Price × % 倍率」）。
- `node scripts/test_audio.mjs` **19/19**、`convert_audio.py --self-test` **12/12**；`build_plan()` 与 `audio_map.json` 的 4 个 BGM 键逐项一致；全部改动文件 `node --check` / `py_compile` 通过。
- `git diff --check` 通过；`HANDOFF.md` 零改动。注：`frontend/audio/audio_map.json` 从生成起**整文件就是 CRLF**（HEAD 与工作区均为 3654 行 CRLF），git 默认会把新增行行尾的 `\r` 判成 trailing whitespace；本仓已设置本地 `git config core.whitespace cr-at-eol`（只影响空白检查、不进版本库），否则可用 `git -c core.whitespace=cr-at-eol diff --check`。

## 18. 对照 Delphi 原版的地图/掉落/尸体与登录动画（2026-11-10 第七轮，实测反馈）

本轮起因：用户反馈「① 大地图位置不太对 ② 掉落位置似乎也不对 ③ 鹿等怪死后没有尸体、不能挖肉，需要参考 Delphi 原版对比」，随后补充「条纹还在，只是变浅了；再次登录就恢复了」「④ 主菜单返回人物选择无效 ⑤ 登录失败文字位置不对、登录成功应该有个开门动画」。

**取证方式**：下载 `StarboyChina/MIR2-1` 的 `GameOfMir/MirClient/*.pas` 与 `M2Server/*.pas`（`ClMain / PlayScn / IntroScn / Actor / AxeMon / Grobal2 / MapUnit / FState / SoundUtil / ObjBase / M2Share`），逐条核对 Pascal 原文与常量号；调研笔记为临时文件，结论全部沉淀在本节。用户约定「找不到权威依据不硬改」，本轮每条改动都对应到具体行号。

### 18.1 六项根因与修复

| 反馈 | 权威依据 / 根因 | 修复 |
|---|---|---|
| 大地图（及小地图）位置不对 | `MiniMap.txt` 的索引是 **1-based**；原版 `ClMain.pas` 收 `SM_READMINIMAP_OK` 后以 `g_WMMapImages.Images[g_nMiniMapIndex-1]` 取图。我们直接拿 `0 101` 的 101 去 `mmap.wil` 取图，取到的是另一张 **900×599** 的地图；正确应为 index 100（**1052×699** ≈ 比奇省 700 格 ×1.5 / 700 格 ×1）。§17 里「底图尺寸与 .map 不匹配」其实是**取错图**的误判 | `_loadMiniMapTexture()` 改 `index - 1`（`index > 0` 才取），注释写明 1-based；归一化换算 `纹理像素/地图格数` 保留（等价于原版 `mx=x*48 div 32, my=y*32 div 32`，且兼容兜底自生成图）；同步修正 §17 的错误注释。大地图点位与底图现在真正对齐 |
| 掉落位置不对 | 原版 `PlayScn.pas:1268-1297` 在**按行 `for j` 循环内**绘制 `DropedItemList`（`if j = DropItem.y - BlockTop`），锚点是格中心 `ix+HALFX-d.Width/2` / `iy+HALFY-d.Height/2`（`HALFX=24, HALFY=16`，Grobal2.pas）。我们以前在整帧末尾统一画，掉落物永远盖在同格角色/树木/建筑之上；服务端 `scatter_pos()` 又在 ±3 范围随机撒 50 次，物品常离尸体很远 | `MapActor.render()` 新增 `drawDrop + groundItems` 参数，按行调度（先于同行实体绘制）；`PlayScene._renderGroundItems()` 改为单格 `_drawGroundItem(ctx,g,sx,sy)` 并 `save/restore`。服务端 `scatter_pos()` 改为原版 `GetDropPosition`（ObjBase.pas:1534-1582）：半径 1 起逐圈环形扩散、避开已有掉落物，找不到才回退尸体格 |
| 动物死后没尸体、不能挖肉 | 原版 `SM_DEATH=32 / SM_SKELETON=33 / SM_NOWDEATH=34 / CM_BUTCH=1007 / SM_BUTCH=637 / RM_BUTCH=10119`（Grobal2.pas）；`Actor.pas:1595` 死亡只置 `m_boDeath` + `PlayScene.ActorDied()` **降绘制层级、不 Free**，`ClMain.pas:6506` 在 `SM_HIDE` 后 60 s 才释放；帧分 `ActDie`（倒地动画）与 `ActDeath`（尸体静止帧 `start + dir`，**不**乘 `frame+skip`）。挖肉是 **Alt+左键**点 3×3 内尸体，`MakeDefaultMsg(CM_BUTCH, actorid, x, y, dir)` 带 actor id；服务端 `ObjBase.pas:17458` 要求 `m_boDeath && !m_boSkeleton && m_boAnimal`，鸡 51/鹿 52/狼 53（M2Share.pas:148-150），**动物死亡不走 ScatterBagItems**，肉由 `TakeBagItems` 给「肉」(StdMode 40)。我们 `update_sight()` 里 `a.alive` 过滤在死亡瞬间就发 `entityRemove`，尸体根本没机会显示 | 服务端：`Monster` 加 `corpse_until / butch_count / skeleton`；`kill_monster()` 对 `butchable()`（race 51/52/53 + 动物 appr 43/70/160/161，排除「护卫」）保留尸体 60 s 且**跳过 `drop_loot()`**；新增 `_sight_actors()` 让快照/视野包含未过期尸体（`nearby()` 仍只含活体，避免怪打尸体）；`brief_json()` 下发 `alive/corpse`；新增 `butch` handler（距离 ≤2、挖 2 次出肉、鸡→「鸡肉」/其他→「肉」依据本地 `MonItems`，下发 `bagUpdate` + 广播 `butch` / `entityRemove`）。前端：`ActorSprite` 加 `death` 动作段（`ActDeath.start + dir`）与 `prefetchDeathFrame()`（否则 `getLayers` 回退到站立帧，看着像「怪还站着」）；`PlayScene` 收 `death{corpse:true}` 先播 `die` 1.4 s 再切 `death`、尸体不画名字/血条、Alt+左键 3×3 找尸体会带 `actorId` 发 `butch` |
| 条纹只是变浅、重登就恢复 | 用户这句定位了真因：冷缓存（首次登录）时首屏纹理常超 6 s，`_prefetchVisible()` 超时把**仍在飞的请求**标记 `failed`；`_floorTex()` 随即删占位重发，与增量预取 `Promise.race` 8 s 超时叠加成**重复请求风暴**，整片格子长期拿不到图 → 兜底色连成横带；重登时 HTTP 缓存已热才「恢复正常」。§17 只把兜底色改亮，所以条纹变浅但还在 | 超时**只放行显示、绝不判 failed**：原 promise 完成时自行写入同一个占位；`_prefetchAround()` 把缓存写入移进各 `getTexture().then()`，`Promise.race` 只用于不阻塞后续批次、结果不再覆盖；增量预取改**行优先**（24 格一批覆盖一整行而不是一列 24 行）；`_ensureFloor()` 增加与坐标解耦的 48×32 **真实地砖像素快照** `f.sample`，整行未就绪时铺真实地砖而非纯色（滚动搬运后 `lastPainted` 坐标会失效，sample 不会） |
| 主菜单返回人物选择无效 | 旧 `_logout()` 只发 `logout`，服务端直接 `session.close()`；客户端收到 close 没有任何场景切换，点了等于没反应 | 新增 `backToSelect` 消息：服务端 `on_disconnect()` 清理世界但**保留登录态与连接**，回 `backToSelectResult` + `characterList`；前端切 `SelectCharScene`，HUD 两个退出按钮统一走回选角（浏览器里无法真正关客户端） |
| 登录失败文字位置 / 缺开门动画 | 原版 `SM_PASSWD_FAIL` 走 `FrmDlg.DMessageDlg` **居中弹窗**（`FState.pas:2407` 白字黑边、`\` 分行、行距 14），`LoginScene.PassWdFail` 只重新显示输入框并不在登录框里画字。开门在 `IntroScn.pas::TLoginScene.OpenLoginDoor`：`m_nMaxFrame=10`、**300 ms/帧**，底图 `ChrSel[22]`，门帧 `ChrSel[23+frame]`（496×361），画在 `((800-w)/2+1, (600-h)/2-23)`，音 `s_rock_door_open=100`（SoundUtil.pas），播完淡出 `ChangeScene(stSelectChr)` | 登录失败改 `_showMsgDlg()` 居中模态框（320 px 宽、白字黑边、`\` 分行、确定按钮），删掉登录框底部 `left=24,top=224` 那行错位文字；登录成功改走 `_startDoor()`：隐藏登录框、播编号 100 音效、预取 10 帧门、逐帧绘制，10 帧后 300 ms 黑幕淡出再切选角 |

### 18.2 验证（服务端/回归 `D:/Python310/python.exe`，Playwright `D:/Python39/python.exe`）

- 浏览器端到端 **15/15 PASS**：登录失败弹窗文字与居中 `(400,300)`；登录成功 0.5 s 时仍在 `LoginScene` 且 `_door.active`、`frames=10`；3.3 s 后到 `SelectCharScene`；`_miniMapIndex=101 → _miniMapTex=[1052,699]`；小地图非零像素 `14400/14400`；大地图 `40965` 个亮像素、标题「比奇省 (296, 500)」；正常帧与「整片格子人为未就绪」两种情况下兜底纯色横带数均为 `0`、`f.sample=true`；主菜单返回后 `SelectCharScene` 且 `connected=true`、角色列表仍可用；`MapActor.render` 收到 `["function", true]`；`pageerrors: []`。
- 尸体专项 **8/8 PASS**：倒地动画 → `death` 帧（鹿 `mon17/700`，与 die 帧 `mon17/360` 不同）、纹理已预取 `hasTex=true`、可绘制、无名字血条（截图目视确认侧躺鹿尸）、Alt+左键发出 `{actorId, x, y}`。
- `test_pyserver.py` **54/0**、`test_tick_systems.py` **53/0**、`test_all_messages.py --live 8080` **27/0 + ping→pong PASS**（新增 8 条：掉落环形扩散、尸体保留/不掉物品、尸体在视野、战斗不含尸体、挖两次出肉、bagUpdate、backToSelect 不断线；消息类型 **46 → 48**，`messages_for()` 与执行顺序同步补齐）、`node scripts/test_audio.mjs` **19/19**、`convert_audio.py --self-test` **12/12**。

### 18.3 边界（未硬改）

- 只给**可挖肉动物**保留尸体；其他怪死亡仍按原逻辑移除（原版所有怪都有尸体，本移植为避免 actor id 与 15 s 重生冲突先不铺开）。尸体实际存活到重生（约 15 s）而非原版 60 s。
- 挖肉次数简化为 2 次（原版 `m_nBodyLeathery` 50~150、`m_nMeatQuality` 递减，本移植无这两个字段）；未实现 `SM_BUTCH` 让别人看到蹲下（`CM_SITDOWN` 动作段本移植没有），肉品质也未写进 `Dura`。
- 尸体帧按 `ActDeath.start + dir` 取一张；原版部分怪（骷髅类、`ActDeath.frame>1`）还有多帧，未逐怪适配。
- 大地图仍是「整张 mmap 底图等比铺进 DOM 面板」的简化实现；原版 `MapUnit.DrawMiniMap`（`SCALE=4`、`MX=12/MY=8` 的 540×360 surface）是另一套遗留代码，`TAB` 实际绑定在原版是 `M` 键，未照搬。
- 开门只做淡出，未做 `g_nFadeIndex` 的选角淡入；失败弹窗用 DOM 样式近似 `g_WMainImages[360/380/381]` 底图。
- 条纹是冷缓存竞态：无头 Chromium 本地资源秒开，只能用「人为把整片纹理置为未就绪」验证 `sample` 生效；真实冷启动观感仍需用户 `Ctrl+F5` 自测。

## 19. 怪物伤害观感排查 + 目标红框对齐原版 ceBright（2026-11-11 第八轮，实测反馈）

### 19.1 反馈与结论

用户反馈：①「怪物打我似乎不掉血，但有被击中的声音和动画帧」；②「选中怪物外面一圈红色边框是调试用的么？原版没有」。

**① 结论：不存在不掉血 bug，是伤害数值太小 + 自然回血抵消造成的观感问题。**

- 服务端链路完整：`_monster_ai` → `_apply_hit_to_player`（`target.hp -= dmg`）→ `_send_hits`（广播 `attack` + 单播 `stats`）→ 死亡走 `kill_player`。3.10 离线定量复现（新建 26 HP 角色、鸡贴身）连续 6 次：`hp 26→25→24→23→22→21→20`，每次成对下发 `attack(damage=1, targetId)` 与 `stats(id, hp, maxHp)`，无丢消息。
- 伤害公式 `max(1, m.roll_attack() - p.combat.acMax)`：新手怪实测区间 **鸡 1 / 稻草人 1-2 / 鹿 2-4 / 多钩猫 2-4 / 钉耙猫 2-4 / 半兽人 4-9 / 毒蜘蛛 6-9**；新建角色 29 HP、acMax=0，一次挨打血条（40 px 宽）只掉 **1.4~5.5 px**，血球约 2~6 px。
- 前端 `case 'attack'` 只负责攻击者动作、命中帧（170 ms）、受击动作与伤害飘字；**HP 由 `stats` 单播更新**——与原版一致：本地 `MirClient/Actor.pas:1550` 的 `SM_STRUCK` 分支里 `Abil.HP := msg.x` 等三行是被注释掉的，HP 走 `HealthSpellChanged()` 单独同步。
- Playwright（3.9）端到端验证：`entities.get(self.id) === self`（引用未分离）；注入 `stats(hp-10)` 后 `self.hp 29→19`、`_hudKey` 变化、`vitalsCanvas.toDataURL()` 像素变化；注入 `attack(damage=7)` 后 `damageNumbers` +1。全链路正常。
- 放大观感的是**自然回血**：`world.py::_regen` 每 `REGEN_INTERVAL_MS=2000` 回 1 HP，而鸡攻击间隔约 1000 ms、单次仅 1 点 → 净血量几乎不动，看起来「永远满血」。原版回复由 `m_nIncHealth/m_nPerHealth/m_dwIncHealthSpellTick` 控制（`M2Server/ObjBase.pas:3780-3861`，间隔 `600 - min(400, level*10)` ms），机制不同；本轮按「无充分依据不硬改」**未改回血逻辑**，只报告。另发现 `_regen` 的 `last_regen_at` 是 world 级共享计时（多玩家会互相抑制），同样留待后续。

### 19.2 目标红框：确认是移植期自加的调试可视化，已按原版替换

- 原版依据（本地 `D:\work\me\game\传奇\MIR2\GameOfMir`）：`MirClient/Actor.pas:2044` `if (g_FocusCret = self) or (g_MagicTarget = self) then ceff := ceBright;`；`cliUtil.pas:17` 定义 `TColorEffect` 含 `ceBright`、:1141 映射到 `BrightColorLevel`；`PlayScn.pas:1281` 地面焦点物品同样用 `DrawEffect(..., ceBright)`。**原版只让 sprite 变亮，从不画矩形框**；点击锁定的 `g_TargetCret` 没有任何视觉标记。
- 改动：`frontend/map/MapActor.mjs` 删除 `strokeStyle='#ff3b3b'` + `strokeRect(anchorX+4, anchorY-70, 40, 100)`；sprite 绘制时若 `e.isFocus` 用 `ctx.filter='brightness(1.28)'` 近似 ceBright（在 `save/restore` 内，只作用于精灵，不影响血条/名字）。`frontend/scene/PlayScene.mjs` 的 `isTarget`（点击锁定 `castTarget`）改为 `isFocus`（鼠标悬停 `magicTarget`，对应 `g_FocusCret`）；`castTarget` 逻辑保留用于锁定攻击/施法，只是不再画框。
- 验证：`node --check` 两文件通过；Playwright **PASS**——`MapActor.render` 收到的实体含 `isFocus`、无 `isTarget`；设置 `magicTarget` 后 `focusCount=1`；画面 `#ff3b3b` 近似像素 19 个（HUD 贴图，远少于 40×100 框至少 80 个），`castTarget` 与悬停两态均无红框；HP 回归 29→24 正常。
- 回归：`test_pyserver.py` 54/0、`test_tick_systems.py` 53/0、`test_all_messages.py --live 8080` 27/0 + ping→pong。

### 19.3 排障工具更新

- 用户提供本地 Delphi 源码 `D:\work\me\game\传奇\MIR2\GameOfMir`（已同步，GBK 编码），项目级 skill `jootm2-original-debug` 已改为**本地优先**：新增 `scripts/pas_lookup.py`（GBK 自动转码、目录/文件过滤、上下文输出），SKILL.md 第 3 步与 `references/delphi-lookup.md` 同步更新，GitHub raw 仅作兜底。本轮两条原版依据均由该脚本秒级定位，无需联网。

## 20. 1.76 / 1.80 客户端资源差异分析（2026-11-11，只读分析）

用户在 `client_original/` 新放入 1.76 客户端，与 `resources/`（1.80 提取）做全量对比。产出 `docs/CLIENT_176_VS_180_DIFF.md`、`scripts/compare_client_versions.py`、`data/client_diff_176_180.json`；**本轮未改动任何运行代码或资源**。

- 来源校验：7-Zip 列两个 rar 清单，与磁盘三组资源目录按文件名+大小逐项核对，`rar 独有 0 / 磁盘独有 0 / 大小不符 0` → `resources/` 确为 1.80 的完整提取。
- 总体：1.76 是 1.80 的严格子集（**0 个 1.76 独有文件**）；1167→1509 文件、955.4→1558.6 MB。1.80 新增 16 组图库 / 128 张地图 / 179 个音效，改动 14 组图库 / 2 张地图 / 5 个音频；**28 组图库 + 526 张地图 + 540 个音效 MD5 完全相同**。
- 结论①（不能换）：`verify_resources.py` 判 1.76「不可用」——缺 `prguse3`（F10 装备页底图）、缺 `mon19/20/21`、`Weapon.wix` 比 `48+count*4` 短 4 B（前端 `loadIndex()` 读末项会越界），关键帧 3/4；1.80「可用」4/4。项目 `resources.json`、UI 帧号、怪物 appr→图库/叫声映射、小地图 1-based 索引均按 1.80 写死。
- 结论②（帧号）：`hum / tiles / magic2 / magicon` 是尾部追加、旧帧号兼容（`hum` 10799/10800 帧字节不变）；`npc / effect / mmap / mon18 / prguse / prguse2 / items / dnitems / stateitem` 为重排重绘，`mon18` 帧数 3707→1630（部分怪迁到 `mon19~23`），`prguse` 帧数不变但 290/510 帧尺寸改变。项目关键帧 `prguse[370]`、`mon5[2669]`、`mon18[821]` 在 1.76 仍命中，`prguse3[29]` 缺库。
- 结论③（BGM 根因）：1.80 把两组曲子**内容对调了文件名**（MD5 交叉验证）：`log-in-long2.wav` ↔ `game over2.wav`（68.6 s ↔ 34.3 s）、`main_theme.wav` ↔ `sellect-loop2.wav`（43.9 s ↔ 36.0 s），`sound.lst` 14155→18660 B。这就是 §17「intro/gameover 索引对调」的根因：Delphi 曲目编号常量没变，变的是文件内容；若改用 1.76 Wav 必须把该对调还原。
- 结论④（地图）：仅 `0.map/1.map` 改动，尺寸不变、4831/1955 字节、2040/872 个 tile，差异落在 12B tile 的 0/1/4/5/10 字节（图层索引字段）；新增 128 张多为 `em000~em063` 活动模板图，均不在 `MapInfo.txt` 登记内，两版都缺同样 71 张，覆盖率无提升。
- 用法：1.76 保留为「1.80 资源是否被私服魔改」的字节级校验基准，不作运行资源；复跑 `D:/Python310/python.exe scripts/compare_client_versions.py`。

