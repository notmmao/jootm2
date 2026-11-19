# 数据库 Wiki 计划

> 「wiki」= 从 gamedata 树生成的静态数据库站（物品/技能/怪物/地图/NPC/商店/掉落），
> 由 `pyserver` 在 `/site` 提供，部署时与游戏同机同步。本文是该功能的计划与实施记录。

## 一句话
把 gamedata 树一键变成可离线浏览、可在线查询的数据库站，数据变化后重跑一个脚本即可全站更新，不引入任何动态后端。

## 功能定义
- 定位：玩家查资料的工具站，不是游戏的一部分；服务端只做静态文件分发，`/site` 挂载。
- 内容：首页统计 + 物品（按分类）/ 技能（按职业）/ 怪物属性 / 怪物分布 / 地图详情 / NPC 与所在地 / NPC 售卖物 / 掉落表；顶部导航互通。
- 每树一份：current 与 176 各存一份产物，切换数据树时 `/site` 跟随切换（见 §62）。
- 图标与地图全部本地生成：物品帧、技能帧、怪物/NPC 立绘、地图渲染图（含刷怪点），**不使用第三方站的图片**（有 watermark 的图一律不借）。
- 不做：在线数据库后端 / 搜索服务、玩家账号相关页面、上传与编辑、多语言。
- **剥离跟随（2026-11-19）**：wiki 与服务端/balance 同源读 `config/content_policy.json`——`gen_website.py::Data._apply_policy()` 在装载末尾过滤 items/monsters/maps/npcs/刷怪/掉落表，`high_drop.reset` 命中的怪照常展示但掉落表换基线。默认跟随剥离；`--no-policy` 生成原始全集（对比私服内容时用）。生成日志首行打印剥离统计。

## 数据源与口径（硬约束）
- 数据树 = `gamedata.root`（默认 `gamedata`，副本树 `data/gamedata/<name>`）；素材 = `resources/`（两树共享）。
- 页面数值只读树内文件：`mud2/DB/*.csv`、`Mir200/Envir/{MapInfo,MiniMap,MonGen,Npcs,MerChant}.txt`、`MonItems/`、`Market_Def/`、`QuestDiary/`。
- NPC 商品直接复用服务端脚本引擎：`pyserver/npc_script.py` 的 `NpcScriptEngine.goods()`，售价 = `Round(Price*1.1)*rate/100`，与游戏内商店同源，不允许在生成器里重造一套价格公式。
- 地图方向：`.map` 是列优先 `i = x*height + y`（Delphi `MapUnit.pas:182`），PNG 行优先 `idx[y*width + x]`。写反就是整图转置（曾发生，用户一眼看出比奇城方向不对）。
- 地图绘制规则对齐客户端 `PlayScn.pas`：背景 `Tiles.wil` 96×64 只画偶数格（:562）；中景 `SmTiles.wil` 48×32 每格（:586）；物件 `GetObjs(btArea)` 每格、底锚定 `(x*48, (y+1)*32 - frameH)`（:1170），按 y 序绘制。室内房间的墙/地板在物件层，均值降采样会整间丢失，所以小图必须真实合成。
- 刷怪点 = `MonGen.txt`，图标规则与客户端 `actorData.mjs` 一致（`getMonImgLib` / `getMonOffset` / 站姿 dir=4），带 1px 黑色描边保证浅色地面上可见。

## 产物与结构
- `scripts/gen_website.py`：主流程（`--tree` 裸树名或路径、`--out`、`--skip-existing-maps`、`--no-policy`、`--open`）；默认读生效指针。
- `scripts/website_assets.py`：CSS / 搜索 JS / 导航 / 页面壳（改样式只动这里）。
- `scripts/render_maps.py`：大地图 1px/格（even-sample 背景 + mid/fr/area 全分辨率 + 颜色查找表）与小图真实合成（阈值 `SMALL_TILES = 4000`）、怪物图标。
- `scripts/wil_extract.py`：WIL/WIX 帧解码（8 位索引色 + 共享调色板 / RGB565），零第三方依赖。
- 输出：纯静态 HTML + `assets/`（style.css、app.js、icons/、maps/），`file://` 也能直接看。

## 服务端挂载（`/site`）
- **当前访问方式（2026-11-17）：302 跳转到 list2**。`website.redirect`（默认 `https://list.notmmao.com/mir2-wiki/`）非空时，`/site` 前缀请求 302 到该地址，路径与 query 原样透传（含中文路径 URL 编码），跳转响应 `no-cache` 不缓存。站点本身已发布到 Vercel CDN（见「部署与同步」）。用 **302 而非 301** 是因用户在观察期，随时可把 `website.redirect` 设空回退到本地。
- 本地兜底路由（`website.redirect` 为空）：`pyserver/net.py` 中 `_is_under(path, config.website_prefix)` → `_serve_file(..., allow_gzip=False, cache_fn=website_cache_control)`；裸 `/site` 301 到 `/site/`（页面内链接是相对路径）。
- 缓存分档（2026-11-16）：HTML `no-cache`；图片 `public, max-age=604800, must-revalidate`（7 天，`WEBSITE_STATIC_MAX_AGE`）；JS/CSS/JSON 及未知类型 `max-age=300, must-revalidate`。所有响应带 ETag/Last-Modified，支持条件请求 304。站点重生成文件名不变，所以图片**不用** `immutable`——7 天后靠 ETag 自动取到新图。旧的统一 `max-age=60` 会让每次开地图页逐张重验图片，已废弃。
- 配置：`website.root`（properties / `JOOTM2_WEBSITE` / `--website`），前缀固定 `/site`；跳转 `website.redirect`（properties / `JOOTM2_WEBSITE_REDIRECT` / `--website-redirect`，传空字符串关闭跳转走本地）。
- 性能底线（用户硬性要求：不能影响主逻辑）：站点路由**不走 gzip**——zlib 压缩在事件循环里同步执行，`drops.html`（1.9MB）单次冷压 13.63ms，关掉后同步阻塞上限 4.21ms（tick 间隔 100ms）。独立进程 keep-alive 8 线程压测（≈158 req/s）下 tick 间隔与空闲不可区分（p99 203.3 vs 197.8ms）。
- 目录不存在时 `/site` 返回 404，启动日志提示生成命令，不影响游戏。

## 每树一份与切换
- `data/website`（主树）/ `data/website-<name>`（副本树）；`switch_data.py use` 同步改写 `gamedata.root` / `db.file` / `website.root` 三行指针。
- 切换后重启 pyserver 生效（与换世界同一次重启）。
- 切树不能 `--skip-existing-maps`：地图上的刷怪点来自各树 MonGen，跳过会留下另一棵树的点。该参数只用于同树重建页面（3 分钟 → 约 4 秒）。

## 部署与同步
- 代码走镜像（ACR）；wiki 不属于镜像（`data/` 在 dockerignore 内），单独 rsync：
  `rsync -a data/website/ root@aliyun.notmmao.com:/root/jootm2/data/website/`
- compose 已挂 `./data:/app/data`，同步后无需重启；验证 `GET /site/` 200 + 首页统计数字。
- 缓存头验证（改 `net.py` 需重启服务/镜像）：`curl -sI https://aliyun.notmmao.com/site/assets/icons/items/1.png | grep -i 'cache-control\|etag'` 应为 `public, max-age=604800, must-revalidate`；HTML 应为 `no-cache`。再带 `-H 'If-None-Match: <上次 ETag>'` 请求同一图片应返回 304。浏览器侧 DevTools Network 勾选 Disable cache 关闭后刷新两次，第二次图片应从 disk/memory cache 读取、无 `/site/*.png` 请求。
- 「wiki 站同步」是部署阿里云的标准步骤之一，与游戏代码一起做。

### list2（Vercel）部署与 /site 跳转（2026-11-17）
- 产物源：`data/website`（72MB / 1967 文件），因 list2 发布脚本有 60MB 上限，用 Pillow 做**无损**调色板压缩（RGBA 颜色数 ≤256 的图转 P 模式并逐像素校验一致）：`data/website-list2/`（54MB），压缩逻辑见 HANDOFF-PYSERVER §88。
- 发布：`python "C:/Users/Mao/.workbuddy/skills/list2-publish/scripts/publish.py" --source "D:/work/me/game/jootm2/data/website-list2" --name mir2-wiki`，push main 后 Vercel 自动部署。线上 `https://list.notmmao.com/mir2-wiki/`（commit `ed0c779`）。
- **list2 仓库 `core.quotepath=false`**：wiki 有中文文件名，`git diff --cached --name-only` 默认输出八进制转义，发布脚本前缀校验会误判为越界而回滚。发布前在 list2 仓库执行 `git config core.quotepath false`（只改本地 git 配置，不改受控文件）。
- **Vercel 缓存头边界（别重复排查）**：Vercel 对所有静态资源返回 `Cache-Control: public, max-age=0, must-revalidate`，但 `X-Vercel-Cache: HIT` + `Age` 说明**边缘 CDN 已命中、不回源**。效果是浏览器仍会发 304 条件请求（打向 Vercel 边缘、极快，观感上仍可能"看到载入"），但**不占阿里云带宽、不回源**——迁站核心目标已达成。想连浏览器 304 都消掉须改根级 `vercel.json` 加 `headers`，但 list2 技能禁止改根级配置、且子目录 `vercel.json` 对 Vercel 无效，故本轮不动。
- `/site` 跳转：pyserver 读到 `website.redirect` 非空即 302 过去，无需再 rsync wiki 到阿里云；阿里云只需更新镜像（配置默认值已内置，也可用环境变量覆盖）。验证：`curl -sI http://139.224.24.88:8080/site/maps.html | grep -i 'HTTP\|location'` 应为 302 + list2 地址。回退：把 `website.redirect` 设空并重启，恢复本地静态服务。

## 顺序与验收（已完成）
1. 图标与地图本地生成：物品 350+、技能 32、怪物 92+、NPC 46；地图 374 张（含刷怪点）。
2. 页面与导航：1017 页（176）/ 1072 页（current），0 断链（`map_link`/`mon_link` 对无页面目标降级为纯文本，MapInfo 注释行跳过）。
3. 方向与室内图校验：`mmap.wil` 8 方向色格比对（正确方向 18.5，其余 ≥30）；GM 传送到 0106 衣服店 / 0108 药店截图对照。
4. NPC 与售卖物：101 NPC（176）/ 133（current），商品 587 / 873 条，价格链与服务端一致（金创药小量 88=80×1.1 实测）。
5. `/site` 挂载：`test_pyserver.py` 99 PASS；301、404、`..` 穿越、Range/HEAD 实测通过。
6. 性能：见上节压测数据；全量生成约 3 分钟。

## 已知边界（不要当 bug 重复排查）
- MapInfo 登记 445 张，其中 71 张数据包内没有 `.map`（如 8000 系列、T000x）→ 站点标「缺 .map」；这是数据缺文件，不是引用错误。
- 大地图用每格取样色，不是逐像素真实合成——远看正确，贴脸看不清细节；室内小图才走真实合成。
- 怪物/NPC 立绘用 Appr 对应 WIL 的站姿帧，个别 Appr 在本地 WIL 缺失时回退占位。
- 搜索是纯前端（app.js 过滤），没有拼音/模糊匹配。

## 后续（本期不做）
- 增量生成（只重建变化的树/页面）、图标目录按内容哈希复用。
- 两棵树同时在线（目前一个 pyserver 实例只挂一个 `website.root`）。
- 地图 PNG 体积优化（59MB/374 张）。