# jootm2 交接归档 b

> 从 HANDOFF-PYSERVER.md 归档的历史轮次（2026-11-13 重构，逐字保留、只读不改）。
> 内容：§22~§30：公网加载持久缓存 / 首屏预载 / 地面条纹收口 / 旁观者血条 / 怪物 AI 与中立怪 / 掉落倍率 / 新角色初始装备 / 尸体绘制

---

## §22 公网加载速度：持久缓存 + 启动预加载（2026-11-11 晚）

### 22.1 定量基线（改之前）

用 CDP 抓同一个浏览器上下文里两次页面加载的响应：

| | 第 1 次（冷） | 第 2 次（应命中） |
|---|---|---|
| `.mjs` 模块 | 23 个 **200 全量** | 23 个 **200 全量**（`no-cache` 且服务端没有 ETag，无法 304） |
| WIL 分片（206） | 3 个 | **3 个，0 命中** |
| WIX 索引（200） | 2 个 | 2 个全部命中磁盘缓存 |

**根因**：WIL 纹理走 HTTP Range → 206 Partial Content，**Chromium 不把 206 写进 HTTP 缓存**，所以服务端给的 `Cache-Control: immutable, max-age=31536000` 对分片完全无效。加上 `.mjs/.json` 是 `no-cache` 且无校验器，每次都全量回源。进游戏后有 120+ 个 512KB 分片，公网（ECS 带宽 + RTT）下就是几十秒。

### 22.2 三层改动

**① 服务端 HTTP（`pyserver/net.py`）**
- 所有静态文件加 `ETag`（`size-mtime_ns`）+ `Last-Modified`；`If-None-Match` / `If-Modified-Since` → **304**；支持 **HEAD**。
- `cache_control()` 细分：`.html` = `no-cache`；`.mjs/.js/.css/.json` = `public, max-age=300, must-revalidate`；素材 = `immutable` 一年。开发期改代码用 Ctrl+F5 强刷即可。
- 文本/JSON/`.map` 走进程内 **gzip 预压缩缓存**（`GzipCache`，`gzip.compress(level=1, mtime=0)`，上限 64MB，`Vary: Accept-Encoding`）。实测 `main.mjs` 4635B → 2390B，`resources.json` 这类索引省得更多。
- **Range 206 绝不压缩**：`Content-Range` 描述的是真实文件字节偏移，压缩会破坏 WIL 解包。
- 新接口 `GET /api/asset-version`：对 `resources_root` 下所有 `.wil/.wix/.map` 的 `(相对路径, size, mtime_ns)` 求 sha1 前 16 位，进程内缓存 60s。**故意排除 `resources.json` / `audio_map.json`**（每次启动 `generate_resources_index` 会重写它们，纳入会导致一重启就全量失效）。实测服务重启前后版本都是 `5246e4b3c930fe8b`。

**② 前端持久缓存（新增 `frontend/gfx/assetCache.mjs`）**
- IndexedDB `jootm2-assets` / `chunks` store，按 `assetVersion` 做命名空间；版本变化自动清空。
- 接入三处：`scheduler.mjs` 的 Range 分片（缓存 `{buf,total}`，`total` 必须存，WIL 末张图位深判定依赖它）、`wil.mjs` 的 WIX 索引、`Maps.mjs` 的 `.map` 字节。
- 两级结构：同步内存 Map（同会话复用，24MB 上限）+ IndexedDB（384MB 上限，按 `byTs` 索引淘汰到 80%）；写队列 150ms / 16 条批量事务，`pagehide` 兜底 flush。
- **任何异常（隐私模式 / 配额 / IDB 被禁）静默降级**，绝不阻塞启动。
- 调试：`window.__assetCache.stats()` / `await window.__assetCache.clear()` / `window.__scheduler.stats()`。

**③ 启动 Loading + 分阶段预加载（新增 `frontend/core/boot.mjs`、`index.html` 内联层、`main.mjs`）**
- `index.html` 内联 `#boot-layer`（HTML 一到就显示，JS 没下载完也不黑屏），真实进度条 + 状态文字，完成后 450ms 淡出。
- 阻塞阶段：`/api/asset-version` ∥ `resources.json` → `assetCache.init()` → WS 连接 ∥ 音频索引 → **登录首屏必需纹理** → flush → 淡出。
- 后台阶段（登录界面出现 400ms 后，低优先级）：① 开门/选角/立绘纹理（并发 8）→ ② 全部 58 个 WIX 索引（合计仅 **0.98MB**，并发 2）→ ③ `0.map` 字节（5.88MB，**只暖字节不解析**，700×700 tile 对象解析会卡主线程）→ flush。
- **关键取舍**：开门动画 `ChrSel[23..32]` 是 10 张 800×600 大图，每张独占一个 512KB 分片。一开始把它们放进阻塞阶段，模拟公网冷启动要下 8 个分片 / 9.27s；挪到后台后降到 6 个 / **6.17s**，而登录成功后才需要这些帧（输账号的几秒足够预热）。
- 开关：`localStorage['jootm2.preload'] = '0'` 关闭后台预热；`window.__warmDone` 可等待后台完成。

### 22.3 验证（全绿）

| 项目 | 结果 |
|---|---|
| 二次打开（同一浏览器，等后台预热完成） | 资源响应 99 → 28；**206 12 → 0**；网络分片 12 → 0；IndexedDB 命中 71 次，省 13.6MB；首屏 1.24s → 0.64s |
| 两轮完整「登录→选角→公告→进游戏」 | 首次进游戏 125 个网络分片 → **二次 0 个**（命中 128）；地图 `0` 700×700 就绪；**0 个 JS 错误** |
| 模拟公网 100ms RTT / 4Mbps | 冷启动 **8.95s → 6.17s**（调整预加载后），缓存命中 **1.00s**，206 12 → 0，提速 **89%** |
| HTTP 层 | `/api/asset-version` 200 `no-store`；`main.mjs` gzip + ETag + 304 + HEAD 同长度；206 无 `Content-Encoding`；`index.html` `no-cache` |
| 回归 | `test_pyserver` **67/0**（新增 12 条缓存用例）、`test_tick_systems` 53/0、`test_all_messages --live 8081` 27/0 + ping→pong |
| 版本稳定性 | 杀掉并重开 8081 服务后 `/api/asset-version` 不变 |

### 22.4 部署与已知边界

- **必须重新构建镜像**：`Dockerfile` 把 `frontend/` 和 `pyserver/` 都 `COPY` 进镜像，8080 上跑的是 Docker 容器（本机 8080 端口属 `com.docker.backend.exe` 转发），改代码后要按 §21.6 重新 `docker build` → 推 ACR → 服务器 `docker pull` → `docker compose up -d`，仅刷新浏览器无效。
- Java/Netty 版没有 `/api/asset-version`，前端自动回退 `version='default'`，IndexedDB 缓存照常工作但**不会随资源更新自动失效**，需 `await __assetCache.clear()`。
- 素材版本含 `mtime_ns`：用不保留时间戳的方式重传资源（如不带 `-a` 的 rsync）会让版本变化、缓存重下一次；属预期行为。
- 音频 MP3 / BGM 仍靠浏览器 HTTP 缓存（`immutable`），未纳入 IndexedDB；首次播放仍会产生一次下载。
- 缓存只覆盖 `/resources/` 的不可变素材，不含任何账号/角色/游戏状态；`window.__assetCache.clear()` 可随时清空。
- 未做：206 分片压缩（需自定义非 Range 端点绕开 `Content-Range` 语义，风险高）、HTTP/2、Service Worker（`http://` 非安全上下文不可用，且 IndexedDB 已够用）。§23 已用「应用层协商头」把 206 分片压缩做掉，无需新端点。

---

## §23 进游戏后的「正在加载地图资源」：首屏纹理预载 + 206 分片压缩（2026-11-11 深夜）

用户反馈：§22 之后登录/首屏明显变快，但**进游戏后仍长时间显示「正在加载地图资源 x/y」**。公网实测地址按 §21.7 用 IP `http://139.224.24.88:8080/`。

### 23.1 定量诊断（连真实公网 + 离线复算）

| 观测 | 数据 |
|---|---|
| `MapActor.enter()` 分解 | `prefetch` 每次都精确 **6003/6008ms** —— 即必然命中 6 秒超时兜底；`loadMap()` 另占 6~14s（现下 `0.map`） |
| 超时瞬间的调度器 | `active=20 / queue=79`、`deduped=259`，即放行时仍有近百个请求在排队，地图出来后继续一块块冒 |
| 首屏帧集合（离线按 `0.map` @ (289,618) 复算） | 23×49 格 → **244 个不同纹理帧** → 约 **16 个 512KB 分片 / 7.7MB**（`objects` 5.6MB、`tiles` 1.5MB、`smtiles` 0.09MB） |
| 冷启动两次进游戏 | 第 1 次 9.6s、第 2 次 10.4s，仍各下 10 / 4 个 206 分片；`assetCache.hits` 几乎不涨是因为超时前没下完的分片根本没写进缓存 |
| 公网分片速度 | 单个 512KB WIL 分片 **4~16 秒**；`Prguse/Prguse3` 的 HUD/开门帧与 `Objects` 分片互相抢 6 条 HTTP/1.1 连接 |
| WIL 分片 gzip 率（实测样本） | `Tiles` 2.07x、`SmTiles` 1.94x、`Objects` 1.66x、`Prguse` 1.97x、level 1 已接近 level 6 |

**结论**：不是「缓存没生效」，而是这 7.7MB 纹理 + 5.9MB `.map` 从来没在进游戏之前开始下载；§22 的后台预热只做了 WIX 索引和 `0.map` 字节，且排在开门帧之后，公网窗口期根本轮不到。

### 23.2 改动

**服务端**
- `/api/asset-version` 响应增加 `start: {map,x,y,miniMapIndex}`（取 `GameWorld.default_map/start_x/start_y/map_mini_index`），前端 Loading 阶段就知道该暖哪张图。无 `world` 时降级为空对象。
- `characterList` 每个角色附带 `map/x/y`（角色上次下线位置），选角界面可精确预载；老前端忽略新字段，协议兼容。
- **206 分片应用层协商压缩**：前端带 `X-Jootm2-Gzip: 1` 时，服务端把该 Range 的原始字节 `gzip(level=1)` 放进 body，返回 `X-Jootm2-Gzip: 1` + `Content-Length`（压缩后），`Content-Range` 仍描述**解压后**的真实文件偏移。**故意不用 `Content-Encoding`**，避开 206 + `Content-Range` 在代理上的语义歧义（§22「206 绝不 gzip」的旧结论由此被取代，但只限这条自定义协商路径）。仅 `.wil/.wix/.map` 且分片 ≤2MB、压缩率 >10% 才启用；不带头的客户端原样返回。

**前端**
- `MapActor.mjs` 导出 `visibleRange()` / `collectVisibleTextures()`；`_prefetchVisible` 与新增预载模块**共用同一个帧集合函数**，杜绝「预载了一堆用不上的帧」。
- 新增 `frontend/core/mapPreload.mjs`：`preloadMapTextures(map,x,y)` 无超时全量预取首屏纹理 + HUD 常驻帧（`Prguse[1,4..15,30..39,128..142,370,371]`、`Prguse3[29,30,32,33,207]`）+ `mmap[index-1]`；并发 6；同 key 复用、不同 key 让位（精确坐标优先于默认出生点）；`preloadState` / `waitIdle()` / 进度回调；`localStorage['jootm2.preload']='0'` 可关。
- `boot.mjs`：`warmMapBytes(mapId)` 在 Loading 阶段**不 await** 地发起 `.map` 下载（gzip 后约 600KB，进游戏时 `loadMap` 直接命中 IDB）；后台顺序改为 开门/选角帧 → 等 `.map` 字节 → **首屏地图纹理** → 全量 WIX → flush。
- `SelectCharScene.mjs`：`characterList` 到达 / 切换角色时按真实坐标触发预载（旧服务端无坐标则用 `start` 兜底）；点「开始」时若预载已过半，最多再等 5 秒并显示 `正在载入地图资源 x/y`，**进度不足一半就放行**（慢网首开白等无益）。
- `scheduler.mjs`：发 `X-Jootm2-Gzip: 1`（有 `DecompressionStream` 时），收到响应头才用 `DecompressionStream('gzip')` 解压；`assetCache` 仍缓存解压后的原始字节，缓存 key 不变。**旧服务端不回该头 → 自动走未压缩路径**，前后端可分批部署。

### 23.3 验证

| 场景 | 结果 |
|---|---|
| 本地 3.10 直连，新前端 + 新服务端 | 预载 **244/244 完成**、纹理就绪 261/318、`gzip` 分片 61、**进游戏→地图就绪 0.03s**（原 6s+）、0 JS 错误 |
| 模拟慢网 500KB/s + 80ms RTT，gzip 关 / 开 | 预载进度 48/244 → **176/244**；首屏纹理就绪 158/318 → **234/318**；线上 206 字节 16.42MB → 14.82MB |
| 真实公网 A/B（旧服务端，用 `page.route` 注入新前端模块） | 进游戏→地图就绪 **24.33s → 6.01s**，点开始→地图就绪 24.78s → **11.34s**，206 分片 27→19、13.4MB→9.7MB，0 JS 错误（旧服务端仍触发 6s 超时兜底，因其无 gzip 与角色坐标） |
| 回归 | `test_pyserver` **72/0**（新增 5 条：`start` 字段、降级、206 协商压缩解压一致性等）、`test_tick_systems` 53/0、`test_all_messages` 离线 28/0、live 8081 **28/0 + ping→pong** |

### 23.4 部署与边界

- **必须重新 build 镜像**（§21.7 / §22.4）。本轮公网 A/B 只注入了前端模块，服务端 `start`/`characterList`/206 gzip **尚未上线**；重新部署后预载会再快约 2 倍，本地直连已验证可做到进游戏零等待。
- 旧服务端完全兼容：无 `start` → 后台暖 `0.map` 字节；`characterList` 无坐标 → 用 `start` 兜底；不识别 `X-Jootm2-Gzip` → 原样返回 206。
- 未做 / 保留边界：`changeMap` 换到新地图仍会现下新图的纹理（服务端可在接近传送点时预告，待做）；`_prefetchVisible` 的 6 秒超时兜底保留（防极端慢网卡死）；动画对象的全帧预取、WIX 全量索引仍可能与其他请求抢 6 条 HTTP/1.1 连接（HTTP/2 / 多域名未做）；音频 MP3 仍只靠浏览器 HTTP 缓存。
- 新增调试接口：`window.__mapPreload`（预载进度）、`await window.__warmDone`、`__scheduler.stats().gzip/gzipSaved`。

---

## §24 选角「退出」回登录 + 地面条纹收口 + 卡顿定量定位（2026-11-12）

用户反馈三件事：① 选角界面点「退出」不切回登录界面（但能听见登录 BGM）② 灰色条纹仍在（返回角色再进就消失）③ 本地 `127.0.0.1` 也明显卡顿，「记得之前没有」，排除网络。

### 24.1 「退出」= 关客户端 → 浏览器改为回登录

原版依据：`IntroScn.pas:1237 SelChrExitClick` 只有一句 `FrmMain.Close`（直接关掉客户端）；`FrmDlg.DscSelect1Click` 把 `DscExit` 派发给它。浏览器不能关闭非脚本打开的标签页，因此改为「断开连接 → 新建连接 → 切回登录界面」，与用户预期一致。

- `SoundManager.install(conn)` 拆成 `attach(conn)`（每条连接一次：挂 `'*'` 路由与 `onClose`，重置 `selfId/prevGold/prevHp`）+ `install(conn)`（一次性全局监听 + 首次 `attach`）。旧代码 `if (this.installed) return this` 会让新连接收不到任何消息。
- `SceneManager` 新增 `bind(conn)` 与方法位 `onReturnLogin`（由 `main.mjs` 注入）。连接工厂留在 `main.mjs`，避免 `LoginScene ↔ SelectCharScene` 循环依赖。
- `SelectCharScene._exit()`：调 `manager.onReturnLogin()`，无注入时退回 `conn.close()`。`main.mjs` 里的实现：`conn.close()` → `new Connection('/ws')` → `sound.attach` → `sceneManager.bind` → `await connect()` → `switchTo(new LoginScene(...))`。

### 24.2 灰色条纹：地面齐备才放行 + 多样本兜底

条纹的唯一来源是「地面层格子没画真实地砖，而是用兜底填充」。三处收口：

1. `MapActor._prefetchVisible` 拆两段：**地面层（tiles bng + smtiles mid）必须等齐**（硬上限 15s，进度回传），对象/动画帧尽力而为（6s）。地面缺一块就是一条横带，对象晚到只是单个贴图冒出来。
2. 新增导出 `collectFloorTextures()`（地面子集），与 `collectVisibleTextures()` 共用 `visibleRange()`；`_prefetchVisible` 据此分批等待。
3. 兜底借像素由「单个 `f.sample` 快照」改成 **24 格样本池**（`f.samples` + 按世界格坐标散列取样本）。单个快照会把同一格地砖重复铺满整行——那正是用户截图里一条条横纹的观感；样本池铺出来是多种地砖交错。
4. 超时**仍然绝不把在飞请求标 failed**（那会删占位重发，形成请求风暴）。

### 24.3 卡顿根因（定量，不是网络）

CDP 采样 + `rAF`/`setTimeout` 心跳双测把范围缩到两处**每帧同步工作**：

| 现象 | 定位 | 量化 |
|---|---|---|
| 未就绪格子**每帧**重新借像素重绘 | `_paintFloorCells` 里 `cells` 只有 0/1 两态，借用后不记账，下一帧又借一遍：整屏 200+ 格 × 每格最多 4 次「1760×957 画布自拷贝」 | 200KB/s 冷进图：rAF p50 **650ms**、11 帧里 10 帧 >33ms |
| 每个 512KB 分片到达就**同步解包块内全部图** | `WIL._unpackRange` 遍历 `imageCount` 逐张建 canvas + `putImageData`；20 并发分片同时到达就是几十~几百毫秒的主线程停顿 | 实测单分片 `mon19` 363 张 14.1ms、`mon20` 88 张 10.7ms；×20 并发 ≈ 280ms |

修法：

- `_paintFloorCells`：`cells` 改三态 **0=未画 / 1=真实地砖 / 2=已兜底画过**，借用后打 2；只有纹理就绪时才升级为 1。维护 `f.open`（未画真实地砖的格数），`open===0` 时整函数直接 return。
- `WIL`：删掉 `_unpackRange`/空闲队列，**只解当前请求的那一张**（单张 0.1~1ms）。分片字节本来就在 `assetCache`（内存 24MB → IndexedDB 384MB），相邻图再次请求零成本。偏移表改为显式排序的 `byOff` 再派生 `sortedOffsets`（不假设 WIX 偏移有序）。

修后同一场景（200KB/s、20 个请求在飞 + 136 排队）：rAF p50 **16.7ms**、6 秒 361 帧、**0 帧 >33ms**。

### 24.4 验证

| 场景 | 结果 |
|---|---|
| 200KB/s + RTT60ms 冷进图（选角立刻点开始） | 未画格 0、兜底横带行 0、重复行 0、`textures 78 就绪 / 0 失败 / 157 在飞` 也能出完整地面 |
| 同上帧率 | 限速 p50 16.7ms（0 帧 >33ms）；解除限速 p50 16.7ms |
| 真实 GPU 窗口（headed，144Hz 屏） | 选角/进图/加载中 rAF p50 13.9ms、无 long task |
| 「退出」流程 | `LoginScene=True`、新连接 `readyState=1`、用新连接重新登录 → 选角成功 |
| 回归 | `test_pyserver` **72/0**、`test_tick_systems` **53/0**、`test_all_messages --live 8081` **28/0** + `ping→pong` |
| 服务端 tick | 离线 300 次 `service.tick()`：p50 **1.08ms** / p95 3.64ms / max 8.09ms（空世界，怪物按地图懒生成） |

### 24.5 边界

- 地面 15 秒硬上限仍在：公网极慢时宁可多等地面，也不放行半张图（用户明确抱怨的是条纹）。此时加载界面显示「正在加载地图资源 n/m」，`PlayScene` 会把进度打在左上角。
- 首帧若地面纹理全未到（极端），仍是 24 格样本池拼出的「假地面」——观感是地砖花纹不对但不出现色带；纹理到达后逐格替换为真图。
- `assetCache` 内存缓存 24MB 会淘汰；淘汰后相邻图请求要走一次 IndexedDB 反序列化（仍不走网络）。
- 未做：把 WIL 解包搬进 Worker（`createImageBitmap` + transfer）；HTTP/2；`.map` 解析仍在主线程（首进图 100~300ms 一次性开销）。

---

## §25 多人旁观者血条不减少（2026-11-12）

用户反馈：多人时别人被怪打、身上确实在播受击动作，但我这边看到他的血条不动。

### 25.1 原因

`World.send_stats()` 对 `Player` 只发给**本人 session**（`:817 if isinstance(a, Player) and a.session is not None: a.session.send_json(o)`），怪物才广播给周围。而原版 `Actor.pas:1550` 里 `SM_STRUCK` 的 HP 字段是被注释掉的，自己那份走 `SM_HEALTHSPELLCHANGED`——于是在我们的实现里，**旁观者永远拿不到别人的血量变化**，`entityAdd` 时那份 hp 就定了终身。

### 25.2 改动

- 服务端 `world.py` 新增 `HP_SYNC_MS = 400` 与 `_sync_player_hp(now)`，在 `tick()` 里 `_tick_pending_ranged_hits` 之后调用：按 `_hp_sync_cache[id] = (hp, max_hp)` 比对，变化就向周围 `broadcast_around` 一条 `{type:'entityHp', id, hp, maxHp}`；**只带血量**（不带 ac/dc/exp，避免泄露他人面板），按 400ms 合批，无变化不重发，玩家下线时清缓存。
- 客户端 `PlayScene.onMessage` 新增 `case 'entityHp'`：更新对应实体的 `hp/maxHp`（`msg.id === self.id` 时忽略，自己以 `stats` 为准）。
- 回归 `scripts/test_tick_systems.py` 新增 `[10] 旁观者血条同步`（4 条）：广播内容与字段集合、无变化不重发、`HP_SYNC_MS` 内合批。

### 25.3 验证

`test_tick_systems` **57/0**（原 53 + 新 4）、`test_pyserver` 72/0、`test_all_messages --live 8081` 28/0 + `ping→pong`。

### 25.4 边界

- 血条最大延迟 400ms（一个 tick 周期的整数倍），原版同样不是逐帧同步。
- 别人的 MP / 经验 / 面板仍不同步（原版也不给）。宠物/召唤物的血量仍走怪物 `stats` 广播，不受影响。

---

## §26 鸡/鹿不主动攻击 + 怪物伤害的 0 门槛（2026-11-12）

用户反馈：鸡、鹿这些不该主动攻击人，现在会；而且会在城里咬人。

### 26.1 原版依据（本地源码）

- `M2Server/M2Share.pas:148-150`：`ANIMAL_CHICKEN = 51`、`ANIMAL_DEER = 52`、`ANIMAL_WOLF = 53`。
- `M2Server/UsrEngn.pas` 的建怪派发：`ANIMAL_CHICKEN` → `TMonster` + `m_boAnimal:=True`；`ANIMAL_DEER` → 1/30 概率 `TChickenDeer`（只会 `m_boRunAwayMode:=True` 逃跑，`m_nViewRange:=5`），否则 `TMonster`；`ANIMAL_WOLF` → `TATMonster`（会攻击）。
- 鸡/鹿在原版服务端表里 DC 为 0，`ObjBase.pas:22470 StruckDamage` 开头就是 `if nDamage <= 0 then Exit`；`TSpitSpider.SpitAttack` 同样是 `if nDamage <= 0 then Exit` 才发飞矢。所以它们**造不成任何伤害**。

### 26.2 改动

- `util.py` 新增 `PASSIVE_ANIMAL_RACES = frozenset({51, 52})`（鸡/鹿；**不含 53 狼**）。
- `world._resolve_target`：race 命中该集合直接返回 `None`（不锁玩家，只自己晃悠）——原版它们即使锁定也打不动，这里省掉「鸡追着人咬」的观感。
- `world._ranged_damage` 去掉 `max(1, …)`，返回原始 `DC 掷点 − acMax`，可能 <= 0：
  - 近战：`dmg <= 0` 时仍广播 `attack`（原版 RM_HIT 照发，有挥击动作）但带 `noDamage: true`，不结算、不飘字；
  - 远程：`dmg <= 0` 直接 `return`（原版 `SpitAttack` 不发飞矢）。
- 客户端 `PlayScene` 的 `attack` 分支：`msg.noDamage` 时只播攻击者挥击，目标不播受击、不出伤害数字。

### 26.3 验证

`test_tick_systems` **61/0**（新增 4 条：鸡 51 / 鹿 52 不锁玩家、狼 53 仍锁玩家、DC 0 怪对 AC5 玩家 `_ranged_damage <= 0`）、`test_pyserver` 72/0。

### 26.4 边界

- 鹿的「1/30 概率只会逃跑」这一分支没有单独建模：现在 51/52 一律不锁定玩家，行为上等价于「无害」。
- 城里为什么会有怪：`MonGen` 刷怪点若落在城墙内，仍会刷出来（本项目的刷怪表来自原版 `Mongen.txt`，比奇省的部分刷怪点靠近城门）。目前只保证它们不会主动咬人，没有做「城内禁刷/禁攻」的额外规则（原版靠刷怪表本身避开城内）。

---

## §27 主动/被动怪的原版控制方式与「中立怪」补全（2026-11-12）

用户问三件事：鸡鹿不主动攻击是写死的还是数据库字段控制？打它会不会还击？还有哪些同类中立怪？

### 27.1 原版机制：由 Monster.DB 的 Race 字段决定，再派发成不同类

`M2Server/UsrEngn.pas:1819 AddBaseObject` 是本项目唯一的建怪入口，内部是 `case nMonRace of`：

| race | 常量 | 实例类 | 行为 |
|---|---|---|---|
| 11 / 12 / 20 / 112 | SUPREGUARD / PETSUPREGUARD / ARCHER_POLICE / RC_ARCHERGUARD | 守卫系 | 只打红名或打过自己的人 |
| 51 | ANIMAL_CHICKEN 鸡 | `TMonster` + `m_boAnimal` | **中立**：不主动，被打还击 |
| 52 | ANIMAL_DEER 鹿 | 29/30 `TMonster`；1/30 `TChickenDeer` | 中立；少数只会逃跑 |
| 53 | ANIMAL_WOLF 狼 | `TATMonster` | 主动 |
| 55 | TRAINER 练功师 | `TTrainer`（`ObjNpc.pas`，NPC 类） | 不攻击 |
| 80 | MONSTER_OMA 奥玛 | `TMonster` | **中立** |
| 81~ | 各类 | `TATMonster` 及其派生 | 主动 |

行为差异的代码位置：

- `ObjMon.pas:415 TMonster.Run` —— **完全没有 `SearchTarget` 调用**，只有一个分支 `if m_TargetCret = nil then Wondering()`（20% 概率转向、5% 概率走一步）。所以 `TMonster` 不会自己找人打。
- `ObjMon.pas:610 TATMonster.Run` —— 每 1.5~3 秒（`m_dwSearchTime = Random(1500)+1500`）调一次 `SearchTarget`，`m_TargetCret = nil` 时每 1 秒就会找一次。
- `ObjBase.pas:2795 TAnimalObject.Struck(hiter)` —— 挨打时 `if IsProperTarget(hiter) then SetTargetCreat(hiter)`。**这就是中立怪还击的唯一入口**。
- `ObjBase.pas:17476` —— 死亡掉落里 `if (m_btRaceServer >= RC_ANIMAL)` 才 `ScatterGolds`；`RC_ANIMAL=50`（`Grobal2.pas:1086`）。
- `m_boAnimal` **不代表被动**：它只管挖肉的肉品质与尸体处理。蝎子（`TScorpion`）、毒蜘蛛（`TSpitSpider`）都是 `m_boAnimal := True` 的主动怪。

本项目 `gamedata/mud2/DB/Monster.csv` 里 race=52 的并不只有鹿：还有「羊」「护卫」「鹿10/20/30」；race=51 只有「鸡」；race=80 一个都没有。所以中立名单按 race 走天然包含羊。

### 27.2 改动

- `util.py`：`PASSIVE_ANIMAL_RACES = {51, 52}` → `NEUTRAL_RACES = {51, 52, 80}`（语义从「不攻击」纠正为「中立：不主动、被打还击」）。
- `util.GUARD_RACES` 补 `11`（卫士）/`12`（宠物卫士）——之前漏了，导致比奇城的卫士对白名玩家也主动开打。
- `world._resolve_target`：中立怪**不再直接 return None**，而是只沿用 `m.target_id` 里已有的目标（存活、同图、未隐身、距离 ≤ AGGRO_RANGE），不搜索新目标；目标跑远/死亡/隐身即放弃，回到闲逛。
- `handlers_combat._hate(m, p)`：命中时同时写 `last_hiter_id` 与 `target_id`（对齐 `Struck → SetTargetCreat`）。近战、长Hit、魔法、范围伤害四条路径统一走它；**未命中不记仇**（原版 `StruckDamage` 只在真掉血时调用）。
- 回归 `test_tick_systems` 新增 3 条：鸡被打会还击、中立怪目标跑出仇恨范围后放弃、命中记仇同时写两个字段 → **64 PASS / 0 FAIL**。

### 27.3 边界

- 原版鹿「1/30 概率生成只会逃跑的 `TChickenDeer`」仍未建模，现在鹿一律是「中立还击」。
- 中立怪还击没有「打一会儿就放弃」的计时器，只有距离/死亡/隐身三种放弃条件；原版 `TMonster.Think` 每 3 秒用 `IsProperTarget` 复查，效果等价。
- 练功师 race=55 在原版是 `TTrainer`（NPC 类，不参与战斗）；项目把它当普通怪处理，本轮未动。

---

## §28 金币/物品掉落改成集中倍率表（2026-11-12）

用户要求：爆率别改单条，做成一张表按倍数调整。

### 28.1 原版数据保持不动

`gamedata/Mir200/Envir/MonItems/<怪物>.txt` 每行 `n/m 物品名 [数量]`，命中概率 `n/m`
（257 个文件、199 个里面带金币条目，例如 `1/4 金币 300`）。这份数据就是「原版爆率」，
本轮**一个字节都没改**。

### 28.2 新增 config/drop_rates.json

```json
{ "gold": 1.0, "item": 1.0, "goldAmount": 1.0,
  "monsters": { "鹿": {"gold": 2.0, "item": 2.0, "goldAmount": 1.5} },
  "items":    { "战神油": 0.5 } }
```

- `gold` / `item`：金币条目 / 其它物品条目的**概率倍率**；`goldAmount`：金币数量倍率。
- `monsters` 按怪物名覆盖，`items` 按物品名覆盖，两者都与全局**相乘**，概率封顶 100%。
- 倍率 `0` = 该类不掉；文件缺失/字段非法一律按 `1.0`，不影响启动。
- 判定：`p = rate * n / m`，`random() < p` 才掉。金币数量 `(count/2 + rand*count) * goldAmount`，最少 1。

实现：`pyserver/drop_rates.py`（`DropRates.probability/amount/hit` + `load_drop_rates`），
`world_extra.init_extra` 读 `config/drop_rates.json`，`drop_loot` 用 `rates.hit(...)` 替换原来的
`sel_point < int(random*max_point)`。Dockerfile 本来就 `COPY config/`，随镜像生效。

### 28.3 验证

`test_tick_systems` 新增 [11] 共 6 条：默认倍率等价原版、怪物覆盖相乘、物品覆盖相乘、
数量倍率覆盖、倍率 0 永不命中、倍率极大封顶必中，外加端到端 2 条（倍率 0 不产生掉落、
默认倍率下 `1/1` 条目必掉）→ **72 PASS / 0 FAIL**。

### 28.4 边界

- 倍率按**怪物模板名**匹配（`Monster.csv` 的 `Name`），不是刷怪名后缀（`鸡`/`鸡0` 视作不同条目）。
- 只影响怪物死亡掉落；商店价格、挖肉产出、任务奖励不走这张表。

---

## §29 新角色初始装备与服装店早期商品（2026-11-12）

用户反馈「人物不应该上来就光着身子」，并问服装店早期卖什么。

### 29.1 原版依据：新角色发 4 件东西

`ObjBase.pas:16400` 的 `if m_boNewHuman then` 分支，用 `!Setup.txt` 的
`Names/` 段名字发物品（默认值在 `M2Share.pas:1615-1619`）：

| 配置键 | 默认值 | 去向 |
|---|---|---|
| `Candle` | 蜡烛 | 背包 |
| `BasicDrug` | 金创药(小量) | 背包 |
| `WoodenSword` | 木剑 | 背包（本实现落到武器槽） |
| `ClothsMan` / `ClothsWoman` | 布衣(男) / 布衣(女) | 背包（本实现落到衣服槽） |

原版只塞背包，玩家自己穿。项目原来的建角代码按 **Idx 0/1/2/3** 发东西，而
`StdItems.csv` 里这四行是「命运之书 / 金创药(小量) / 魔法药(小量) / 肉」——
所以新角色背包里是一本书两块肉，没有衣服也没有武器，这就是「光身子」的直接原因。

### 29.2 改动

- `service._create_character`：改成**按名字**取 `蜡烛` / `金创药(小量)` / `木剑` /
  `布衣(男|女)`（按 `sex` 选），不再依赖 Idx。布衣写 `slot=U_DRESS(0)`、木剑写
  `slot=U_WEAPON(1)`，登录时 `Player.distribute_items` 会直接落到装备槽。
  与严格原版的差异只有一处：**布衣和木剑是穿上而不是留在背包**（用户明确要求不要光身子）。
- `gamedata/Mir200/Envir/Market_Def/3Tdr-0.txt`（比奇省「白家服装老板」）的 `[goods]`
  补 `布衣(男) 10 10` / `布衣(女) 10 10` 两行。该脚本原本**没有布衣**，最便宜的是
  轻型盔甲（11 级 / 3000 金币），1 级新人根本买不到能穿的东西。补的两条按
  `StdItems.Price=400 × 脚本 %100` 定价，与同版本 GM 服装店脚本 `gmyf1-8039.txt`
  的商品清单一致。文件保持 GBK + CRLF。

### 29.3 验证

- 临时脚本（已删）在数据库副本上真实走 `_create_character`，落库结果：
  `slot -1 蜡烛`、`slot -1 金创药(小量)`、`slot 1 木剑`、`slot 0 布衣(男)`，等级 1 / HP 26。
- `test_pyserver` 新增「比奇服装店卖 1 级布衣」→ **73 PASS / 0 FAIL**。

### 29.4 边界

- `蜡烛` 放背包（原版同）；玩家要自己装到右手槽才会点亮/消耗，未做自动装备。
- 只补了比奇这一家服装店；盟重/其他城的服装店脚本未动（它们本来就卖轻盔以上）。
- 新角色金币仍为 0（原版同），所以「买布衣」是给 400 金币之后的事。

---

## §30 尸体绘制层级与挖肉动作（2026-11-12）

用户反馈两条：鹿/鸡的尸体画在人物**之上**；挖肉时角色**没有动作**。

### 30.1 尸体层级：原版把死亡 actor 插到「第一个活人之前」

`PlayScn.pas:2268 ActorDied`：

```pascal
for i := 0 to m_ActorList.Count-1 do
  if not TActor(m_ActorList[i]).m_boDeath then begin
    m_ActorList.Insert(i, actor);   // 插到最前面那个「活着的」之前
    flag := TRUE; break;
  end;
if not flag then m_ActorList.Add(actor);
```

行绘制是 `for k := 0 to m_ActorList.Count-1`（`PlayScn.pas:1300`），后画的盖前面。
所以**尸体永远排在最前、先画、压在活人/活怪下面**。我们的实现里实体就是
`this.entities` 的插入顺序（玩家先登录、怪物后进视野），于是后加入的尸体画在最上面。

修法：`PlayScene` 构建帧快照时把 `_corpse || _action === 'die'` 的实体收集到单独数组，
`entities.unshift(...corpses)`——与 `ActorDied` 语义一致（尸体全球排最前，跨行时仍按 y 分行）。

### 30.2 挖肉动作：原版是「蹲下」，不是攻击

- 点击尸体：`ClMain.pas:2840` 发完 `CM_BUTCH` 后**本地**再 `SendMsg(CM_SITDOWN)`，
  不等服务端回包就播蹲下动作。
- 旁观者：`ClMain.pas:4649 SM_BUTCH` 分支把它**转成 `SM_SITDOWN`** 发给该 actor。
- 帧参数：`Actor.pas:89 HA.ActSitdown:(start: 456; frame: 2; skip: 0; ftime: 300)`。
  注意 `skip=0`——每个方向只占 2 帧，方向偏移是 `dir*2`，不是 `dir*(frame+skip)=dir*8`。

我们的 `case 'butch'` 原来是空的（注释写着「本移植没有 sitdown 段」），所以点了没反应。

改动：

- `actorData.mjs`：HA 增加 `sitdown: { start: 456, frame: 2, skip: 0, ftime: 300 }`。
- `ActorSprite.mjs`：`PLAYER_SEG.sitdown = 'sitdown'`；一次性动作（播完回 stand）的判断加上 sitdown。
- `PlayScene.mjs`：Alt+左键点中尸体时本地转向 + 播 `sitdown`；收到 `butch` 时给 `msg.actorId`
  那个实体播 `sitdown`；`SITDOWN_ANIM_MS = 600` 后回 `stand`。
- `service._butch`：广播的 `butch` 消息补 `actorId` / `dir`，并把挖肉者 `p.dir` 转向尸体
  （`direction_toward`），否则旁观者只能看到原地蹲下。

### 30.3 验证

| 项 | 结果 |
|---|---|
| 帧号计算（node 直读 actorData.mjs） | dir0=456、dir3=462、dir7=470，count=2/ftime=300，均 < HUMANFRAME 600 |
| Playwright 进游戏注入 `butch` | 怪物 `_action: stand → sitdown → stand`，`dir` 被设为目标方向 |
| 同一场景里的自己 | `sitdown → stand`（600ms 回位） |
| 控制台 | 0 条 error / pageerror |
| 尸体与人物同格截图 | 玩家完整可见、未被尸体遮挡（尸体先画） |
| 回归 | `test_pyserver` 73/0、`test_tick_systems` 72/0、`test_all_messages --live` 28/0 |

### 30.4 边界

- 服务端仍用「挖两次出肉」近似 `m_nBodyLeathery`，没有按 Leathery/MeatQuality 精确递减。
- 挖完 `RM_SKELETON` 后尸体直接 `entityRemove`，没有单独画骷髅帧。
- 蹲下动作播完回站立，不会「蹲着不动等下一次挖」；原版也是每次点击重新触发。

---

