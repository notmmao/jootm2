# jootm2 Python 服务端分支交接文档

> **最后更新**：2026-11-13（文档重构：历史轮次逐字归档到 `docs/handoff/`，本文件只留 索引 + 强制规则 + 已知缺陷 + 架构/命令/部署 + 最近轮次）
> **分支**：`feat/python-server`；远程默认分支 `origin/main`（**没有 master**）
> **本机多个 Agent 协作**：每轮改动追加到本文件末尾（`## §N 标题（日期）`，N 递增）；文件超过 ~500 行时把最老的轮次移入 `docs/handoff/archive-*.md` 并更新文末索引

## 0. 一句话状态

Java 后端已完整迁移为**纯标准库 Python（asyncio）**实现，49 种 WebSocket 消息全部有对应 handler（含 GM 命令等扩充），与 Java 版共用同一套协议 / SQLite schema / 配置文件；部署走 ACR 镜像（阿里云 / gp-bench-3 / 本地 Docker 三处），Java 版仅作对照保留。

## 0.5 强制规则（踩坑沉淀，违反会出事故）

1. **junction 禁止任何形式的递归删除**：`gamedata/Mir200/Map` 是 NTFS junction → `resources/Map`。`rm -rf`、**`git worktree remove`**、`shutil.rmtree` 都可能穿透删除真实地图。**2026-11-13 事故**：在临时 worktree 里建 junction 指向主仓库 `resources/Map`，`git worktree remove --force` 把主仓库 656 张地图删光（已从 gp-bench-3 rsync 恢复）。教训：① 临时目录/worktree 里**绝不建指向仓库数据的 junction**；② 清理前先显式删掉里面的 junction；③ 误删可从 gp-bench-3 / 阿里云的 `~/jootm2/resources/` rsync 恢复，恢复后重启服务重建 `resources.json`。
2. **`HANDOFF.md`（远澜原版）永不编辑**；`src/`（Java 源码）不动——本分支只维护 Python 端。
3. **部署约定**：「部署」默认指阿里云 `root@aliyun.notmmao.com`；访问服务一律用 **IP `139.224.24.88:8080`**（域名未备案，阿里云拦截 HTTP，返回 `403 + Server: Beaver`，且有约 1 小时延迟生效的坑）；gp-bench-3 内网入口 `https://mir2.notmmao.com/`（Cloudflare Tunnel，见 §21.8）。
4. **回归解释器**：服务端复现/回归/live 测试一律 `D:/Python310/python.exe`（3.10，与线上镜像运行时一致；3.13 会掩盖 3.10 专属差异，见归档 a §14）；Playwright 浏览器测试用 `D:/Python39/python.exe`。
5. **纯标准库**：pyserver 禁止第三方包（Docker 镜像只需 `python:3.12-slim`）；单事件循环串行世界模型，**禁止引入线程池**。
6. **玩法/数值改动必须有 Delphi 权威依据**：本地源码 `D:\work\me\game\传奇\MIR2\GameOfMir`（GBK 编码，`MirClient/` 与 `M2Server/`）；找不到依据不硬改，依据（文件:行号/常量）写进轮次记录。
7. **新增服务端字段必须 grep 消费点**（历史教训：`recompute`/`tick`/MapQuest 整层「写了没人消费」）；`PENDING_TYPES` 保持空集；消息数量变化要同步 `scripts/test_all_messages.py` 与分派校验。
8. **镜像不含游戏素材**：`resources/`（1.6G）永远卷挂载不打进镜像；`resources/` 下必须直属 `Data/ Map/ Wav/` 三目录齐全，`resources.json` 才会生成；删文件前先列清单。
9. **部署后验证走 `/api/version`**（见 §21 流程），核对 `version` 字段 == 最新提交短 hash 即可，**不做全量深度回归**（用户约定 2026-11-13）。
10. **GBK vs UTF-8**：`gamedata/Mir200/Envir/*.txt`、NPC 脚本、MonItems 是 GBK（UTF-8 grep 中文假阴性）；`gamedata/mud2/DB/*.csv` 是 UTF-8。

## 0.6 已知缺陷 / 遗留（未处理，别当新 bug 重复排查）

来自 §39.7 / §41.5（最近轮，权威）：

- `m_nMagLight`（魔法瞬时光）未做；火墙/火堆 `TClEvent.m_nLight` 光源待接（特效光源钩子已预留）
- lig0 贴图 additive 合成是 canvas 近似，非原版 `FogCopy` 逐像素 ColorLevel 压暗；极暗场景层次感与原版有可见差异
- 商店未扣库存（原版 `List20.Delete(ii)`）
- 天狼蜘蛛(118) 原版 `THighRiskSpider` 高风险双倍暴击未做
- 鹿 1/30 `TChickenDeer` 变种品质未做；矿石卖价超上限 1.3 倍分支实际不可达（纯度上限恒低于 DuraMax）
- `ET_PILESTONES` 碎石堆视觉事件未渲染；挖矿无专门重击动画（复用 attack 帧）；武器耐久挖到 0 无损坏提示（原版一致地不能再挖）

更早的已知边界（PvP 永不 miss、逐技能施法音近似、受击/死亡固定男声、大地图/系统菜单 DOM 简化实现、0 伤害 `max(1,…)`、`_regen` world 级共享计时等）——详见归档 a/c/d 各节「边界/遗留」小节（§13.3 / §16.4 / §18.3 等）；部分可能已被后续轮次处理，以最近轮次为准。

---
## 2. 架构与关键设计决策

### 2.1 为什么是纯标准库

本机 Python（3.13）没有 `fastapi` / `uvicorn` / `orjson` / `numpy` 等第三方包，也没有稳定的安装渠道。因此实现只依赖 `asyncio` / `socket` / `sqlite3` / `json` / `struct` / `dataclasses`，`pip install` 一步都不需要，Docker 镜像也因此只需 `python:3.12-slim`。

### 2.2 并发模型

Java 侧 `onMessage`、`tick`、`saveAllPlayers`、`handleAdmin` 全部包在 `synchronized (world.lock)` 里，本质是单线程串行世界模型。Python asyncio **单事件循环天然等价**，所有状态只在事件循环内读写，因此不需要任何锁。不要引入线程池去「加速」世界逻辑，那会直接破坏语义一致性。

### 2.3 协议兼容策略

**前端不改一行**（唯一例外见 §4：`frontend/map/Maps.mjs` 的地图 tile 推断 bug 修复）。两个服务端共用：

- WebSocket TextFrame + UTF-8 JSON，`{"type": ...}` 字符串消息类型（**不是**原版 Delphi 的数字协议号）
- 同一份 `config/server.properties`
- 同一套 SQLite schema（账号、角色、模板表），Java 创建的老账号能在 Python 侧直接登录（密码都是 `SHA-256(salt + password)` 小写 hex）
- 同一份 `gamedata/`：`Mir200/Envir/*.txt` 是 **GBK**，`mud2/DB/*.csv` 是 **UTF-8**，SQLite 的 `items/storage/skills/quick_slots` 与模板表 `data` 列是 Gson JSON 字符串

### 2.4 模块清单

| 文件 | 行数 | 职责 |
|---:|---:|---|
| `__main__.py` | 80 | CLI 入口、启动/关闭、生成 `resources.json` |
| `config.py` | 144 | 读 `server.properties` + 环境变量 / CLI 覆盖 |
| `net.py` | 436 | HTTP 静态 / REST / Range 206 / CORS / WebSocket 握手与分帧 / tick 定时 |
| `service.py` | 479 | `GameService`：46 消息分派、账号角色、REST、`sendBagUpdate`、`/api/migration` |
| `world.py` | 1220 | `GameWorld` / `GameMap` / `Actor` / `Player` / `Monster`、`recompute`、**完整 tick** |
| `world_extra.py` | 808 | 掉落 / 地面物品 / NPC / 行会 / 攻城 / 组队 / 仓库 / 交易 / 火墙 / 召唤 / `killMonster` / `addExp` / MapQuest 触发 |
| `handlers_items.py` | 349 | 拾取、使用、穿脱装备、快捷栏、丢弃、移动物品、技能键 |
| `handlers_npc.py` | 132 | NPC 对话、选项、买卖 |
| `handlers_combat.py` | 871 | 物理攻击 / 刺杀 / 半月 / 野蛮冲撞 / **33 个技能分支** / 伤害与经验 |
| `handlers_social.py` | 349 | 行会、攻城、组队、仓库、交易闭环 |
| `npc_script.py` | 409 | 直译 `NpcScript` + `NpcScriptEngine`（`#IF/#ACT/#SAY/#ELSEACT/ELSESAY`、`[goods]`、任务变量） |
| `gamedata.py` | 343 | GBK 文本、`MapInfo`/`MonGen`/`MiniMap`/`MapQuest`、`read_map`、`map_tile_stride`、`resources.json` |
| `db.py` | 307 | SQLite DAO、CSV 导入、密码哈希 |
| `templates.py` | 224 | `ItemTemplate` / `MonsterTemplate` / `MagicTemplate`（dataclass 复刻 Gson 字段名） |
| `util.py` | 179 | 常量表、`java_div` / `java_round`、装备槽、方向、技能解析 |

**Mixin 架构**：`GameService(ItemHandlers, NpcHandlers, CombatHandlers, SocialHandlers)`，`MIGRATED_TYPES = … | ITEM_TYPES | NPC_TYPES | COMBAT_TYPES | SOCIAL_TYPES`，`PENDING_TYPES = frozenset()`，并在构建分派表时校验 `set(_handlers) == set(MIGRATED_TYPES)`，不一致直接 `RuntimeError`。`GameWorld(WorldExtra)` 同理。

**查询迁移进度**：`GET /api/migration` → `implementation=python-stdlib, totalTypes=46, migratedCount=46, pendingCount=0, stage=all-46-types`。

### 2.5 双栈回归

```
java -jar target/jootm2.jar                          # Java 占 8080（需 JDK 17）
python -m pyserver --port 8081 --db data/jootm2-py.db   # Python 占 8081，独立 DB
python scripts/compare_stacks.py --java-port 8080 --py-port 8081 --full
```

`compare_stacks.py` 对比 46 步的响应类型与字段结构；`scripts/golden/protocol_baseline_full.json` 是 Java 侧录制的基线，可 `--golden` 离线回放。

---

## 3. 启动、配置与回归命令

### 3.1 启动

```bash
python -m pyserver                                  # 默认 8080，读 config/server.properties
python -m pyserver --port 8081 --db data/jootm2-py.db
docker compose up --build                           # 默认 Python 镜像
docker compose --profile java up jootm2-java --build   # Java 对照，宿主 8082
```

版本机制：`python scripts/gen_version.py` 生成 `pyserver/version.json`（gitignored），`GET /api/version` 返回 `{version: <git 短 hash>, subject, builtAt, changes[...]}`；**镜像构建前必须先生成**，部署后只核对该端点。

CLI：`--config --host --port --db --gamedata --web --resources --tick-ms --no-resource-index --version`。
环境变量：`JOOTM2_PORT / JOOTM2_HOST / JOOTM2_DB / JOOTM2_WEB_ROOT / JOOTM2_RESOURCES / JOOTM2_GAMEDATA / JOOTM2_ROOT / JOOTM2_CONFIG`。
启用端点：`/` 静态、`/ws`、`/resources/*`、`/api/status`、`/api/players`、`/api/migration`。

### 3.2 回归脚本（全部零依赖）

```bash
python scripts/verify_resources.py --self-test            # 资源体检脚本自检 10/10
python scripts/verify_resources.py resources              # 真实资源体检
python scripts/smoke_ws.py --port 8081                    # WS 8 步冒烟
python scripts/test_pyserver.py --live 8081               # 单元 + 实时自检
python scripts/test_all_messages.py --live 8081           # 46 handler + 33 技能分支 + 18 成功路径
python scripts/test_tick_systems.py                       # tick / 装备属性 / MapQuest，53 断言
python scripts/compare_stacks.py --java-port 8080 --py-port 8081 --full
python scripts/compare_stacks.py --py-port 8081 --golden scripts/golden/protocol_baseline_full.json
python scripts/convert_audio.py                       # 全量 WAV→MP3（幂等，需 ffmpeg）
python scripts/convert_audio.py --check --probe 20    # 校验 mp3 产出 + ffprobe 抽样
python scripts/convert_audio.py --explain 103         # 查某编号对应的 mp3 / wav URL
python scripts/convert_audio.py --self-test           # 12/12，临时目录自检
node scripts/test_audio.mjs                          # 音频前端链路 18/18
node --check frontend/audio/SoundManager.mjs
# 渲染防闪烁抓帧（需本机 Playwright，脚本在 data/，不提交）：
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe.py
"F:/funasr/.venv/Scripts/python.exe" data/flicker_probe2.py
"F:/funasr/.venv/Scripts/python.exe" data/doublebuffer_check.py
```

最后一次全量验证结果（全绿）：`48 PASS`（离线 `test_pyserver.py`，含 2 条地图用例）/ `53 PASS`（live）/ `18/18` 成功路径 / `53/0` tick 断言 / `8/8` WS 冒烟 / `10/10` 体检自检 / `--full` 46 步响应类型全一致、字段结构零差异 / golden 回放零差异 / `node --check frontend/map/Maps.mjs` 通过；音频链路追加：`12/12` 转换自检、`18/18` 前端回归、`723/723` mp3 产出、`20/20` ffprobe 可解码、HTTP 200/206 与 MIME 实测通过；§11 追加：全部 `frontend/**/*.mjs` 语法检查通过、无 console/page error、首屏地图纹理 231/231、强制走路动画 288 次取帧 0 次缺失（详见 §11.4）。

---


---
## 21. Docker 真机构建与部署（2026-11-11，第九轮）

§5.1 遗留的「Docker daemon 未启动、只做过等价目录模拟」在本轮补齐：用户本机 Docker 24.0.7 + Compose v2.23.3 真机构建并全量验证通过。**本轮只动 `.dockerignore` 与 `docker-compose.yml`，`Dockerfile` / 代码 / 协议零改动。**

### 21.1 resources 是否打入镜像的评估结论：**不打，维持卷挂载**

- 打入：镜像 122MB → ~1.8GB（resources 1.6G = Data 1.2G + Map 204M + Wav 191M + Audio 16M），且每次 build 都要把 1.6G 打包进 build context 传 daemon，构建极慢；换资源要重建镜像；镜像分享会把客户端素材带出去（版权边界）。
- 挂载：镜像保持 122MB；部署时 rsync 一次资源到服务器；资源与代码独立更新。`Dockerfile` 注释「resources/ 由卷挂载（客户端素材不随镜像分发）」的设计意图不变。

### 21.2 修掉的 3 个部署坑

| 坑 | 修复 |
|---|---|
| `.dockerignore` 未排除 `client_original/`（441MB 原始客户端存档）→ build context 被打进 daemon | `.dockerignore` 加 `client_original`、`docs` |
| `gamedata/Mir200/Map` 是 NTFS junction → Docker CLI 打包 context 会跟随 junction 把 204MB `.map` 打进镜像；且 **Linux 服务器 checkout 没有 junction**（gitignore 不跟踪），镜像内该目录为空 → 服务端回退 256×256 全通行图、阻挡/门失效（§5.3 的坑在容器里复现） | `.dockerignore` 加 `gamedata/Mir200/Map`（context 不带地图）；compose 嵌套挂载 `./resources/Map:/app/gamedata/Mir200/Map`，服务端 `.map` 与前端 `/app/resources/Map` 同源，服务器零手工操作。想单独维护服务器地图时删掉该挂载行即可 |
| Java 对照服务同样读 gamedata 地图 | `jootm2-java` 补同一行嵌套挂载 |

### 21.3 真机验证（全绿）

- `docker compose build` 成功，镜像 `jootm2:python` **122MB**。
- 启动日志：`445 map defs, 2173 gates`、`spawned monsters=704`（与 §5.3 本机直跑数字一致，证明嵌套挂载生效、非回退图）；`resources.json` 生成 images=58 / maps=656 / wavs=723。
- HTTP：`/api/status`（monsters=704）、`/api/migration` **48/48** pending=0、`resources.json` 200、`Hum.wil` Range 分片 **206**、`audio_map.json` 200、`Audio/103.mp3` 200 audio/mpeg、`Map/0.map` 200。
- WS：`smoke_ws.py --port 8080` **8/8**；`test_all_messages.py --live 8080` **27/0 + ping→pong**（按 §14.5 用 `D:/Python310/python.exe`）。
- 健康检查 `healthy`；`docker compose restart` 后 SQLite 数据持久（`./data` bind mount）、资源索引重建正常。
- 注：容器 `./data:/app/data` 与本机开发共用同一 `data/jootm2.db`，本轮冒烟在库里新增了测试账号；服务器部署不受影响。

### 21.4 服务器部署步骤（Linux）

```bash
# 1. 传代码（不含 resources/data/client_original）+ 资源
rsync -a --exclude resources --exclude data --exclude client_original --exclude target ./ user@server:/opt/jootm2/
rsync -a ./resources/ user@server:/opt/jootm2/resources/     # 1.6G，一次即可
# 2. 服务器上构建并启动
cd /opt/jootm2 && docker compose up -d --build
# 3. 验证
curl http://localhost:8080/api/migration    # 48/48
docker logs jootm2 | head                    # 应见 445 map defs / 2173 gates
```

带宽紧张时可先不传 `resources/Wav/` 内容（保留空目录）：音效走 `resources/Audio/*.mp3`（16MB），wav 仅是 404 回退；`Data/` 里 19 个未引用图库 476MB 也可先不传（见 §6.1.5，但 `verify_resources.py` 会告警）。

### 21.5 已实际部署到 gp-bench-3.hil（2026-11-11）

- 服务器：Ubuntu 22.04 / Docker 27.5.1 / **docker-compose v1（1.29.2，无 `docker compose` 插件，用 `docker-compose` 命令）**，compose 文件无 `version` 键 v1 可直接解析；磁盘 94% 已用（剩 15G），部署占 ~2.5G。
- 步骤：`rsync` 代码（排除 resources/data/client_original/target/.git）→ `docker-compose build` → `rsync resources/`（1.65G / 2233 文件）→ 部署在 `~/jootm2`，`docker-compose up -d`。
- 两个坑：① rsync 会跟随 NTFS junction，把 `resources/Map` 204MB 复制进 `gamedata/Mir200/Map`（已在服务器删除，compose 嵌套挂载遮住该路径，无影响）；② 传前实测 `ss -tln` 确认 8080 空闲。
- 验证（全绿）：启动日志 `445 map defs / 2173 gates`、monsters=704；`/api/migration` 48/48 pending 0；`resources.json`/mp3 200、WIL Range 206；容器 `healthy`；从本机走 LAN `/api/status` 31ms、`smoke_ws.py --host gp-bench-3.hil` **8/8**。
- 更新部署：在开发机改完代码后 `rsync` 增量代码（compose 的嵌套挂载让 resources 无需重传）→ `docker-compose up -d --build` 即可，SQLite 数据在 `~/jootm2/data/`。

### 21.6 已部署到阿里云 root@aliyun.notmmao.com + 镜像推送 ACR（2026-11-11）

- 服务器：Alibaba Cloud Linux 3 / Docker 25.0.3 / **Compose v2（`docker compose`）**；磁盘剩 7.4G（81%）、**内存仅 1.8G**（纯 asyncio 服务端实测正常，容器运行后宿主剩 ~920M available）；部署在 `/root/jootm2`，8080 空闲（部署前 `ss -tln` 实测）。
- **镜像构建的坑**：服务器 `/etc/docker/daemon.json` 配的加速源 `d.notmmao.com` 对 `python:3.12-slim` 返回 **429**，备用 aliyuncs 个人源未走通，直连 `registry-1.docker.io` 超时 → 服务器上 `docker compose build` **不可用**。解法：本机 `docker save jootm2:python | gzip`（122MB → 42MB）→ `rsync` → `docker load`。
- 资源传输同 §21.5（rsync 跟随 junction 多复制 204MB 地图，同样已删）；`docker compose up -d` 后验证全绿：445 map defs / 2173 gates、monsters=704、migration 48/48、Range 206、公网 `/api/status` 75ms、`smoke_ws.py --host aliyun.notmmao.com` **8/8**。
- **镜像已推送阿里云 ACR**（个人版，上海）：`crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python`，标签 `a5784cc` 与 `latest`（digest `sha256:9d25ee3141…`，与本地 `jootm2:python` 同 ID `d5237ece2fc0`）。镜像**不含客户端素材**（resources 不打入），无版权问题。已验证阿里云 ECS 可直接 `docker pull`。
- **以后部署/更新的标准路径**（绕开 429，无需在服务器上构建；2026-11-13 起加版本机制）：
  ```bash
  # 开发机：先提交，再生成版本信息（写 pyserver/version.json，gitignored），然后构建推送
  python scripts/gen_version.py                      # version = git 短 hash
  docker build -t crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:<hash> .
  docker push ...  # 或按 §21.5 的 rsync + 服务器构建路径（gp-bench-3 可构建）
  # 服务器（任一台）：
  docker pull crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:latest
  docker tag  crpi-n05futsy39yjkqf2.cn-shanghai.personal.cr.aliyuncs.com/notmmao/mir2-python:latest jootm2:python
  cd ~/jootm2 && docker compose up -d     # resources/data 已就位则无需重传
  ```
  compose 的 `image: jootm2:python` 名字不变，`docker tag` 打回本地名即可复用现有 compose 文件，无需改动。
- **部署后验证（用户约定 2026-11-13，轻量化）**：只做 `curl http://139.224.24.88:8080/api/version`（gp-bench-3 用 `https://mir2.notmmao.com/api/version`），核对 `version` 字段 == 最新提交短 hash + `/api/status` 200，**不再全量深度回归**；深度回归只在改协议/改 tick 等核心时做。

### 21.7 e8b8f62 更新部署 + ICP 备案拦截坑（2026-11-11）

- `e8b8f62`（IndexedDB 分片缓存 + ETag/304/gzip + Loading 预加载）已推送 ACR（标签 `e8b8f62` + `latest`，digest `sha256:a24227e39b…`，image `31b1f7db8cd3`）并按 §21.6 推荐路径部署到阿里云（pull → tag → `compose up -d`）。
- 新功能实测（服务器本机）：ETag/`If-None-Match` 二次请求 **304 (0B)**；gzip 后 `main.mjs` 4751B → 2440B。
- ⚠️ **重要坑：阿里云 ECS 的 ICP 备案拦截**。`notmmao.com` 未备案，阿里云大陆节点会拦截未备案域名在 80/443/8080 的 HTTP 流量：公网所有请求返回 `403 Forbidden` + `Server: Beaver` + 「Non-compliance ICP Filing」拦截页（HTML iframe 指向 beian-block）。**且拦截有延迟**——刚部署完域名访问是通的（还做了 8/8 冒烟），约一小时后才生效，极易误判成代码 bug（本次就先怀疑了新 net.py）。判别方法：服务器本机 `curl localhost:8080` 始终 200 即为链路拦截；响应头 `Server: Beaver` 不是 pyserver 发的。
- **约定：访问阿里云服务一律用 IP `139.224.24.88:8080`，不用域名**（用户明确要求）。冒烟/回归用 `--host 139.224.24.88`；SSH `root@aliyun.notmmao.com` 不受影响。恢复域名访问需 ICP 备案，或改用非拦截端口（未验证）。

### 21.8 7b6e4a5 部署（2026-11-12）

- `7b6e4a5`（进游戏地图加载优化：首屏纹理预载 + `.map` 字节提前 + 206 分片应用层 gzip）推送 ACR（`7b6e4a5` + `latest`，digest `sha256:0d0b2d9d73…`，image `4e3af0dc8080`），按 §21.6 标准路径部署阿里云。
- IP 直连验证：`/api/status` 41ms；`.map` Range 分片 **206 + ETag**；`smoke_ws.py --host 139.224.24.88` 全通。
- **流程约定（用户要求记住）**：今后「部署」默认指阿里云；标准流程 = 本机 `docker compose build` → 推 ACR（git 短 hash + `latest` 双标签）→ 阿里云 `docker pull` → `docker tag` 为 `jootm2:python` → `compose up -d` → IP 直连验证。gp-bench-3 只在用户点名时更新。
- 同日追加 `35665ec`（尸体绘制层级 + 挖肉蹲下动作）与 `4e15c9e`（新角色初始装备 + 服装店 1 级布衣）：ACR digest `sha256:5027aac745…`（image `3722552b0527`），同流程部署；IP `/api/status` 29ms、WS 冒烟全通。
- **gp-bench-3 也切换到 ACR 镜像**（2026-11-12，image 同为 `3722552b0527`，与阿里云完全一致），LAN WS 冒烟全通。⚠️ 坑：gp-bench-3 的 compose v1（1.29.2）解析 **BuildKit 构建的镜像会抛 `KeyError: 'ContainerConfig'`**（开发机 Docker 24 默认 BuildKit，镜像 config 无该字段；此前 gp-bench-3 本地构建走旧 builder 所以没暴露）。修复：无需 root，下载 compose v2 独立二进制到 `~/.local/bin/docker-compose`（v2.27.0）；`~/.profile` 已有 `.local/bin` 前置规则，**交互登录**后裸 `docker-compose` 即 v2，**非交互 ssh** 仍解析 `/usr/bin` 的 v1，脚本里请用显式路径 `~/.local/bin/docker-compose`。ota 用户无免密 sudo。
- `3d570ce`（材质精确区间加载修黑条/卡顿 + 现场诊断快捷键）：ACR digest `sha256:6674de0674…`（image `a6357852f385`），同流程部署阿里云；IP `/api/status` 81ms、WS 冒烟全通。本轮纯前端改动，服务端协议不变。gp-bench-3 同步部署（ACR pull + `~/.local/bin/docker-compose`），image 一致、LAN 冒烟全通。
- **gp-bench-3 接入 Cloudflare Tunnel**（2026-11-12）：新增 `~/jootm2/docker-compose.override.yml`（gp-bench-3 专用，rsync 无 `--delete` 不会覆盖）把 `jootm2` 服务挂到外部网络 `cf_cloudflared_network`（cloudflared 容器同网络，token 模式、ingress 配在 CF 后台按容器名 `http://jootm2:8080` 寻址）。对外入口 **`https://mir2.notmmao.com/`**。验证：该域名 `/` 与 `/api/status` 均 200；TLS WebSocket 走 443 握手 101 + ping→pong 通（tunnel 支持 WS）；LAN 8080 直连不受影响。此前 jootm2 是手动 `docker network connect` 挂网，compose 重建会掉——现已声明化。aliyun 未接 tunnel（域名备案被拦与 CF 无关，CF 入口解析到 gp-bench-3）。
- `6e39dd4`（§39~41 十个提交批次：多光源/商店原价/卖货定价/肉品质/挖矿等）部署阿里云（2026-11-13）：ACR digest `sha256:f87b66d791…`（image `577b947d3ab8`）。启动后 monsters **704 → 3422 属预期**：`ebe2720`（第 11 轮第 18 项刷怪密度）删掉了每条 MonGen 钳制 `min(max(count,1),3)` 的旧实现，改按 MonGen.txt 真实 count 刷怪——排查时曾疑似 bug，用离线探针（`GameWorld(cfg, db)` 构造后数 Monster）确认 HEAD 与本地同代码均为 3422、`test_tick_systems` 123/0 绿。探针注意：worktree 裸 checkout 缺 `gamedata/Mir200/Map`（junction 不进 git）会导致刷怪为 0，需先建 junction。IP `/api/status`、WS 冒烟全通。

---


---
## §39 第 13/14 轮：m_LightMap 多光源 + 免蜡 / 死亡黑白屏 / 掉落「爆了」/ 商店价格 / 金币图标 / 蜘蛛毒 / 食人花（2026-11-13）

### 39.1 m_LightMap 多光源（原版 PlayScn.pas）

- 新增 `frontend/map/LightMap.mjs`：`LMX=30 / LMY=26 / MAXLIGHT=5`，
  `LightMask0..5` 亮度核（与 `PlayScn.pas:36-128` 常量逐值比对通过）、
  `ClearLightMap / UpdateBright / CheckOverLight / AddLight` 等价逻辑、
  `loadLightMasks()` 加载 `Data/lig0a.dat..lig0f.dat`（前 8 字节
  `<ii` 宽高，后跟 `w*h` 字节 fogmask，值 0..30）。
- `Maps.mjs` 保存 `.map` tile 的 `btLight`（`light` 字节，原版 Envir.pas:33
  「0..1..4 光源强度」），此前解析出来直接丢弃。
- `MapActor.mjs::_drawFog` 重写：每帧 Clear → 收集光源（地图 btLight 格、
  玩家 `m_nChrLight=3`、NPC light=2/守卫 4、特效光源）→ `AddLight` →
  `ApplyLightMap` 用 6 张 lig0 贴图 additive 累加 → `destination-out`
  挖掉整屏暗色（近似原版 `DrawFog` 的 ColorLevel 压暗）。
- 服务端 `brief_json` 下发 `light`：玩家右手持武器且耐久>0 → 3
  （ObjBase.pas:3384），NPC 2、守卫 4（ObjNpc.pas:5652 / ObjGuard）。
- **免蜡开关**：系统菜单新增「免蜡：开 / 关」，`localStorage['jootm2.forceNotViewFog']`
  持久化，等价 `MShare.pas g_boForceNotViewFog`（PlayScn.pas:1471
  `if g_boViewFog and not g_boForceNotViewFog`）。

### 39.2 死亡黑白屏（用户反馈 25）

- `kill_player` 不再立刻满血回城：广播 `death` 后停尸原地（hp=0、
  `p.dead=True`），前端 `PlayScene` 收自身 `death` 置 `_selfDead`，
  渲染整屏黑白（近似 `ceGrayScale`）+ 关闭 fog（原版 `g_boViewFog := FALSE`），
  拦截移动/攻击/施法；BGM `gameover` 继续播（原版 SM_NOWDEATH →
  `PlayBGM(bmg_gameover)`，Actor.pas:2388）。
- 复活走「返回角色选择 → 重登」：`service._do_enter` 登录时 HP<=0 按
  `UsrEngn.pas:576-600` 回出生点 ±2 格随机 + `HP := 14`（不再满血）。
- 消息分派加死亡拦截：死亡玩家发 move/attack/magic 等直接忽略。

### 39.3 掉落「爆了」（用户反馈 26）

- `scatter_pos` 重写为原版 `GetDropPosition`（ObjBase.pas:1534）：从半径 1
  逐格方形扫描（不是只取外环），首格无物品即用；找不到空位回退「同圈
  占用最少格」（原版 n24<8），最后才退尸体中心；范围 `DropWide=3`。
- `add_ground_item`：同格金币 `<=2000` 合并（Envir.pas:215），非金币同格
  上限 **5 件**（`ObjList.Count >= 5` 拒绝，Envir.pas:230）——BOSS 100 行
  `1/1` 掉落不再被「一格一件」挤成大圈，而是按原版语义堆叠。
- 前端同格多件掉落按序号微偏移绘制，拾取判定仍按格。

### 39.4 商店价格（用户反馈 27）

- 核对 `LocalDB.pas:3251`（`[goods]` 行 = `物品名 库存 刷新时间`，第二列
  不是单价）+ `ObjNpc.pas`：`GetUserPrice = Round(Price/100*rate)`、
  `CheckItemPrice` 补货价 = `Round(StdItem.Price * 1.1)`、
  `%rate` 只有 `>=55` 才接受、`+N` 是 `m_ItemTypeList` 类型过滤。
- `npc_script.py`：价格改为 `delphi_round(Price * 1.1) * rate / 100`
  （原版先 1.1 后乘倍率）；`NpcScript` 解析 `+N` 存 `item_types`；
  `goods()` 增加 `CheckItemType` 过滤与 3-token 行约束。
- 把 `delphi_round` 上移到 `util.py`（handlers_combat 里同样语义的两份
  实现合一）。卫家店实测：木剑 55 / 青铜剑 990 / 修罗 11000（= Price×1.1），
  与 1.76 原版一致。

### 39.5 金币图标 / 蜘蛛毒 / 食人花（用户第 14 轮反馈）

- **金币图标**：`M2Share.pas:3568 GetGoldShape` 分 5 档
  （<30→112、≥30→113、≥70→114、≥300→115、≥1000→116，DnItems 钱袋）。
  `GroundItem.to_json` 金币分支下发真实 looks（此前恒 0 → 客户端画圆点），
  前端金币走 `_drawDropTex(looks)`，贴图未就绪才退回圆点。
- **蜘蛛毒**：`TSpitSpider.SpitAttack`（ObjMon.pas:706）——
  `m_boUsePoison` 的怪命中伤害>0 后 `Random(AntiPoison+20)=0` →
  `POISON_DECHEALTH 30s point1`。race 82（毒蜘蛛/剧毒蜘蛛/沙虫等）与
  race 119（花吻蜘蛛，TBigPoisionSpider）毒；race 118（天狼蜘蛛）
  显式 `m_boUsePoison := False` 不毒。挂在 `_apply_hit_to_player` 尾部
  （远程飞矢与近战路径共用）。
- **食人花**：race 85 = `TStickMonster`（ObjMon2.pas:154）——
  `m_boStickMode=True` 永不移动、`m_boFixedHideMode=True` 潜地，
  目标 `abs<4` 出土（ComeOut）、`>4` 或无目标潜回（ComeDown），
  只打相邻格。服务端 `Monster.hidden` 字段 + `_stick_ai`；隐藏不进
  `_sight_actors/nearby`，出土广播 `entityAdd`、潜回 `entityRemove`。
- **挖肉产出**：`butchable` 增加 race 82/85（构造里 `m_boAnimal := True`；
  118/119 为 False 不可挖）。新增 `roll_butch_loot`：挖空尸体按该怪
  MonItems 表掷骰（原版 `TakeBagItems` 语义，ObjBase.pas:17491），
  食人花叶(`食人树叶`) 1/1、果实 1/5、毒蜘蛛牙齿 1/3 都走这条链；
  无掉落表才回退「鸡→鸡肉 / 其余→肉」，背包空提示「未发现任何物品。」。
  `new_item_instance` 顺带补 `name` 字段。

### 39.6 验证

| 项目 | 结果 |
|---|---|
| `test_pyserver` / `test_tick_systems`（新增 19 条本轮用例） | **76/0、123/0** |
| `test_all_messages --live 8080`（新增死亡重登用例） | **31/0** |
| `test_map_view` / `test_sfx_map` | 34/0、84/0 |
| Playwright 浏览器冒烟（登录→PlayScene→无 pageerror） | PASS，darkLevel=1，金币/掉落路径无异常 |

### 39.7 遗留

- 光源快照只下发了 `light`，`m_nMagLight`（魔法瞬时光）未做；
  火墙/火堆 `TClEvent.m_nLight` 光源待接（特效光源钩子已预留）。
- lig0 贴图 additive 合成是 canvas 近似，不是原版 `FogCopy` 的逐像素
  ColorLevel 表压暗；极暗场景层次感与原版有可见差异。
- 商店未扣库存（原版 `List20.Delete(ii)`）；`querySellPrice` 卖价仍是
  `Price/2`，原版按耐久折算。
- 天狼蜘蛛(118) 攻击伤害路径 OK 但它原版是 `THighRiskSpider`（高风险
  双倍暴击未做）。

## §40 第 15 轮：村店/城店品类核查 + 卖货定价/收购类型修正（2026-11-13）

### 40.1 用户问题：新手村（边境村等）卖的技能书/武器/药水应该比比奇主城少？

核对依据：本地原版 `gamedata/Mir200/Envir/Market_Def/*.txt`（1.80 GOM 服务端
原封脚本）+ `StdItems.csv` 联表，按脚本头 `+N` 分类逐店比对；网络佐证用
公众号文章《银杏村与边界村》（店内截图价目）与若干 1.76 攻略文。

**结论：不是「更少」，而是「品类分工 + 库存量 + 倍率」三重差异：**

| 店 | 边境村（T 前缀，比奇省西南 290-305,600-612） | 比奇城（B 前缀） |
|---|---|---|
| 武器店 | 2Twe 20 件（多一把**鹤嘴锄**，矿区必备） | 2Bwe 19 件 |
| 小店/药店 | 4Tdu：**蜡烛/火把/随机传送卷/地牢逃脱卷/回城卷/修复油/强效金创药/强效魔法药** | 4Bdu：小量/中量红蓝药+强效+**太阳水**（无蜡烛火把卷轴） |
| 书店 | 5Tbo 21 本，**%80 打 8 折** | 5B 23 本（多「集体隐身术」「火墙」两本高级书），%100 |
| 服装店 | 3Tdr 14 件（多新手**布衣(男/女)**） | 3Bdr 12 件（轻型盔甲起步） |
| 肉店 | 1Tme 鸡肉/肉，**库存 10** | 1Bme 同 2 种，**库存 100**（原版卖空要等 [goods] 第三列刷新时间） |

银杏村（G 前缀，比奇省东南 620-650,590-640）武器店 20 件与边境村一致，
药店 4Gdu 与比奇 4Bdu 完全相同。网络截图价目（木剑 55 = 50×1.1、铁剑 1100
= 1000×1.1）再次验证 §39.4 的买价公式。

**服务端品类清单本来就是直读这套 Market_Def 脚本，无需改动**；差异已经
是原版行为。真正的原版语义修正见 40.2。

### 40.2 原版语义修正：卖给 NPC 的定价与收购类型（handlers_npc._sell_price）

逐字核对 `ObjNpc.pas`：
- `ClientSellItem:2156` → `nPrice := GetSellItemPrice(GetUserItemPrice(UserItem))`
  = `Round(GetItemPrice(...) / 2.0)`（GetSellItemPrice:2127-2130）。
- `GetItemPrice:999-1024`：该店 `[goods]` 补货清单内的物品 → 价表里的
  `Round(Price*1.1)`（CheckItemPrice:798-823 补货即入价表）；不在清单但
  StdMode 在脚本头 `+N`（m_ItemTypeList）里 → `StdItem.Price`；都不满足 →
  `-1`，`nPrice<=0` 卖不掉。
- **关键纠偏**：`+N` 不影响货架清单！`BuyItem:1434-1456` 遍历整个
  `m_GoodsList` 列出全部补货商品（§39.4 曾担心过滤导致比奇药水店变空店，
  实查 goods() 本就未过滤，无此 bug）；`+N` 真正的用途是**收购限制**。

实现：
- 本店补货物品卖价 = `Round(Round(Price*1.1)/2)`（木剑 55→27.5→28）。
- `+N` 类型外且非补货物品 → 拒收（sellPrice 下发 0，sell 拒绝）。
- 无 `+N` 行的 NPC 什么类型都收，价 = `Round(Price/2)`。
- 耐久折价（StdMode 40 肉 / 43 矿 / 武器装备按 Dura 折算，
  GetUserItemPrice:1838-1950）仍未做，延续 §39.7 遗留。

### 40.3 验证

| 项目 | 结果 |
|---|---|
| test_pyserver（新增 8 条：村/城品类对比 5 条 + 卖货定价 3 条） | **84/0** |
| test_tick_systems / test_all_messages（离线+live）/ test_map_view | 123/0、31/0、34/0 |
| 8080 重启后 live WS + 死亡重登冒烟 | PASS |

## §41 第 16 轮：肉品质 / 挖矿 / 耐久折旧卖价（2026-11-13）

### 41.1 肉品质（用户：挖出鹿肉有品质，品质高卖得贵）✅

原版逐字核对：
- **品质载体**：``TBaseObject.m_nMeatQuality``（ObjBase.pas:185）。
- **初始化**（UsrEngn.pas:1835-1864，按 race 建类时）：鸡(51) ``Random(3500)+3000``
  → 3000~6500；鹿(52)/狼(53) ``Random(8000)+8000`` → 8000~16000；其余动物 0。
  鹿另有 1/30 概率 ``TChickenDeer`` 变种（10000~30000），本项目未做变种。
- **扣减**：物理被打 ``Struck``（:2807-2811）每次 ``-Random(300)``；
  魔法伤害（:4515-4517）``-nDamage*1000``；绿毒每跳（:4260）``-1000``；
  每挖一刀（ClientGetButchItem:17479-17482）``-Random(201)+100``。
  **打得越狠/拖得越久肉越差**。
- **出肉**：``ApplyMeatQuality``（:20517-20535）把尸体背包里全部
  StdMode 40 物品的 ``Dura := m_nMeatQuality`` → 耐久即品质。

实现：``Monster.meat_quality`` + ``util.init_meat_quality``（两处出生点调用）+
``handlers_combat._attack_monster/_broadcast_magic_hit/_apply_poison`` 扣减 +
``service._butch`` 挖一刀扣品质、出肉写耐久。

### 41.2 挖矿（原版有、本项目没有 → 新实现）✅

原版链路逐字核对：
- 客户端（ClMain.pas:2696）：StdMode 6 武器一律发 ``CM_HEAVYHIT``。
- 服务端（ObjBase.pas:8819-8847）：``CM_HEAVYHIT`` + 武器耐久>0 +
  前方格不可走 + ``Shape=19``（鹤嘴锄）→ ``PileStones`` 并 Exit。
- ``PileStones``（:21881-21925）：前方格需有 ``TStoneMineEvent``（MINE/MINE2
  地图每格都建，svMain.pas:867；创建存量 ``Random(200)``）；每锄存量 -1；
  ``Random(4)=0``（nMakeMineHitRate）命中 → 武器磨损 ``Random(15)+5``；
  命中且 ``Random(12)=0``（nMakeMineRate）→ ``MakeMine``；存量空 10 分钟
  后 ``AddStoneMine`` 补 ``Random(80)``（Event.pas:352）。
- ``MakeMine``（:24351-24474）：``Random(120)`` 定矿种：1~2 金矿 /
  3~20 银矿 / 21~45 铁矿 / 46~56 黑铁矿石 / 其余铜矿；
  ``RandomDrua`` = ``Random(13000)+3000``、1/20 再 ``+Random(10000)``。
  背包满挖不到（:24364）。
- 本项目 MapInfo.txt 24 张 MINE 地图（比奇矿区系），无 MINE2（宝石矿区）。

实现：``world.mine_state``（惰性矿点存量）+ ``world.map_mine_type`` +
``handlers_combat._dir_attack`` 挖矿分支（``_weapon_template/_weapon_dura/
_damage_weapon/_pile_stones/_make_mine``）。前端：点击**相邻**不可走格 →
``dirAttack``（重击方向），远程的墙仍走移动（原版同样被挡）。
碎石堆视觉事件（ET_PILESTONES）本项目无事件渲染层，暂略。

### 41.3 耐久/品质折旧卖价（用户：原版有就补齐）✅

``GetUserItemPrice``（ObjNpc.pas:1838-1919）逐行直译进
``handlers_npc._user_item_price``：
- 肉(40)：耐久=品质；品质 = DuraMax 卖最贵（满补货价 88 → 卖 44），
  偏低打折（3000 → 卖 34），**超过 DuraMax 反而暴跌**（原版 else 分支就是
  减价，照抄）；
- 矿(43)：UserDuraMax 强制 ≥10000，纯度同肉（超上限按 1.3 倍）；
  黑铁矿石纯度 5000 → 卖 374；
- 其余 StdMode>4：升级点数加价段（btValue，本项目无升级 → 跳过）→
  按 DuraMax 比例折算 → 磨损折旧；半磨损木剑（2000/4000）卖 20；
- 肉/矿走完自身分支后**还会再走一遍装备折旧段**（原版三个 if 顺序执行，
  双段折旧是原版行为，照抄）；
- 装备卖价公式：``Round(Round(原值/ DuraMax×ItemDuraMax) − 原/2/DuraMax×磨损)``。

### 41.4 验证

| 项目 | 结果 |
|---|---|
| test_pyserver（+5：肉/矿/装备折旧卖价） | **89/0** |
| test_all_messages（+6：肉品质写入、挖矿出矿/纯度/磨损/存量/补矿；离线+live） | **37/0** |
| test_tick_systems / test_map_view / test_sfx_map | 123/0、34/0、84/0 |
| 8080 重启 + live WS 冒烟 | PASS |

### 41.5 遗留

- 鹿 1/30 ``TChickenDeer`` 变种品质未做；矿石卖价耐久超上限的 1.3 倍分支
  需要纯度 > DuraMax 的矿（RandomDrua 上限 23000 < 30000，实际不可达，
  仅黑铁矿石 DuraMax=30000、纯度最高 23000 恒低于上限）。
- ET_PILESTONES 碎石堆视觉事件未渲染；挖矿无专门重击动画（复用 attack 帧）。
- 武器耐久挖到 0 后不能再挖（原版一致），但无「武器修理」以外的损坏提示。

---

## 42. 可挖尸种族全量 / 配置文本 UTF-8 / 「厚血同模型怪」机制核查（用户第 17 轮）

### 42.1 哪些怪能挖尸体：原版是逐类赋 ``m_boAnimal``，不是按 race 段 ✅

依据 ``UsrEngn.pas:1831-1938`` 的 ``case nMonRace`` 分发 + 各怪物类构造函数
（``ObjMon.pas`` / ``ObjMon2.pas``）。**判定条件**（``ObjBase.pas:17476``）：

```pascal
if BaseObject.m_boDeath and (not BaseObject.m_boSkeleton) and BaseObject.m_boAnimal then
```

| race | 类 | ``m_boAnimal`` | 出处 | 磨损初值 | 肉品质 |
|---|---|---|---|---|---|
| 51 鸡 | TMonster | True | UsrEngn.pas:1838 | 50 | 3000~6499 |
| 52 鹿/羊 | TMonster（1/30 → TChickenDeer） | True | UsrEngn.pas:1846/1853 | 150 | 8000~15999 / 10000~29999 |
| 53 狼 | TATMonster | True | UsrEngn.pas:1861 | 150 | 8000~15999 |
| 82 毒蜘蛛/沙虫/毒蚁/腐尸 | TSpitSpider | True | ObjMon.pas:664 | 50（默认） | 0 |
| 84 蝎子 | TScorpion | True | ObjMon.pas:650 | 50 | 0 |
| 85 食人花 | TStickMonster | True | ObjMon2.pas:166 | 50 | 0 |
| 90 洞蛆 | TGasAttackMonster | True | ObjMon.pas:865 | 50 | 0 |
| 118 天狼蜘蛛/工蚁 | THighRiskSpider | **False** | ObjMon.pas:752 | — | — |
| 119 花吻蜘蛛 | TBigPoisionSpider | **False** | ObjMon.pas:849 | — | — |

本轮补齐：**84 蝎子**、**90 洞蛆** 之前漏了；``ANIMAL_RACES`` 常量 +
``WorldExtra.butch_race / butch_skeleton / base_leathery`` 三个小函数统一口径。
race 52 里的「护卫」(appr 71) 仍按动物 appr 白名单排除（数据混用，非原版行为）。

挖空（``m_nBodyLeathery <= 0``）后的分支也按原版分了两种（``ObjBase.pas:17485``）：

- ``50 <= race < 80``（``RC_ANIMAL..RC_MONSTER``）→ ``m_boSkeleton := True`` +
  ``ApplyMeatQuality``（把品质写进 StdMode 40 肉的耐久）+ ``RM_SKELETON``；
- ``race >= 80``（蜘蛛/蝎子/食人花/洞蛆）→ **不变骷髅**，只 ``TakeBagItems``
  清空尸体背包，尸体原样留着（再挖只有「未发现任何物品！！！」）；
- 两种都把 ``m_nBodyLeathery`` 重置为 **50**（不是回到 150）。

死亡时的磨损初值改为按 race 取（``ANIMAL_LEATHERY``）：鸡 50 / 鹿・狼 150，
所以鹿要挖十几刀才出肉，而以前统一 50（3~10 刀）。尸体背包清空用
``Monster.bag_looted`` 标记（原版 ``TakeBagItems`` 清 ``m_ItemList`` 的等价物），
避免 race≥80 反复挖同一条尸体重复掷骰。

**顺带修的真 bug**：``MonItems`` 里有 5 个文件名带尾随空格
（``洞蛆1 .txt`` / ``蜈蚣1 .txt`` / ``山洞蝙蝠1 .txt`` / ``巨型蠕虫1 .txt`` /
``蜜蜂 .txt``），``path.stem`` 带空格永远匹配不上怪物名 → 这 5 个变种怪此前
**完全没有掉落**。``load_drop_tables`` 已 ``.strip()``。

### 42.2 配置文本兼容 UTF-8 + 一键转换脚本 ✅

``gamedata.read_lines`` 不再写死 GBK，改走 ``decode_text``：BOM UTF-8 →
精确 UTF-8 → UTF-16 BOM → GBK。GBK 的中文双字节序列几乎不可能是合法 UTF-8，
所以「先严格 UTF-8、失败回落 GBK」不会误判；纯 ASCII 两者等价。
服务端所有文本入口（MapInfo / MonGen / MerChant / Npcs / StartPoint / MiniMap /
MapQuest / MonItems / Npc_Def / Market_Def / Notice / Sabuk / UnbindList）都走这一个
函数，改一处即可全兼容；``read_lines(path, encoding=...)`` 仍可强制指定。

一键转换（默认预览，不动文件）：

```bash
D:/Python310/python.exe scripts/convert_gbk_to_utf8.py            # 统计 + 预览
D:/Python310/python.exe scripts/convert_gbk_to_utf8.py --apply     # 转换（自动备份）
D:/Python310/python.exe scripts/convert_gbk_to_utf8.py --check     # CI：有 GBK 退出 1
```

- 只做「GBK 字节 → UTF-8 字节」，换行（``\\r\\n``）与内容一字不改；
- 幂等：已是 UTF-8 / UTF-8 BOM 的文件跳过；两种都解不出的（疑似二进制）跳过并告警；
- ``--apply`` 先把原文件拷到 ``gamedata/.gbk-backup/<时间戳>/``（已在 .gitignore）；
- 当前仓库统计：591 个文本文件里 **576 个 GBK、15 个 UTF-8**。没有整体转换
  （避免 576 个文件的 diff 噪音），需要统一时跑一条命令即可。

### 42.3 「刷出比模板厚很多血、同模型、掉落更好的怪」= 数据变体，不是运行时变异 ✅

原版**没有**任何运行时加血 / 变异 / 精英化的刷怪逻辑，证据：

- ``TUserEngine.MonInitialize``（UsrEngn.pas:2578-2617）按名字 ``CompareText``
  精确查 ``Monster.DB``，HP/AC/DC 一律照抄，**没有** 任何 ``Random`` 放大；
- ``TUserEngine.CreateMonster``（:1931-1938）只按 race 选类，加血的唯一特例是
  沙巴克城门/城墙/守卫（``Castle.pas`` 读配置的固定 HP）；
- 怪物名字颜色 ``m_btNameColor`` 在 ``TBaseObject.Create`` 里恒为 255（白），
  只有玩家（PK/行会/攻城）、宝宝（``SlaveColor[slaveExpLevel]``）、
  狂暴 ``$F9``、神圣 ``$7D`` 会变色（``ObjBase.pas:19197-19202``）。

玩家记忆里那种「同模型、血厚一大截、名字看着不一样、掉落好很多」的怪，是
**Monster.DB 里带数字后缀的独立模板** + ``MonGen.txt`` 直接刷它，掉落表也各自
独立。本仓库数据实例（都在跑）：

| 普通 | HP/掉落 | 变体 | HP/掉落 |
|---|---|---|---|
| 狼 | 48，只有肉 | 狼1 | 120，+战神油 1/1000、传送戒指 1/100 |
| 稻草人 | 25，金币/小金创药 | 稻草人1 | 1000，神水・神秘腰带/戒指/头盔 各 1/6 |
| 天狼蜘蛛 | 260 | 天狼蜘蛛3 | 390，+绿色/狂风/灵魂项链 1/4000~1/8000 |
| 鹿 | 25 | 鹿1 / 鹿10~30 | 1200 / 25（练功用） |

我们早就支持（``spawn_monsters`` 按 ``MonGen`` 的名字精确取模板，
``filter_show_name`` 按原版 ``FilterShowName`` 砍掉数字后缀再下发显示名），
本轮只是**验证 + 补上掉落表漏配**，没有新增非原版机制。

唯一真实存在的「运行时随机变体」是**动物**：``UsrEngn.pas:1844`` 里 race 52
（鹿/羊）有 ``Random(30) = 0`` 的 1/30 概率变成 ``TChickenDeer`` —— 视野 5
（``ObjMon.pas:534``）、见人就跑、**永不攻击**（``Run`` 的逃跑分支跳过
``AttackTarget``，ObjMon.pas:447；目标 ``abs<=6`` 起步，:589）。本轮把它补全：
``Monster.chicken_deer`` + ``init_meat_quality`` 里同时掷这 1/30，
``world._chicken_deer_ai`` 实现「朝背离目标的方向挪一格，走不通试相邻方向」。

### 42.4 验证

| 项目 | 结果 |
|---|---|
| test_pyserver（+6：GBK/UTF-8/BOM 三写一读、转换脚本转换/备份/幂等） | **95/0** |
| test_tick_systems（+16：84/90 可挖、骷髅/非骷髅分支、磨损初值、MonItems 空格名、TChickenDeer 概率与行为、狼/狼1 掉落对比） | **139/0** |
| test_all_messages（+2：race≥80 挖空不变骷髅 / 尸体仍可再挖；离线+live） | **39/0** |
| test_map_view / test_sfx_map | 34/0、84/0 |
| 8080 重启 + live 冒烟 | PASS |

### 42.5 遗留

- ``MonGen.txt`` 有 10 个名字在 ``Monster.csv`` 里没有对应模板（``骷髅精灵`` /
  ``骷髅精灵1`` / ``骷髅精灵9`` / ``骷髅战将`` / ``骷髅战将0`` / ``森林雪人1`` /
  ``祖玛弓箭手100`` / ``血魔兽0`` / ``褐蚂蚱`` / ``巨毒蜘蛛``），这些刷怪行
  目前**刷不出怪**（原版同样查不到就退化成零属性，属于数据缺失，不是代码问题）。
  补数据需要权威 ``Monster.DB`` 数值，没依据不硬填。
- 若希望「厚血变体」在画面上有辨识（名字染色/后缀），那是**非原版**行为，
  等用户明确要求再做；现按原版保持同名同色。

---

## 43. 第三方 DB 对照（用户第 18 轮）：applem2 库 vs 本项目三张表

用户提供另一套私服 DB（`D:\work\code\notmmao_code\notmmao_python\pypxlib\db\mir2-applem2`，
含 `Monster.DB`/`StdItems.DB`/`Magic.DB`）要求导出 CSV 对比。完整报告：
**`docs/DB_COMPARE_APPLEM2.md`**（含逐字段差异表、可补数值、复现命令）。

### 43.1 名词先对齐 ✅

- 原版服务端读的是 `Mud2\DB\Monster.DB`（Paradox，配 `.MB`），入口
  `M2Server\LocalDB.pas:1288 TFrmDB.LoadMonsterDB` → `select * from Monster`。
- **本项目不读 `.DB`**：`gamedata/mud2/DB/` 只有 Monster.csv / StdItems.csv /
  Magic.csv，是 `scripts/paradox_to_csv.py` 从 Paradox 表导出的 CSV（commit `2988350`）。
  即「Monster.DB」在本仓库的等价物 = `gamedata/mud2/DB/Monster.csv`。

### 43.2 工具 ✅

| 文件 | 说明 |
|---|---|
| `scripts/mir2_db_export.py` | 纳入用户提供的 `pypxlib` 版导出器（表头取 DB 自身字段名，可导任意第三方 DB）。依赖 `pip install pypxlib` |
| `scripts/compare_mir2_db.py` | 新增：两套 CSV → Markdown 报告（规模/字段差/同名取值差/名称集合差），`FIELD_ALIAS` 处理 schema 命名差异 |

### 43.3 结论（不做整库替换）✅

| | 本项目 | applem2 |
|---|---|---|
| Monster | 273 行（267 唯一） | 386 行 |
| StdItems | 541 行（532 唯一） | 567 行 |
| Magic | 39 行 | 52 行 |
| 血统 | 1.76/1.80 + 私服占位（`GM*` 11、`New*` 39）+ GM 变体技能 | 1.85（魔龙/雪域/雷炎/火龙、圣战·法神·天尊·雷霆·光芒、逐日剑法/流星火雨） |

- **同源**：191 个同名怪里 **123 个 HP 完全一致**（含沃玛教主 2200），但 **172 个 Exp 不同、
  其中 79 个恰为 3.000 倍**（我们更高）→ 我们的库被逐条调过经验，不是统一倍率。
- **物品库几乎零交集**（共有仅 89/532）：applem2 是开高等级服口径（入门只有乌木剑，
  无木剑/铁剑/青铜剑/短剑/匕首/轻型盔甲、无「金创药(小量)」、无蜡烛/肉），不能互补。
- **applem2 的 `Magic.DB` 不完整**：缺 MagID 1/5/9/23（火球术/大火球/地狱火/爆裂火焰），
  MagID 25 从「半月弯刀」改成「圆月刀法」。
- **`NeedLevel` 位打包坑**：applem2 是 `2293760 = 35<<16`（低 16 位另有标志），
  本项目是纯等级，直接照抄会把需求等级读成 2293760。
- 覆盖度反证：applem2 缺 73 个 MonGen 名字（我们缺 10 个）→ **整库替换会大面积断刷怪**。

### 43.4 唯一可直接收益 ✅（待用户确认后再动数据）

MonGen 的 10 个「有刷怪行、无模板」名字（`world.py:806` 遇 `template is None` 直接
`continue`，一只都不刷），applem2 能补 **4 个**：

| 名字 | 刷怪量 | applem2 数值（Race/RaceImg/Appr/Lvl/Undead/CoolEye/Exp/HP/AC/MAC/DC/DCMax/Speed/Hit/WalkSpeed/AttackSpeed） |
|---|---|---|
| `骷髅精灵` | 10 | 89/14/150/40/1/100/**600**/500/5/4/7/24/15/12/1200/2000 |
| `骷髅精灵1` | 24 | 89/14/150/50/1/100/**600**/800/0/100/0/60/17/20/600/2000 |
| `骷髅战将` | 185 | 89/14/23/20/1/0/**100**/110/2/1/7/13/15/9/1200/2300 |
| `骷髅战将0` | 106 | 同上（与 `骷髅战将` 完全相同） |

校验依据：我们已有的 `骷髅精灵10` 与 applem2 的 `骷髅精灵` **除 Exp/Hit 外逐字段相同**
（Exp 1800 = 600×3、Hit 14 vs 12）→ 血统一致。补入时 Exp 按 ×3 对齐
（骷髅精灵 → 1800 与已有条目完全吻合，骷髅战将 → 300）。

其余 6 个两边都没有：`骷髅精灵9`(20)、`森林雪人1`(1)、`祖玛弓箭手100`(10)、
`血魔兽0`(180)、`褐蚂蚱`(100)、`巨毒蜘蛛`(4)。这 10 个名字的刷怪量合计 640。

---

## 44. 传送两段特效还原（用户第 19 轮）

用户：原版传送/地图切换后人物身上有视觉特效。核查结论与实现如下。

### 44.1 原版机制 ✅

- **过门（``EnterAnotherMap``，ObjBase.pas:20266）只有 ``RM_CHANGEMAP``，没有特效**；
  特效属于 **``TBaseObject.SpaceMove``（ObjBase.pas:4359）**：离开前
  ``RM_SPACEMOVE_FIRE`` → ``SM_SPACEMOVE_HIDE``（客户端 ``TScrollHideEffect.Create(250, 10, x, y)``
  — magic 库 250-259，10 帧 × 30ms 固定在旧坐标格 + 音 ``s_spacemove_out=109``，
  Client\Actor.pas:1635-1644）；到达后 ``SpaceMove`` 内部按 ``nInt`` 发
  ``RM_SPACEMOVE_SHOW``（260）/``SHOW2``（1600）→ ``TCharEffect.Create(..., 10, self)``
  10 帧 × 30ms 跟随角色 + 音 ``s_spacemove_in=110``（Actor.pas:1645-1658）。
- **variant=1（1590/1600）只给瞬息移动技能**（``Magic.pas:959`` ``RM_SPACEMOVE_FIRE2`` +
  ``SpaceMove(..., 1)``）和宝宝跟随（ObjMon.pas:494 等）；随机传送卷/回城卷
  （``EatUseItems`` Shape 1/3）、GM 移动（CmdUserMoveXY/CmdPositionMove/CmdMapMove）、
  NPC 脚本 MOVE（ObjNpc.pas:7836-7837）、回城（MoveToHome）全是 0 号（250/260）。
- 图库为 ``g_WMagicImages = Magic.wil``（本项目 resources/Data/Magic.wil ~1338 帧，
  250-269 / 1590-1609 都在）；``EffectBase := effnum`` 原样用（magiceff.pas:613）。

### 44.2 实现 ✅

| 端 | 改动 |
|---|---|
| `world.py` | 新增 ``space_move(p, map, x, y, variant)``（entityRemove + spaceMoveHide → ``change_map``）与 ``space_move_show(p, variant)``；消息顺序对移动者本人 = hide → changeMap 快照 → show |
| `service.py` | GM ``@移动`` 从 ``change_map`` 换成 ``space_move`` + show（原版 GM 移动命令带特效） |
| `npc_script.py` / `handlers_npc.py` | 脚本 MAPMOVE 走 ``space_move``；``_handle_npc_result`` 的 moved 分支在快照后补 ``space_move_show`` |
| `handlers_combat.py` | 瞬息移动补消失段：``spaceMoveHide`` variant=1（原来只有现身段，且缺 entityRemove 由 hide 消息承担） |
| `PlayScene.mjs` | FX_FRAMES 新增 ``spaceMoveHide(250)/spaceMoveShow(260)/spaceMoveHide2(1590)``，``spaceMove``(1600) 帧时 60ms→**30ms**（原版 NextFrameTime=30，10 帧 300ms）；处理 ``spaceMoveHide/Show``（variant 选 1/2 号特效），旧 ``spaceMove`` 消息保持为 variant=1 现身别名 |
| `SoundManager.mjs` | ``spaceMoveHide`` → 109、``spaceMoveShow`` → 110（109/110.mp3 已存在） |

### 44.3 验证

| 项目 | 结果 |
|---|---|
| test_all_messages（+5：GM 移动两段与顺序、瞬息移动 variant=1 两段、过门无特效静态检查；离线+live） | **46/0** |
| test_pyserver / test_tick_systems / test_map_view / test_sfx_map | 95/0、139/0、34/0、84/0 |
| 8080 重启 + live 冒烟 | PASS |

### 44.4 边界

- 过门依旧**无**特效（与原版一致）。如果实测觉得过门也该有，那是私服口径，需明确要求再加。
- 原版 Show 特效逐帧跟随角色（``TCharEffect.DrawEff`` 取 ``m_nRx+m_nShiftX``）；
  本实现落在落点格（到达后角色暂无位移，等价），未做逐帧跟随。
- ``_random_teleport`` 的落点仍是 ±4 随机近似（原版 ``Magic.pas:495`` 是全图随机），
  属既有行为，本轮未动。

---

## §45 第 20 轮：怪物出场动画（DIGUP）+ 空图休眠/清怪性能优化

### 45.1 原版核查：出场动画 = `RM_DIGUP`（SM_DIGUP=20）

钻出地面的怪不是逐怪硬编码动画，而是统一消息：

| 类 | race | 机制 | 依据 |
|---|---|---|---|
| `TDigOutZombi` | **95**（僵尸2/僵尸20/石墓尸王） | 刷出即 ``m_boFixedHideMode := True`` 埋地；视野内活目标 ``abs<=3`` 才 ComeOut：脚下创建尘土事件（``TEvent`` 5 分钟）+ ``RM_DIGUP``，出土后走普通 AI **不再钻回** | ObjMon.pas:1193-1260 |
| `TStickMonster`（食人花） | 85 | 潜地/出土同样走 RM_DIGUP/RM_DIGDOWN，可反复钻回 | ObjMon2.pas:203/211 |
| `TWhiteSkeleton` / `TScultureMonster` / `TElfMonster` | — | 白骨出生 DIGUP、祖玛石像 MeltStone DIGUP、神兽变身 DIGUP/DIGDOWN | ObjMon.pas:1336/1371/1699 |

客户端表现（ClMain.pas:4970 → Actor.pas:2401 / AxeMon.pas:352）：
- 动画 = **ActDeath 段正向播放**（`ActDeath.start + dir*(frame+skip)` 起，ftime 同 ActDeath），播完回 stand；
- 脚下单帧尘土 = mon6 库 `ZOMBIDIGUPDUSTBASE=420+dir`，**正常混合非光效**（clEvent.pas:105，m_boBlend=FALSE），事件 5 分钟；
- 音 = `m_nAppearSound = 200 + Appr*10`（offset 0，Actor.pas:2345/2401）。

### 45.2 实现 ✅

| 端 | 改动 |
|---|---|
| `world.py` | race 95 刷出/重生同样 `hidden=True`；`_monster_ai` 出土分支（3×3 活玩家 → digUp 广播 + `next_move_at=+1000`）；食人花 ComeOut 从 entityAdd 改 digUp 广播 |
| `PlayScene.mjs` | `digUp` 消息（entity 注册 + `_action='digup'` + 尘土 FX）；尘土分支 mon6 `420+dir` 单帧正常混合、留 5 分钟（对齐服务端事件时长） |
| `ActorSprite.mjs` | `MON_SEG.digup='death'` + 加入一次性动作组（播完回 stand）——ActDeath 正放即原版出土动画 |
| `SoundManager.mjs` | `MON_SOUND_OFFSET.appear=0`；`digUp` → `_monSound(appr,'appear')` |

### 45.3 性能：空图休眠 + 位索引 + 广播零开销（用户第 2 问）

实测基线（12 张大图满怪 = **9725 只**）：tick 一次 **131ms（间隔 200ms → CPU≈65%）**，
热点 = `occupied` 全图线性扫描（45%）+ 空图广播（20%）+ 全怪 AI。

| 优化 | 做法 | 收益 |
|---|---|---|
| **空图 AI 休眠** | `tick` 建 active_maps（有 session 玩家的图），无人图的怪物跳过 `_monster_ai`/`_apply_poison`（尸体过期/重生计时照走） | 无观察者的游走全省 |
| **空图定时清怪** | 最后一名在线玩家离开 > `MON_DESPAWN_MS=5min` → 整图清怪（含尸体/待重生），`mon_spawned=False`；玩家进图 `player_entered()` 重新满编刷出。从未有人进过的图不清（保持首访满编） | 常驻怪量趋近「只有玩家在的图」 |
| **位索引 `by_pos`** | `GameMap` 维护 `(x,y)→[actors]`（add/remove/reposition 三入口），`occupied` O(1) | 消掉最大热点 |
| **玩家索引 `players`** | `broadcast_around` 只遍历图上玩家 dict，空图直接 return 不 encode | 消掉 20% 热点 |

优化后同基线：**无人在线 8.2ms/tick（CPU 4.1%），1 人在线 6.8ms（3.4%）**——降低约 95%。
纯粹是内部优化，无玩家可感知的行为变化（除：离开 5 分钟的图怪被清、再进满编重刷；
登录回到休眠图时怪物是满编新刷而非原地续存）。

### 45.4 验证

| 项目 | 结果 |
|---|---|
| test_all_messages（+4：digUp 触发/出土后不重复/1 秒不动、空图休眠；离线+live） | **50/0** |
| test_pyserver / test_tick_systems / test_map_view / test_sfx_map | 95/0、139/0、34/0、84/0 |
| 清怪→重刷闭环（脚本模拟 60s 离开） | 538 清空 → 进图重刷 538 ✅ |
| 8080 重启 + live 冒烟 | PASS |

### 45.5 边界

- 出土动画用 ActDeath 正放与原版客户端完全同构，但**未逐帧校对** WIL 僵尸段是否含专用出土帧（race 23 白骨走 WMon4 特效帧，未做）。
- race 96（`TZilKinZombi` 复活僵尸：死后减半复活 1~3 次）、祖玛石像 StoneMode→DIGUP、
  `TLightingZombi` 闪电攻击（race 94）均未移植，属后续轮次。
- 清怪时地面物品不清（只按自身过期时间走）；攻城/门状态不受影响。
- `MON_DESPAWN_MS` 写死 5 分钟，未暴露配置。

---

## §46 第 21 轮：race 字段参考文档 + race 96 复活僵尸实作

### 46.1 race 参考文档（用户任务 1）

新增 `docs/RACE_REFERENCE.md`：原版 `UsrEngn.pas:1831 AddBaseObject` 的 `case nMonRace`
分发表（常量 M2Share.pas:143-171）逐条对照本项目实作状态（✅/🟡/❌/➖），含
race 字段三处同名含义（服务端类分发 / 客户端动作表 / 客户端 actor 类）、机制层
横切表（挖肉/骷髅/守卫/远程/毒/变种/埋地/复活）、`Monster.csv` 273 只的实际分布。

### 46.2 race 96 复活僵尸（TZilKinZombi，用户任务 2）

原版（ObjMon.pas:1263-1313）：出生 1/3 概率 `nZilKillCount := Random(3)+1`（1~3 次）；
死亡时 count>0 则安排 `(Random(20)+4)*1000`（4~23 秒）后复活，count 无条件递减；
复活条件 = 未 ghost + count≥0 + 未石化 + 附近有观察者 + 到时；复活 = MaxHP shr 1、
FightExp div 2、HP 拉满、`ReAlive` 广播 RM_ALIVE(10153→SM_ALIVE=27)。

| 端 | 改动 |
|---|---|
| `pyserver/world.py` | `Monster.zil_revives/zil_revive_at`；`_init_race_traits`（spawn/respawn 共用，85/95 hidden + 96 复活次数）；`_tick_zilkin_revive` 挂 tick 主循环 |
| `pyserver/world_extra.py` | `kill_monster`：race 96 死亡安排 revive_at 并递减；respawn_at 顺延到复活窗口后（复活成功取消重生=原版同对象复活；复活不发生则照常补怪，数量守恒） |
| `frontend/scene/PlayScene.mjs` | `case 'alive'`：复位 `_corpse/_dieUntil/skeleton/alive` + 'alive' 一次性动作 |
| `frontend/actor/ActorSprite.mjs` | `MON_SEG.alive='death'`（ActDeath 段正向重播，AxeMon.pas:424 同构） |
| `frontend/audio/SoundManager.mjs` | `alive` → 出场音 `200+Appr*10`（与 digUp 同 offset 0） |

近似点：POISON_STONE 石化未实作（原版复活条件之一，恒通过）；`m_VisibleActors.Count>0`
近似为 SIGHT_RANGE 内有活玩家。

### 46.3 验证

| 项 | 结果 |
|---|---|
| `test_all_messages.py` | 63 PASS / 0 FAIL（新增 12 条：revive_at 窗口/重生顺延/减半/次数耗尽/观察者条件/无观察者不复活） |
| `test_pyserver / test_tick_systems / test_map_view` | 95/139/34 全 PASS |
| 前端三文件 `node --check` | OK |

---

## §47 外部仓库档案与 ddtl-legend 核验（2026-11-13）

本轮无代码改动，登记外部仓库事实，详情与全部证据见 **`docs/DDTL_LEGEND_ANALYSIS.md`**。要点：

- remote 全景：`fork`=`git@github.com:notmmao/jootm2.git`（已推 `feat/python-server`）；`ddtl`=`https://github.com/suyuan168/ddtl-legend`（**无推送权限，只 fetch**，分支 `main` + `archive/rpg-before-3v3-20261111`）。
- ddtl 分叉点 `217e125`，其后 22 提交全部是"打到天亮"原创化（GPT 美术 PNG 图集替代 WIL、地图 445→7、物品/怪物/技能砍量改名、触屏 HUD）；Java 服务端仅动 3 文件 17 行。
- 按「bug 修复 / 原版还原」标准**可借鉴项 = 0**：两处疑似修复（maxExp、商店售价）pyserver 均已有更正确的 Delphi 语义版本（`world.py:1035`、`npc_script.py:423,446-447`）；log 里"对齐原版"系列提交全在分叉点之前，非其工作。
- 客观结论沉淀：256 色限制仅在解码层（`gfx/palette.mjs`/`wil.mjs`）；WIL/WIX 解析全在客户端；Mir2 强绑定同心圆=协议⊃帧号契约⊃地图格式⊃WIL 编码；其 `make-map.mjs` 按原版规范手写 `.map` 并原样通过未改的 Java 解析，反向印证我方 `map_tile_stride()` 实现。
- 音效时间线：基线无音效（`_toggleSound` 是空壳）→ 我方 `fcd36e4` 补齐（仅在本分支）→ ddtl 无音效且 `AUDIO_SPEC.md` 属计划未实施。

---

## §48 第 22 轮：大刀守卫主动打怪/召唤物 + 杀怪经验等级惩罚与组队分配

### 48.1 大刀守卫（用户确认「大刀守卫会攻击怪物和召唤物」属实）

原版 `TSuperGuard`（race 11，`ObjGuard.pas`，继承 `TNormNpc`——与弓箭手的
`TGuardUnit` 是**两套 AI**）：`Run`（:87-126）每攻击冷却扫视野 7，目标 =
`PKLevel>=2` 玩家 或 `race>=RC_MONSTER(80)` 且非任务怪的**任何对象**；
`m_boAttackPet=True` → **玩家召唤物照打**；race 12 `TPetSuperGuard`
`m_boAttackPet=False` 只打无主怪（有主召唤物须先打它才反击）。守卫站桩
（Run 无 Walk 分支）。弓箭守卫（112/20）`TGuardUnit.IsProperTarget`
（ObjMon2.pas:828）不打普通怪，只有红名/反击——维持原状。

| 端 | 改动 |
|---|---|
| `pyserver/util.py` | `SUPER_GUARD_RACES/SUPER_GUARD_VIEW(7)/RED_NAME_PK_POINT(200)`；`calc_get_exp` |
| `pyserver/world.py` | `_resolve_target` 支持返回 Monster（大刀守卫目标集）；`_monster_ai` 守卫打怪结算（roll_attack−怪 AC，kill_monster 不给经验）；守卫有目标**不再追击移动**（站桩）；`_guard_hostile` 红名阈值 `pk_point>0 → ≥200`（原版 PKLevel=PkPoint div 100，ObjBase.pas:2236） |

### 48.2 经验惩罚与组队分配（原版均有，此前未实作）

- `CalcGetExp`（ObjBase.pas:2250-2261）：玩家等级 ≥ 怪等级+10 时
  `exp - Round(exp/15 × 超出级数)`，保底 1；`boHighLevelKillMonFixExp`
  默认 False（M2Share.pas:2141）＝惩罚生效。Delphi Round=银行家舍入＝Python round。
- 组队 `GainExp`（ObjBase.pas:15557-15603）：不是平分！有效成员（同图、存活、
  切比雪夫 ≤12）n>1 时 `exp × bonus[n]`（2 人 1.3 … 11 人 2.2）后按等级比例
  （`dwExp/sumlv×个人等级`）分配。
- 接入点：`handlers_combat._grant_exp`（击杀）、`world.py` 火墙击杀、宝宝击杀
  （原版 20845-20852 主人走 CalcGetExp）。

### 48.3 验证

`test_all_messages` 72/0（+9：惩罚 3/单人/组队/守卫打怪/打召唤物/打红名/race12
不打有主召唤物）；`test_tick_systems` 140/0（旧「守卫打红名」断言按原版口径修正：
pk_point=1 黄名不打、≥200 才打）；`test_pyserver` 95/0；`test_map_view` 34/0。

---

## §49 第 23 轮：昼夜图标答疑 + 骨架状态下发 + 仓库全链路 + 寻路对齐 GotoTargetXY

用户四项反馈：①面板右侧一直月牙是否有昼夜；②挖肉骨架重进地图变回尸体；
③仓库无法存放；④寻路容易卡住。

### 49.1 昼夜（无 bug，答疑）

- 原版 `FrnEngn.pas:85-91` 分档：5-10/16-22 点=白天(图标12太阳)、11/23=傍晚(13
  橙太阳)、4/15=早上(15 蓝底月牙)、**0-3 与 12-14 点=晚上(14 月牙)**——
  原版怪癖：中午 12-14 点也显示月牙。`day_bright_for_hour` 与图标映射
  `[15,12,13,14][b]`（FState.pas:3764-3768）逐值一致，22 点下发 1=太阳。
  实测确认无 bug；用户看到月牙大概率是 0-3 点时段或正午怪癖。

### 49.2 骨架状态下发（bug 修复）

- 根因：`Monster.brief_json` 只发 `corpse` 不发 `skeleton`，entityAdd 走
  「未挖尸体」分支画 ActDie 末帧。前端 ActorSprite 本来就按 `e.skeleton`
  选 ActDeath.start（GetDefaultFrame，Actor.pas:2124）。
- 修复：brief_json 增 `o["skeleton"]`；前端无需改。

### 49.3 仓库全链路（bug 修复：前端无 UI、脚本引擎无标签）

- 原版链路：脚本 `@storage`（ObjNpc.pas:1560 → SM_SENDUSERSTORAGEITEM 开
  dmStorage 投放框，点背包物品直接 CM_USERSTORAGEITEM 存入）/
  `@getback`（→ SM_SAVEITEMLIST 开 BoStorageMenu 列表，行点击
  CM_USERTAKEBACKSTORAGEITEM 取回）。容量 **39**（ObjBase.pas:24720），
  要求仓库 NPC 同图 15 格内。
- 服务端：`Result.storage_mode`；`_npc_select` 识别 @storage/@getback（标签
  不存在自动回 main，普通 NPC 开不出仓库）；`_handle_npc_result` 发
  `storageOpen` / `storageList`；`_storage_store` 容量 45→39 + `_storage_npc_ok`
  同图 15 格校验；`_storage_take` 成功后重发 storageList 刷新窗口。
- 前端：`_showStorageDlg`（复用卖物窗口外观，点背包物品直接发 storageStore，
  对齐 FState.pas:5454 dmStorage 分支）；`_showStorageList`（复用买物菜单
  外观，行点击发 storageTake）；`_closeShopPanel` 复位 storageMode。

### 49.4 寻路对齐（bug 修复：单方向卡死）

- 根因：追击/跟随只 `try_move(朝目标方向)`，被树/墙/怪挡住就原地站桩。
  原版 `TAnimalObject.GotoTargetXY`（ObjBase.pas:2709-2759）被挡时按
  `n20=Random(3)`（2/3 顺时针 / 1/3 逆时针）依次重试相邻方向最多 7 次，
  每 tick 最多走一格。
- 修复：新增 `GameWorld._walk_toward`，替换四处：近战追击、远程追击、
  召唤物追敌/跟随主人。乱走分支对齐 `Wondering`（ObjBase.pas:22723-22728）：
  每 walk tick 5% 才动，1/4 只转向、其余沿当前方向直走（此前是 35% 每次
  随机换向，怪走得乱且从不走直线）。

### 49.5 验证

`test_all_messages` 78/0（+6：仓库闭环 4 + 骨架下发 2）；`test_pyserver`
95/0；`test_tick_systems` 140/0；`test_map_view` 34/0；node --check 通过。

---

## §50 第 24 轮实测反馈：攻击转向 / 升级特效 / 刷怪不可见（2026-11-13）

用户反馈三项（续第 23 轮四项后的 5/6/7 号问题），全部对齐原版修复。

### 50.1 怪物对打轻微位移不转向（对齐 SM_HIT 朝向）

- 原版依据：`SM_HIT` 每条携带 `wParam = m_btDirection`（ObjBase.pas:5322-5327）；
  怪物每次出手 `TMonster.AttackTarget → GetAttackDir` 重新算朝向并
  `Attack(m_TargetCret, btDir)`（ObjMon.pas:383-400），玩家轻微位移后下一刀
  必面向新位置。此前服务端攻击广播不带 dir、前端也不改朝向 → 怪物永远
  面向旧方向，只有拉开距离触发走路才转向。
- 修复：`world.py` 四处怪物攻击广播补 `"dir"`（守卫打怪 / 近战空挥 /
  食人花空挥 / `_send_hits` 通用命中）；前端 `attack`/`flyAxe` 消息应用
  `msg.dir`。原版范围内冷却未到时**不转向**（原地站到下一刀），保持一致。

### 50.2 升级视觉特效（此前只有声音）

- 原版依据：`SM_LEVELUP → DrawEffectHum(8)`（ClMain.pas:4825-4832）：
  `NewMagic(self, -1, 999, mtExplosion)` + `s_meltstone`。magnumb=999 分支
  （PlayScn.pas:1538-1546）用人物选择场景解除石化的光特效：
  `g_WChrSelImages`、`MagExplosionBase=4`、`ExplosionFrame=14`、
  `NextFrameTime=80`（即 ChrSel.wil 帧 4~17，实测 292×350）。
- 修复：前端 `stats` 消息按自身 level 跳变触发 `_spawnLevelupFx`（chrsel
  帧 4 起 14 帧 × 80ms，lighter 混合，角色格中心居中、人形锚点上移 20px）。
  原版绘制坐标 `m_nTargetX+260/-55` 依赖投掷目标残留值（未投掷恒 0，特效
  落屏幕左上），改为以角色为中心。音效 s_meltstone 此前已在 SoundManager。

### 50.3 刷怪到身边站着看不见、一动才出现（根因修复）

- 原版依据：`SM_WALK / SM_RUN / SM_TURN` 每条都携带 `TCharDesc.feature+status`
  （ObjBase.pas:5292-5312 / 5430-5460），客户端对未知 id **现场创建 actor**
  （PlayScn.SendMsg → FindActor 找不到就 NewActor）。
- 本项目断点：`update_sight`（增量 entityAdd/entityRemove）只在玩家自己移动
  时跑；怪从视野外（>12 格）走进来时玩家只收得到 move，而 move 处理器
  `if (e)` 直接忽略未知 id → 怪已贴身砍人（挨打音效正常，SoundManager 全局
  播 attack 音）但实体不存在，玩家一动才被 update_sight 补发 entityAdd。
- 修复：`broadcast_move` 携带 `"entity": brief_json()`（对齐原版 move 带 feature
  语义）；前端 move 处理器对未知 id 先建实体（跳过自己）再套用移动。
  守卫类站桩怪不追击，出生即广播 entityAdd，不受此影响。

### 50.4 验证

`test_tick_systems` 144/0（+4：攻击朝向 3 + move 实体快照 1）；
`test_all_messages` 78/0；`test_pyserver` 95/0；`test_map_view` 34/0；
node --check 通过。合计 351 PASS。

---

## §51 仓库交互流程对齐原版（第 25 轮）

用户反馈：保管（存入）窗口点一下背包物品就直接存了，原版应先把物品放进
对话框下方的**圈**（DSellDlgSpot）里，再点 **OK** 才提交（类似卖东西）。

### 51.1 原版依据（FState.pas / ClMain.pas）

- 窗口即卖出窗口的 dmStorage 模式：`ClientGetSendUserStorage`（ClMain.pas:
  6245-6248）→ `CloseDSellDlg; SpotDlgMode := dmStorage; ShowShopSellDlg`
  → 窗口 (260,170)、背包 (475,0)、标题 `'保管物品:'`（FState.pas:5378）。
- 圈：`DSellDlgSpot (27,67) 61x52`（FState.pas:1443-1446），物品图标绘制见
  `DSellDlgSpotDirectPaint`（5425-5439）。
- 点背包物品 = 拿起（`DItemGridGridSelect`，FState.pas:4747-4797；放入圈内后
  背包格清空）。
- OK 按钮 `(85,150)` 图 386 系（FState.pas:1435-1437 `DSellDlgOk`）→
  `DSellDlgOkClick`（5447-5459）才 `SendStorageItem`；关窗 `CloseDSellDlg`
  （4984-4990）把圈内物品 `AddItemBag` 放回。
- 取回列表同理：点行只 **选中**（`DMenuDlgClick` 设 MenuIndex，5104-5168），
  点 OK(386 @215,171) 才 `SendTakeBackStorageItem`（`DMenuBuyClick`，5223-5239）。

### 51.2 修复

- `_showStorageDlg` 重写：标题「保管物品:」、圈 (27,67) 61x52、OK 热区
  `(85,150)`（prguse 393 按下态）＋关闭 (115,0)；新增 `storagePending`
  暂存、`_renderStorageSpot` 画图标、`_confirmStorageDeposit` 才发
  `storageStore`；点圈可取回暂存（清 `storagePending` 并刷新背包）；
  `_refreshBag` 把暂存物品从网格里过滤掉（原版移入圈后 g_ItemArr 清空）；
  `_closeShopPanel` 清暂存 → 物品自然回到背包。
- `_showStorageList` 重写：标题「保管物品 / 持久」（27 / 170，对齐
  DMenuDlgDirectPaint）、行点击只选中（红色 ▶）、OK(386 @215,171) 才发
  `storageTake`；原来「点行即取回」。
- 失败兜底：OK 后先本地刷新背包（服务端成功随后用 bag 更新移除；仓库满/
  超距时物品保持可见，对齐原版失败分支 AddItemBag）。

### 51.3 验证（Playwright 无头实测，脚本用完即删）

打开窗口标题「保管物品:」+ OK/圈齐全 → 点背包物品：暂存进圈、背包格 -1、
**未发** storageStore → 点 OK：发 1 条 storageStore 且暂存清空 → 点圈：物品
回背包、暂存清空。取回列表：点行只选中（未发 storageTake）→ 点 OK 发
storageTake 取选中项。截图核对标题/列/按钮无错位。回归脚本未改（纯前端
交互），`node --check` 通过。

---

## 52. 命中判定对齐原版（40 级打鸡还 MISS）

**现象**：GM 调级到 40 的角色打鸡仍频繁 MISS，怀疑命中有 bug。

**原版依据（ObjBase.pas:22240-22247 `TBaseObject._Attack`）**

```pascal
if AttackTarget.m_btHitPoint > 0 then            // 目标命中点 > 0 才可能被闪避
  if m_btHitPoint < Random(AttackTarget.m_btSpeedPoint) then
    nPower := 0;                                 // MISS
```

即命中率 = `(min(命中点+1, 敏捷)) / 敏捷`（命中点 ≥ 敏捷-1 必中）。
点数来源：

| 角色 | 命中点 m_btHitPoint | 敏捷点 m_btSpeedPoint |
|---|---|---|
| 玩家 | `DEFHIT(5) + 装备准确(1:1) + 剑术技能加成` | `DEFSPEED(15) + 装备敏捷(1:1)`，道士 +3 |
| 怪物 | `Monster.DB.Hit`（UsrEngn.pas:2606） | `Monster.DB.Speed`（UsrEngn.pas:2605） |

- 装备准确/敏捷走 `m_AddAbil`（ItmUnit.pas `ApplyItemParameters` 里 **原始 1:1 累加**），
  只有「升级可分配加点」`m_BonusAbil` 才按 `BonusTick.Hit/Speed`（战士 20/35、
  法师 25/35、道士 30/30）折算——本项目没有加点系统，故玩家点数 = 基础 + 装备 + 技能。
- 技能加成（`RecalcHitSpeed`，ObjBase.pas:18590-18632）：基本剑术 `9/3*lv`、
  精神力战法 `8/3*lv`、攻杀剑术 `3/3*lv`，直接加到命中点。
- **等级不加命中**——这就是「40 级仍 MISS」的原因：鸡敏捷 10、裸装角色命中点 5，
  命中率 60%，原版也是如此；有准确装备或基本剑术 3 级（+9 → 命中 14）即 100%。

**修复**

1. 旧实现是自造公式 `命中 15+准确` vs `闪避 8+怪物敏捷` 再归一化，把鸡的闪避
   放大成 18、把玩家的准确按 1:1 计入命中基数 → 裸装角色打鸡仅约 45% 命中。
   现改为 `world.hit_point / speed_point / melee_hit / positive_hit`
   （后者对应原版 `DirectAttack` 与怪物远程的 `Random(敏捷) < 命中` 正向写法），
   删除 `handlers_combat.hit_check` 里的自造数值。
2. 补齐缺失的判定点：怪物近战 / 食人花 / 远程飞矢 / 宝宝攻击 / 大刀守卫打怪、
   玩家 PvP（此前玩家打玩家完全不掷命中）、刺杀与半月副目标（逐个目标单独判定）。
3. 未命中不再给玩家发「攻击未命中。」系统提示（原版没有该提示），只广播
   `attack(miss)` 供客户端飘 MISS；广播同样带 `dir`（对齐 SM_HIT 的 wParam）。
4. 目标命中点为 0（NPC 类 / Monster.DB Hit=0 的怪）时永远命中，保留原版门槛。

**客户端 / 服务端职责**：命中与伤害**全部在服务端**（`handlers_combat.py +
world.py`）；客户端只发 `attack { targetId }` 意图、播挥击动画、按服务端下发的
`damage` 飘数字，不参与任何掷骰。

### 52.1 验证

`test_tick_systems` 157/0（新增 §[13] 命中判定 13 条：点数组成、必中边界、
40%/27% 命中率区间、命中点 0 门槛、正向写法 90%、`Random(0)` 安全）；
`test_all_messages` 78/0；`test_pyserver` 95/0；`test_map_view` 34/0。
顺带修了两个测试夹具：食人花果实应为「食人树果实」（1/5 掉落偶尔触发旧断言失败）、
守卫假模板补 `hit/speed` 字段。

---

## 53. 僵尸出土洞口改回地面层（盖住人物）

**现象**：僵尸从地下钻出来后留下一个洞口/土堆，它会盖在人物身上；应当和地面、
尸体一样在角色之下。

**原版依据**

- 洞口/土堆 = 客户端场景事件 `TClEvent`，类型 `ET_DIGOUTZOMBI`，图
  `WMon6Img[ZOMBIDIGUPDUSTBASE(420) + dir]`（clEvent.pas:14/105），单帧、
  `m_boBlend` 保持默认 `FALSE`（普通混合，不是加色光效）。
- 绘制位置在 **`for j` 行循环内部**：`if j = evn.m_nY - BlockTop then
  evn.DrawEvent(...)`（PlayScn.pas:1259-1266），顺序是「同行地砖/大对象 →
  事件 → 掉落物 → 角色」，所以角色站在洞口上会盖住它，后一行的树/城墙也会。
  锚点 = 格子左上角 + 图自带 px/py（`Draw(ax + m_nPx, ay + m_nPy)`），不做格中心换算。
- 生命周期：M2 `TDigOutZombi.sub_4AA8DC` 建 `TEvent.Create(..., 1, 5*60*1000, True)`
  （ObjMon.pas:1213），客户端留 5 分钟。

**修复**

- `MapActor.render()` 新增 `drawEvent / groundEvents` 两个参数：按 `ev.y` 分桶，
  在每行大对象画完之后、掉落物与实体之前回调绘制（`_eventsByRow` 每帧复用）。
- `PlayScene` 每帧把 `effects` 里的 `digDust` 收进 `_groundEvents` 传给 MapActor；
  `_renderEffects` 的 `digDust` 分支只保留保活（5 分钟），不再绘制。
- 新增 `PlayScene._drawGroundEvent()`：普通混合、格左上 + px/py，与原版一致。

**验证**（Playwright 无头，独立临时账号，未打扰在线会话）

- 打桩 `_drawEntity` / `_drawGroundEvent` 记录一帧内调用顺序：
  `["ev","ent","ent",...]` → 洞口确实先于实体；
- 截图差分：脚下的洞口像素被画出来（变化 3162 px，bbox 落在格子周围），
  角色躯干区域在「有/无洞口」两张图里完全一致 → 没被盖住；
- `node --check` 两个文件通过。临时账号 / 脚本 / 截图已清理。

> 注：原版只在登出/换服时 `EventMan.ClearEvents`（ClMain.pas:3231/3273），
> 换图时不清理事件，洞口会跟着坐标留在新地图上——这里**照原版保留**，未额外清理。

---

## 54. 复活僵尸击杀经验仍按原始值（m_dwFightExp 读错来源）

**现象**：race 96 复活僵尸复活后血量确实减半，但杀掉拿到的经验还是原始值。

**原版依据（ObjMon.pas:1296-1313 `TZilKinZombi.Run`）**

```pascal
m_Abil.MaxHP := m_Abil.MaxHP shr 1;
m_dwFightExp := m_dwFightExp div 2;   // 经验也减半，且是**实例**字段
m_Abil.HP := m_Abil.MaxHP;
ReAlive();
```

`m_dwFightExp` 在 MonInitialize 时从 `Monster.DB.Exp` 拷进**怪物实例**，之后只有
复活僵尸会改它；杀怪结算 `CalcGetExp` 拿的也是这个实例值。

**根因**：复活逻辑里 `m.exp = m.exp // 2` 自己做对了，但三处经验结算读的是
`m.template.exp`（模板值），于是减半结果从来没人读。

**修复**

- `Monster.__post_init__`：出生把 `template.exp` 写进实例 `exp`（与原版
  MonInitialize 一致），所有构造点统一生效，不会再出现漏赋值结算成 0。
- 经验结算改读实例值：`handlers_combat._grant_exp`（玩家击杀）、火墙击杀
  （world.py `_tick_fire_walls`）、宝宝击杀（`_summon_ai`）。
- 复活时无条件 `m.exp //= 2`（与原版一致，0 也照样除）。

**验证**

`test_all_messages` 80/0（+2：「出生即把模板 Exp 写入实例」「复活僵尸击杀经验取
实例值 200→100 而非模板 200」）；`test_tick_systems` 157/0；`test_pyserver` 95/0；
`test_map_view` 34/0。8080 已重启（旧连接会被断开，需重新登录）。

---

## 55. 技能书学习后技能栏不出现（第 29 轮）

**现象**：买一本「攻杀剑术」书双击使用，书消失了、聊天框也有提示，但 F11 技能窗 /
状态窗技能页里没有新技能，重登才看得到。

**原版依据**

```pascal
// M2Server/ObjBase.pas:23443-23477 TPlayObject.ReadBook
Magic := UserEngine.FindMagic(StdItem.Name);          // 按**物品名**查魔法
if not IsTrainingSkill(Magic.wMagicId) then
  if (Magic.btJob = 99) or (Magic.btJob = m_btJob) then
    if m_Abil.Level >= Magic.TrainLevel[0] then begin
      ...
      m_MagicList.Add(UserMagic);
      RecalcAbilitys();
      PlayObject.SendAddMagic(UserMagic);             // ← SM_ADDMAGIC 主动推送
      Result := True;
    end;
// 23504: MakeDefaultMsg(SM_ADDMAGIC, ...) + TClientMagic 结构体下发
```

注意原版**没有任何「学会了 XXX」文字提示**——玩家看到的反馈就是技能出现在技能栏。

**根因**：`handlers_items._learn_skill_book` 只做了 `p.skills[magId] = {...}` + `bagUpdate`，
既没有 `send_stats`（本项目的 SM_ADDMAGIC 等价物是 stats 的 `skills` 全量快照），
也没 `recompute`。前端只在 `msg.skills !== undefined` 时才更新 `this.self.skills`
（PlayScene.mjs:1220），于是学习结果只在内存里，UI 要等重登（`to_player_json` 带 skills）才刷新。

**修复**

- `_learn_skill_book`：学会后 `p.recompute(w)`（原版 RecalcAbilitys，被动技能命中/攻杀加成
  立刻生效）+ `w.send_stats(p, {"skills": p.skills_json(w)})`；顺带补 `clear_quick_ref`。
- 同类漏发：`handlers_combat._finish_cast` 技能修炼升级（`skill["level"] = lv + 1`）时
  同样带 `skills` 下发（原版 10172 训练成功也 SendAddMagic），否则技能页等级永远停在 0。

**验证**

- 离线：`test_all_messages` 83/0（+3：学习后进入技能列表 / 书本离开背包 / 下发 stats.skills）。
  其余 `test_tick_systems` 157/0、`test_pyserver` 95/0、`test_map_view` 34/0。
- live WS（8080）：临时战士号 level 30 背包塞攻杀剑术 → `useItem` 后收到
  `stats.skills = {"3":基本剑术, "7":攻杀剑术}`。
- Playwright 端到端（无头，脚本用完即删）：登录后 `self.skills` 只有 `["3"]`，
  双击背包里的书后立刻变成 `{"3":…,"7":攻杀剑术}`，F11 技能窗截图里两行技能都在。

**未做（原版额外行为，留作后续）**：原版 `EatItems` StdMode 4 分支在学会刺杀/半月后
会自动 `ThrustingOnOff(True)` / `HalfMoonOnOff(True)` 并下发 `'+LNG'` / `'+WID'` 开关状态；
本项目暂未实现自动开启，需要时按 ObjBase.pas:17377-17391 补。

---

## 56. 被动技能（基本剑术 / 攻杀剑术）的修炼与快捷键（第 30 轮）

**现象**：用户问「基本剑术、攻杀剑术怎么用、怎么升级？他好像不能像道士法师那样
添加快捷键」。查下来是**两件事**：① 被动技能从来不涨修炼点（永远 0 级）；
② 我们自造了「被动技能不能绑键」的限制（原版允许绑）。

**原版依据**

```pascal
// ① 不能主动施放：DoSpell 对战士技能直接返回（ObjBase.pas:21859）
if not MagicManager.IsWarrSkill(UserMagic.wMagIdx) then ... // 3/4/7/12/25/26/27 都在表里
// ② 修炼：命中造成伤害之后（ObjBase.pas:22283-22312）
if (m_MagicOneSwordSkill <> nil) and (btLevel < 3)
   and (MagicInfo.TrainLevel[btLevel] <= m_Abil.Level) then
  TrainSkill(m_MagicOneSwordSkill, Random(3)+1);          // 基本剑术：每次命中 1~3 点
if bo21 and (m_MagicPowerHitSkill <> nil) and (btLevel < 3) ... then
  TrainSkill(m_MagicPowerHitSkill, Random(3)+1);          // 攻杀：只在攻杀发动那一击
// ③ 升级：CheckMagicLevelup（ObjBase.pas:21825）：TrainLevel[btLevel] 是**人物等级**门槛，
//    MaxTrain[btLevel] 是需要的点数，够了 btLevel+1 并扣点
// ④ 攻杀是自动触发，不需要按键（ObjBase.pas:8862-8875）：
Dec(m_btAttackSkillCount);
if m_btAttackSkillPointCount = m_btAttackSkillCount then begin m_boPowerHit := True; SendSocket('+PWR'); end;
if m_btAttackSkillCount <= 0 then begin
  m_btAttackSkillCount := 7 - m_MagicPowerHitSkill.btLevel;
  m_btAttackSkillPointCount := Random(m_btAttackSkillCount);
end;
// 客户端收到 '+PWR' 置 g_boNextTimePowerHit（ClMain.pas:4197），下次攻击改发 CM_POWERHIT
// ⑤ 绑键：服务端 ClientChangeMagicKey（17506-17520）不校验技能类型；
//    客户端 DStMag1Click（FState.pas:3671-3701）只处理「键被别的技能占用」，不过滤被动
// ⑥ 技能页显示 CurTrain/MaxTrain（FState.pas:3114-3118，满 3 级显示 '-'）
```

**修复**

- `handlers_combat`：新增 `_can_train` / `_train_skill`（原版 TrainSkill +
  CheckMagicLevelup，升级时 `send_system` + `send_stats(skills)`）/
  `_train_sword_skills`（基本剑术每次命中 1~3 点；攻杀只在 `sword_hit in (26, 7)`
  即 bo21 那一击 1~3 点）。接到 `_attack_monster`、`_attack_player`、两个刺杀
  long-hit 的主目标；副目标（半月/刺杀扫到的）**不**加点，与原版一致。
- `_finish_cast` 改为复用 `_train_skill`（顺手修掉提示里 `level+1` 的 off-by-one，
  文案改回原版「XXX 的 YYY 技能修炼成功！！！」）。
- `world.skills_json` 增加 `train` / `maxTrain`（对应 TClientMagic.CurTrain +
  Magic.Def.MaxTrain）；未升级时按 1 秒节流下发 skills（原版是
  `SendDelayMsg(RM_MAGIC_LVEXP, 3000)` 推 CurTrain），否则玩家看不到进度在涨。
- `handlers_items._magic_key_change`：删掉「被动技能无法设置快捷键」的自造拒绝。
- 前端 `_refreshSkillPage`：等级右侧显示 `train/maxTrain`（满级显示 `-`）；
  被动技能图标恢复可点击设键、已绑键位正常显示；去掉自造的「未设置」文字
  （原版 `if keyimg > 0` 才画，没绑键就是空白，且会与进度文字重叠）。

**数值（Magic.DB，人物等级门槛 / 所需点数）**

| 技能 | 0→1 | 1→2 | 2→3 |
|---|---|---|---|
| 基本剑术(3) | 7 级 / 10 点 | 11 级 / 20 点 | 16 级 / 30 点 |
| 攻杀剑术(7) | 19 级 / 50 点 | 22 级 / 80 点 | 24 级 / 120 点 |

攻杀每次发动 +1~3 点，发动间隔 = `7 - 等级` 刀随机一次（0 级约 7 刀一次），
所以 0→1 平均三四百刀，符合原版手感。

**验证**

- `test_all_messages` 91/0（+8：命中加基本剑术点 / 攻杀发动加点 / 未发动不加 /
  满点升级 / 升级下发 skills / 等级不足不加点 / 被动技能可绑键 / 绑键下发 skills）；
  `test_tick_systems` 157/0、`test_pyserver` 95/0、`test_map_view` 34/0。
- live WS（8080）：40 级战士走到鸡旁边砍一刀 → 收到 `stats.skills` 里
  `基本剑术 train=2/10`。
- Playwright：技能页显示「基本剑术 0 2/10 [F3]」「攻杀剑术 0 5/50」，无文字重叠。

**说明（给玩家/后续）**：这几个技能绑了键按下去**没有效果**——原版客户端会发
`CM_SPELL`，服务端 `DoSpell` 对战士技能直接 return。真正起作用的是：
基本剑术常驻加命中点（3×等级）、攻杀发动时该刀伤害 +5+等级、精神力战法加命中点
（8/3×等级，原版只能 GM 升级，`_Attack` 里没有它的加点分支）。

---

## 57. 骷髅召唤的动画/跟图 + 死后仍被怪物围砍（第 31 轮）

用户反馈两条：`12. 骷髅召唤有没有动画? 召唤的骷髅会随着我一起切换地图么?`
`13. 死亡后, 人物角色还站在那被怪物打.`

### 12-a 召唤有没有「特效动画」——原版没有

- 召唤魔法 17/30 在 Magic.DB 里是 `EffectType=4 / Effect=15(28)`。4 = `mtFireWind`，
  客户端 `TPlayScene.NewMagic` 的 `mtFireWind:` 分支直接 `meff := nil`
  （MirClient\PlayScn.pas:1679-1680）——**原版召唤不放任何魔法特效**。
- 原版只有两件事：① 服务端 `SendRefMsg(RM_SPELL, Effect, tx, ty, MagicId)`
  （M2Server\Magic.pas:270）→ 客户端播道士施法动作（`HA.ActSpell`）；
  ② `MakeSlave` 把骷髅放到身前（见下）。本项目 `spellCast` 广播（magicId=17、
  effect=15）已能让所有人看到施法动作，`MAGIC_VFX` 里**故意没有** 17/30 的条目，
  与原版一致（别再加特效）。
- 顺带修正落点：`spawn_summon` 原来是 `_free_near(owner, 半径 2)` 随机撒，
  原版 `TBaseObject.MakeSlave`（ObjBase.pas:7167-7168）先 `GetFrontPosition`
  再 `RegenMonsterByName`，**召唤物永远在主人面朝的正前方一格**。新增
  `GameWorld._front_near`（正前方优先，被占/不可走才退回 `_free_near`）。

### 12-b 骷髅跟不跟着换图——原版跟着，我们以前留在原地

- 依据 `TSlave.Run`（ObjMon.pas:468-497）：每轮检查
  `(m_PEnvir <> m_Master.m_PEnvir) or (abs(dx) > 20) or (abs(dy) > 20)`
  → `SpaceMove(m_Master.m_PEnvir.sMapName, m_nTargetX, m_nTargetY, 1)`，
  即**瞬移跟随**（`nInt=1` → `RM_SPACEMOVE_SHOW2`，magic 1600，10 帧×30ms；
  离开时 `RM_SPACEMOVE_FIRE` → `SM_SPACEMOVE_HIDE2`，magic 1590）。
- 旧实现 `_summon_ai` 开头 `master.map is not m.map → return`，主人一过门骷髅就
  被丢在旧图（而且空图不跑 AI，永远追不回来，最终被 `_despawn_idle_maps` 清掉）。
- 修复：
  - `_summon_ai`：同图但拉开 >20 格、或主人换图 → `_slave_space_move`；
  - 新增 `_slave_space_move`：旧位置广播 `spaceMoveHide{variant:1}`（换图再加
    `entityRemove`），落点用 `_back_near`（原版 `GetBackPosition` 背后一格，
    被占则按 1→8 环逐圈找可走空格），新位置 `entityAdd` + `spaceMoveShow{variant:1}`；
  - `change_map` 末尾调用新增的 `_follow_summons(p)`：主人过门/回城卷/瞬息移动后
    宝宝立刻跟过去（原版是等下一次 `TSlave.Run`，但本项目空图不跑 AI）；
  - `remove_player` 顺手清宝宝（原版主人 `m_boGhost` 1 秒后 `MakeGhost`，
    ObjBase.pas:3986）：以前登出后骷髅永久留在原地。

### 13 死了还站着被怪砍——`alive` 没跟着 `dead` 一起清

- 原版是**两套东西**：`m_boDeath`（死亡标志，目标筛选全用它）与对象是否还在
  地图上无关。`TAnimalObject.SearchTarget` 遍历视野时 `if not BaseObject.m_boDeath`
  （ObjBase.pas:22677），`TBaseObject.Run` 每轮把尸体目标置 nil（3884-3895）。
- 本项目 `alive` 才是目标筛选用的旗标（`_resolve_target` 只认 `p.alive`、
  `nearby()` 只返回 `alive`），而 `kill_player` 以前只置 `p.dead = True`，
  `alive` 保持 True → 怪物继续把尸体当活目标，围上来一刀刀砍。
- 修复：
  - `kill_player` 置 `p.alive = False`（同时清毒/回血池）；
  - `_apply_hit_to_player` 开头 `if not target.alive: return`（延迟飞矢落地时兜底）；
  - 尸体可见性补到 `_corpse_visible`：玩家死亡**不倒计时**（原版只有重登才回
    出生点），`_sight_actors` 继续把尸体下发给视野，避免「死的一瞬间人消失」；
  - `Player.brief_json` 增加 `alive` / `corpse`：别人后进视野也能按 `corpse`
    画 ActDie 末帧（以前缺这个字段，后进视野的玩家会看到一具**站着**的尸体）；
  - 登录复活（`service.py`，`UsrEngn.pas:576-600` 的 HP=14 分支）同时复位
    `p.alive = True`，否则复活后永远不能被选为目标。

**回归**

- `test_tick_systems` 166/0（+9：死亡同时清 alive / 死后不再被锁定 / 尸体仍在
  视野 / brief_json corpse 标记 / 召唤落点正前方 / 宝宝跟主人换图 / 拉开 >20 格
  瞬移 / 主人登出清理宝宝；另修了夹具「上一步死亡状态没复位」的连带失败）；
  `test_all_messages` 91/0、`test_pyserver` 95/0、`test_map_view` 34/0。
- live WS（8080，40 级道士带 17 级满级召唤）：`castMagic {magicId:17}` →
  收到 `spellCast{casterId, magicId:17, effect:15}` 与
  `entityAdd{name:"变异骷髅", x, y}`，落点与主人 `dir=4` 的正前方
  (289,619) 完全一致，随后 `systemMessage「召唤了 变异骷髅。」`。

**给后续**：宝宝「休息/攻击」切换（`m_boSlaveRelax`，`CmdChangeSalveStatus`）
本项目还没做，`_summon_ai` 里没有 relax 分支；召唤走位仍是最简跟随，
原版 `TSlave.Run` 的 `GetBackPosition` 目标点 + `GotoTargetXY` 只做了瞬移部分。

---

## §52 多 gamedata 树并存 + 生效目录切换（1.76 实验）（2026-11-14）

**需求**：基于 ruchu8 站/REDM2 交叉验证过的 1.76 数值做实验。用户明确要求**多份 gamedata 目录并存，切换只改生效目录**，不动主树文件。

**现成机制免改后端**：`pyserver/config.py` 本就支持 `gamedata.root` 配置项（server.properties / 环境变量 JOOTM2_GAMEDATA / `--gamedata` 三通道），所有消费点都走 `config.gamedata_root/envir_dir/mud2_dir/map_dir` 派生属性。

**新增** `scripts/switch_data.py`：
- 布局：主树 `gamedata/`（git 跟踪，切换时**永不改动**）+ 副本树 `data/gamedata/<name>/`（**入库分发**：.gitignore 改 `data/*` + `!data/gamedata/` 白名单，`*/Mir200/Map/` 与 `.gbk-backup/` 排除，整树 ~2MB/525 文件）；生效指针 = server.properties **两行**：`gamedata.root=` + `db.file=`。
- `build-176`：主树整树复制（ignore Map 与 .gbk-backup）→ `Mir200/Map` 建 **junction 指回 resources/Map**；CSV 用 REDM2.DB 合并（同名覆盖、**本地 Idx 保留**、1.76 独有插入：物品 +121、怪 +196、技能 +0）；Envir 五件套（MonGen/Npcs/MerChant + MonItems/ + Market_Def/）照抄 176-mir2-red 包；其余文件同主树。
- `use <name|main>`：两行指针一起换——main: `gamedata` + `data/jootm2.db`；176: `data/gamedata/176` + **`data/jootm2-176.db`（实验账号/数据与主库完全隔离，用户约定 2026-11-14）**+ 清该库三张模板表（`db.py::seed_game_data` 只在空表导入，不清表切换不生效）+ 副本树缺 Map junction 自动补建（git clone 后必触发，junction 不入 git）。
- `list`：树清单 + 生效树/db。

**坑（都实测踩过/防住）**：①CSV 合并切片少写 Name 列致整行左移——`_csv_write` 现带列数断言；②写 properties 丢 CRLF/EOF 无换行风格 → 整文件 diff，现按原文件换行风格写回，diff 精确 2 行；③删除副本树必须 junction-aware。**Python 3.10 Windows 实测**：junction 的 `is_symlink()`/`os.path.islink()` 都是 False、`entry.is_junction()` 不存在（3.12+ 才有）、`is_dir(follow_symlinks=False)=True` 会穿透——唯一可靠判定是 `os.lstat(p).st_file_attributes & 0x400`（REPARSE 位），命中就只 `rmdir` 摘链接，绝不 rmtree 穿透 resources/Map（铁律 2）。早先一次 rmdir FileNotFoundError 其实是递归内外双重 rmdir，现容忍；④176 包 MerChant 脚本列含 `传送员\传送员` 反斜杠——**副本树只能本机 Windows 用，别把它部署进 Docker/Linux**；⑤176 包 MapInfo 有幻境/生肖私服垃圾，故 Envir 五件套不含 MapInfo（地图属性/名字表仍用主树）；⑥主树 `.gbk-backup/`（转换备份）会被整树复制带进副本——build ignore 掉 + gitignore 排除。

**验证**：生效树路径正确、Map junction 加载正常（map=0 700x700 spawn 2651）、0 Traceback；`test_pyserver --live` **102 PASS/0 FAIL**、smoke_ws 全通；数值抽查 1.76 口径（僵尸1 Exp 160 vs 本地 480、木剑 Price 50 vs 91）；`use main` 往返后 git diff 归零；176 用独立 `jootm2-176.db`，注册新号不碰主库。

**当前状态**：`use 176` 生效（树 + `data/jootm2-176.db`）；回私服 = `python scripts/switch_data.py use main`。**8080 常驻服务重启前仍是旧数据**（模板启动时进内存），重启即进 1.76 世界。部署镜像前确认指针在 main。

---

## §53 33 个技能数值/判定全面对齐原版（第 32 轮，2026-11-14）

**需求**：用户要求「检查下其他技能和原版的差异，然后还原」——把 33 个技能（魔法 + 剑术）
的数值、判定闸门、MP/护身符消耗、状态效果逐条对回 `M2Server/Magic.pas` +
`ObjBase.pas`，删掉 Java 版遗留的自造规则。

### 施法链路（两层，别只看一层）

| 层 | 原版位置 | 本项目 |
|---|---|---|
| `ClientSpellXY` | ObjBase.pas:9029-9241 | `_cast_magic` 的开关/蓄力分支 |
| `DoSpell` → `GetSpellPoint` → `MagicManager.DoSpell` | ObjBase.pas:21852 / Magic.pas:216 | `_cast_magic` 主路径 + `_resolve_magic` |

- **3 基本剑术 / 4 精神力战法 / 7 攻杀剑术**：`IsWarrSkill`（Magic.pas:208）直接 exit，
  不扣 MP、无效果，只回一句「被动技能无需施放」，不产生 `spellCast`。
- **12 刺杀 / 25 半月**：只切开关，不扣 MP（`ThrustingOnOff` / `HalfMoonOnOff`）。
- **26 烈火**（10 秒冷却）/ **27 野蛮冲撞**（3 秒冷却）：**先判冷却再扣 MP**；27 成功时
  才 `TrainSkill(Random(3)+1)`，门槛是 `TrainLevel[lv] **<** 人物等级`（严格小于，
  与 Magic.pas:722 施法修炼的 `<=` 不同）。
- 其余：先扣 MP → 判 `abs(dx)>8 或 abs(dy)>8`（超距 **MP 照扣**、直接返回）→ 转向
  （`broadcast_move`）→ `CretInNearXY` 3x3 闸门（不满足则 `target=nil` 当空地施法）→
  广播 `spellCast` → 结算 → `_finish_cast`（`boTrain` 才加点）。

### 数值函数（重写，全部对齐 Delphi 语义）

- `mpow(t) = wPower + Random(wMaxPower - wPower)`（**不含上界**）。
- `get_power(t,lv,n) = ROUND(n/(btTrainLv+1)*(btLevel+1)) + btDefPower + Random(btDefMaxPower-btDefPower)`，
  `btTrainLv` 恒 3（LocalDB.pas:382 写死）。
- `get_power13(t,lv,n)`：1/3 固定 + 2/3 随等级放大（`GetPower13`）。
- `get_rpow(lo,hi)` = MC/SC 区间随机；`attack_power` = `base + Random(rng+1)`，
  幸运 >0 时 `Random(10-luck)=0` 取上界、诅咒取底（`GetAttackPower`）。
- **魔法命中闸门** `Random(10) >= m_nAntiMagic`（怪物恒 1 → 90%，玩家项链魔法躲避可降）；
  **MAC 只减伤不闪避**（旧实现 `1+MAC//5` 几率闪避是自造，已删）。
- `mag_struck_damage()`（ObjBase.pas:22441 `GetMagStruckDamage`）统一收口：先扣 MAC
  （玩家按区间随机），再过魔法盾 `Round(dmg/100*(lv+2)*8)`。物理侧**不再**受魔法盾影响
  （旧代码 `_apply_hit_to_player` 统一 ×0.7 已删）。
- `RM_DELAYMAGIC`（4565）：只对 `race >= RC_ANIMAL(50)` 折 `Round(pwr/1.2)` 再走
  `RM_MAGSTRUCK`；玩家 race=0 不吃这条。

### 逐技能还原要点

| 技能 | 还原内容 |
|---|---|
| 1 火球 / 5 大火球 | `magic_hits` 闸门 + `RM_DELAYMAGIC`；大火球 `MagHbFireBall` 击退（`Random(20) < 6+lv*3+等级差` 且 `Random(lv)-1 > 0`） |
| 2 治愈术 | 目标默认自己；满血不 `boTrain`；治疗进 `inc_healing` 池（每 tick 5 点） |
| 6 施毒术 | 毒药粉 1 张（Shape<=2）；`Random(AntiPoison+7) <= 6` 才生效；Shape1=绿毒掉血 / Shape2=红毒降防；点数 `ROUND(lv/3*(n/10))` |
| 8 抗拒火环 | 1 格内、`等级 > 目标等级`、`Random(20) < 6+lv*3+等级差`、`push=1+max(0,lv-1)+Random(2)` |
| 9 地狱火 / 10 疾光电影 | 穿透 13 步（5 / 8 格终点）；疾光 `undeadattack` 的 ×1.5 在**原版是每命中累乘**（照抄） |
| 11 雷电术 | 只有它对不死系 ×1.5；`Random(10) >= AntiMagic` |
| 13 灵魂火符 | **先扣 1 张护身符再判命中**；走 SC；无不死系加成 |
| 14 幽灵盾 / 15 神圣战甲 | 各要 1 张护身符；半径 3；`GetAttackPower(GetPower13(60)+SC下限*10, SC区间+1)`；AC/MAC **上限** `+2+等级 div 7`（14=MAC、15=AC，`btState` 1/0） |
| 16 困魔咒 | 1 张护身符；3x3 内 `Random(4)+等级-1 > 目标等级` 且无主；**任一失败整体 Result=0**（已定住的不解除，原版同款）；时长 `GetPower13(40)+GetRPow(SC)*3` |
| 17 骷髅 / 30 神兽 | 护身符 1 / 5 张；先清旧召唤再召 |
| 18 隐身 / 19 集体隐身 | 1 张护身符；先把 9 格内以我为目标的怪清仇恨（距离>1 或 50%）；时长 `GetPower13(30)+GetRPow(SC)*3` |
| 20 诱惑之光 | `Random(4-lv)=0` 才进入；`Random(2)=0` 尝试诱惑、另一半「定住」`Random(lv*5+10)` 秒；概率 `1/n14`（`MaxHP div 100`，<=2 取 2，否则 ×2）；失败 `Random(14)=0` 打死 |
| 21 瞬息移动 | `Random(11) < lv*2+4`；落 **家地图**随机点（边距 2/20/50，`MapRandomMove(m_sHomeMap,1)`），特效 variant=1 |
| 22 火墙 | 十字 5 格；伤害 `GetAttackPower(GetPower(MPow)+MC下限, MC区间+1)`；时长 `GetPower(10)+(GetRPow(MC) shr 1)` |
| 23 爆裂 / 33 冰咆哮 | `MagBigExplosion` 半径 `nFireBoomRage/nSnowWindRange=1` → 3x3 |
| 24 地狱雷光 | 自身半径 `nElecBlizzardRange=2`；**非不死系只有 pwr div 10** |
| 28 心灵启示 | `Random(6) <= lv+3`；时长 `GetPower13(GetRPow(SC)*2+30)`；下发 `openHealth` 给视野 |
| 29 群体治疗 | 目标点 3x3 队友，治疗进池 |
| 31 魔法盾 | 已开不叠加；时长 `GetPower(GetRPow(MC)+15)`；记 `shield_level` 决定折减系数 |
| 32 圣言术 | 只对不死系；`Random(2)+等级-1 > 目标等级`、`目标等级<50`、`Random(100) < lv*8-lv+15+等级差` 秒杀（HP=0，非伤害）；**失败返 False 不涨点** |

### 服务端其它配套改动

- `world.py`：`Actor.open_hp_until`（`brief_json.openHealth`）、`Player.shield_level`、
  新增模块级 `mag_struck_damage()`（火墙/技能共用）、`_tick_fire_walls` 改 3 秒一跳且
  首跳立即（`m_dwRunTick=500`）、火墙伤害改扣 MAC、删掉物理侧的魔法盾 0.7 折减。
- 剑术修炼点（`_train_sword_skills`）：基本剑术每次命中 `Random(3)+1`；攻杀只在
  `bo21` 那击；刺杀/半月/烈火每次命中 `+1`；副目标不加。
- `_attack_player` 的剑术加成（`m_nHitPlus` / `m_nHitDouble`）改到**扣 AC 之前**
  （`Inc(nPower, ...)` 在原版位于 `_Attack` 的破防计算前）。
- 删掉自造内容：`_splash_magic`、`_poison_shape`、「恢复了 X 点生命」「XXX 血量 x/y」
  之类提示、`is_projectile_magic` / `is_area_magic` 两个伪分类。

### 前端

- `PlayScene.mjs` 新增 `openHealth` 分支（写实体 `openHealth`/`hp`/`maxHp`）。
- `MapActor.mjs` 头顶血条加闸门：`SHOW_RED_HP_LABEL || e.isSelf || e.openHealth > now`
  （原版 `DrawScrn.pas:305-307` 的 `g_boShowRedHPLable`，客户端设置缺省 False；
  这里默认开，`localStorage['jootm2.showHpLabel']='0'` 可切回原版缺省行为）。

### 回归

- `test_all_messages` 91/0（33 个技能分支 0 异常）+ `test_tick_systems` 167/2 +
  `test_pyserver` 87/0 + `test_map_view` 34/0。
- `test_tick_systems` 剩 2 个失败是 **176 数据 profile** 下 MonItems 目录差异
  （「MonItems 尾随空格」「狼1 掉落表」），与代码无关，`switch_data.py use current` 后自愈。
- 测试脚本同步修正三处夹具：护身符/毒药粉装备（原版只认已装备槽，不给就永远走不到
  伤害分支）、挖肉循环 12→40 刀（鹿 `m_nBodyLeathery=150`，每刀 5~20）、守卫测试先清
  上一段的骷髅（守卫「锁定扫描到的第一只」）。

### 已知未做（留给后续）

- `IsProperTarget` 的 PK/阵营筛选：火墙、穿透、抗拒、群疗目前只对怪物生效，没有对敌对
  玩家的判定（本项目 PvP 本身也还没有命中判定的完整链路）。
- 诱惑之光成功后原版还有 `OpenCrazyMode` 狂怒分支、宠物攻速/移速封顶
  （`1500-lv*200` / `2000-lv*200`）、叛变计时 `m_dwMasterRoyaltyTick`。
- 圣言术命中后原版会让「无目标」的不死系逃跑 10 秒（`m_boRunAwayMode`）。
- 14/15 的 buff 在原版是 `STATE_DEFENCEUP` / `STATE_MAGDEFENCEUP` 两个独立计时，
  本项目仍共用一个 `ac_buff_until`。

---

## §54 176 树 Gee 脚本方言兼容 + MonGen/MonItems 原版口径重构（2026-11-14）

**用户实测反馈四连**：176 树 NPC 无对话（上一轮已修 #CALL）→ 本轮：购买列表空、对话以 `{` 开头、爆率明显高、刷怪明显多；追问 176 文件来源。**来源结论**：CSV 数值=REDM2.DB（与 ruchu8 站逐字段 100% 互证，可信）；Envir 玩法层（MonGen/MonItems/NPC 脚本）=176-mir2-red 包——Gee 引擎私服发行（GMGJX.COM，幻境/生肖/会员痕迹），**不是原版**：MonItems 爆率分母 1/1=1172、1/2=1773 行 vs 主树 778/784；MonGen 0 系变体堆怪（钉耙猫王 21 vs 站点 1、多钩猫0 173 vs 11）。ruchu8 站从未作为数据源，只是对照基线。

**脚本方言修复**（`pyserver/npc_script.py`，三处）：
1. **parse 跳过 Gee 文本块标记**：Gee 对话块用独立 `{` 行开始、`}` 行结束（Delphi 原版无），此前落入 say_text 致对话首尾带花括号——`if line in ("{", "}"): continue`。
2. **goods() 子脚本回退**：主城商店主脚本（`Market_Def/比奇城/比奇城_武器-0103.txt`）只有 `[@main] #CALL [商人\武器店.txt] @武器店`，`[goods]` 在 QuestDiary 子脚本里——`npc.script.goods_rows()` 为空时遍历 `_called_subs(script)` 取第一个有货的子脚本，**price_rate 也随该子脚本取**。
3. 上一轮的 #CALL 全套（parse 收编进 IfStmt.actions、`@SUB\t` 带内信号切脚本、`_load_sub` 走 `config.envir_dir/QuestDiary` 带缓存；`Npc` 是 slots dataclass 不能挂运行时属性，故用带内信号而非实例属性）。注意 `world_extra.goods()` 每次新建 engine 实例，`_subs` 缓存不跨请求——正确但重复读盘，商店对话量小未优化。

**数据重构（用户选型②）**：新增 `scripts/build_176_envir.py`，把 176 树失真最重的两层换成 ruchu8 站原版口径，NPC 脚本保留 Gee 包：
- `MonGen.txt` ← `data/db_compare/ruchu8/site_spawn.csv`（Mob_*.html 刷怪表，轮9 抓的）：站点中文图名→本地 MapInfo 图号（精确/ALIAS/difflib 前缀约束），多候选用主树 MonGen 出现频次选图号；range 取主树同怪众数缺省 50；**站点未覆盖地图回退主树行**。结果 4262 行（站点 2916 + 主树回退 1349），总量 64216 只 vs 176 包 28696/主树 37428（原版口径本就密）。
- `MonItems/` ← 新抓 `site_drop.csv`（10441 行/206 怪，`data/db_compare/r10_drop_extract.py`）：站点把多槽位列成重复 `1/1 X` 行，**同怪同品 1/1 合并为 `1/1 X k`**（数学等价，1/1 从 1498 降到 797 且剩余全来自主树回退文件）；站点未覆盖怪回退主树掉落文件（176 包 Gee 爆率文件整体作废）。结果 206 站点 + 103 回退 = 309 文件。
- 丢弃口径：17 个站点图名本地无图（尸魔洞/骨魔洞/钳虫巢穴等，66 图资源集没有）+ 宝箱1-7 等无模板怪；物品名查无 5 种 275 行保留原样（服务端查无模板自然不掉）。报告 `data/db_compare/r10_build_envir.txt`。

**验证**：桩测（`data/db_compare/r10_shop_stub.py`）比奇城_武器：say 无花括号、#CALL 拿到完整对话、goods 12 件、价 = StdItems.Price×1.1（木剑 50→55，对齐 1.76 玩家实测）。回归：**176 指针** test_pyserver 95/0、test_tick_systems 169/0、test_all_messages --live 8099 91/0；`use main` 复跑 95/0、169/0 后切回 176。8099 服务日志 spawn/299 掉落表/165 NPC 正常。观感（爆率/密度）仍需用户自测确认。

**当前状态**：已提交 `d0ff237`。**阿里云部署的是 main 版**（用户 176 实验仍在本地，构建前 `use main` 切指针、推完 ACR 立即切回 176——容器不带 data/ 目录，指针若留 176 会构建出引用不存在树的坏镜像）：`/api/version`=d0ff237、`/api/status` 200。本地 `use 176` 生效中；8080 常驻服务需重启 + 浏览器 Ctrl+F5 才吃到 176 新数据。

---

## §55 传奇数据库静态站点生成器（2026-11-15）

**需求**：仿 ruchu8 做一个数据查询站，纯静态 HTML、数据变化一键重生成；内容覆盖技能（按职业）/物品（武器·装备·消耗品·书·杂项）/怪物属性/怪物分布/地图/掉落；补图标。

**新增四个脚本**（全部纯标准库，不入运行时）：
- `scripts/gen_website.py`：一键生成。数据源 = `--tree` 指定的 gamedata 树（默认读 `gamedata.root` 生效指针），产物默认 `data/website/`（gitignore 内）。1017 个页面：index（全局检索）/items（分类 tab+搜索+排序）/skills（战法道分组）/monsters/每怪详情（分布+掉落）/maps/每图详情（渲染图+刷怪+NPC+出入口）/npcs/每个 NPC 详情（位置+售卖商品）/dist（怪物分布总表）/drops（按怪+按物双向）。列表页客户端过滤（`assets/app.js`，无框架）。
- `scripts/website_assets.py`：CSS/JS/页面外壳常量。
- `scripts/wil_extract.py`：WIL/WIX→PNG 纯 Python 移植（照抄 `frontend/gfx/wil.mjs` 的帧解码：8 位索引色·共享 256 色调色板 / 16 位 RGB565，调色板从 `frontend/gfx/palette.mjs` 的 base64 读单一数据源）。
- `scripts/render_maps.py`：`.map` → PNG 地形渲染图 + 怪物刷新点标记（见下节），也可独立 CLI 批量/单张渲染。

**图标四条来源**（全部本地生成，**不再借用 ruchu8 任何图片**；用户明确其地图带水印）：
- 物品：`Items.wil` 帧号 = StdItems `Looks`。427 种 Looks 提取出 367（60 个空帧/1x1 占位，页面显示灰格）。
- 技能：`MagIcon.wil` 帧号 = **`Effect × 2`**（不是 MagId！依据 Delphi 客户端 `MirClient/FState.pas:3658` `icon := pm.Def.btEffect * 2`）。32 种 Effect 全提取。
- 怪物：按客户端同款规则取站姿帧——`getMonImgLib(appr)` 选 MonN.wil、`getMonOffset(appr)` 偏移、stand 段 `dir=4` 首帧（动作表与 `RACE_ACTION` 在脚本里直接正则解析 `frontend/actor/actorData.mjs`，避免手抄漂移）。141 种 Appr 提取出 118；缺的 23 个（appr 60/64/75-79/85/87/94-99/104-108/167-169）是本地缺对应 Mon WIL 帧，页面显示占位。
- NPC：`npc.wil` 站姿帧 = `getNpcOffset(appr)` + `npcAction(appr)`（appr 23→MA51，24/25→MA52，其余 MA50）stand dir=4；`NPC_PLACEHOLDER_APPR` 集合同样从 actorData.mjs 解析并回退 appr=0。24 种 Appr 全提取。

**地图渲染图（`render_maps.py`，对齐 Delphi MapUnit/PlayScn）**：
- 数据布局：`.map` tile 是**列优先** `i = x*height + y`（`MapUnit.pas:182` `FileSeek(52 + x*height*12 + y*12)`），PNG 是行优先——首版把列优先数组直接当行优先输出，整张图转置（比奇省海水从西南跑到东北，用户第一轮就发现了）。
- 大图（区域/洞穴，w*h>4000）：1 像素/格彩块近似。背景 `wBkImg & 0x7FFF - 1` → `Tiles.wil`（**仅偶数 i,j**，`PlayScn.pas:562-564`）；`wMidImg` → `SmTiles.wil`、`wFrImg` → `GetObjs(btArea)`（`MShare.pas:783`）**每格都画**（`PlayScn.pas:586/1143`）。颜色 = 整帧 α 加权平均色；按「原始值→颜色」查表后再跑主循环（49 万格约 4s，否则 8 分钟）。
- 小图（室内/房间，w*h≤4000）：**逐帧真实合成**，输出长边约 1400px。Tiles 帧 96×64 画在 (x*48,y*32)；SmTiles 48×32 画在 (x*48,y*32)；Objects 帧**底边对齐格底** (x*48, (y+1)*32 - 帧高)（`PlayScn.pas:1170` `mmm := m + UNITY - Height`），按 y 行序绘制。正确性靠游戏内实拍核对：衣服店 0106 / 药店 0108 / 皇宫 0122 的截图与渲染图一致（家具、地板、栏杆全部对位）。性能技巧：帧像素先拆「不透明连续段 + 半透明散点」，段用切片拷贝；`wil_extract.frame_rgba` 的 8 位索引色改用整行 `str.translate`（C 层）+ 每帧缓存。
- 刷新点：MonGen 行 → 怪物图标（站姿 dir=4，透明 PNG），地图标记加 1px 黑色描边（α=0xC8）；站点表格图标用 `halo=False`。图标尺寸 = `min(18, max(10, min(w,h)//50))`（小图按画布自适应）。
- 数据缺文件（用户第三问「杂货店 8000」）：MapInfo 登记 445 张图，其中 **71 张在客户端资源里没有 .map**（8000~系列租屋、T000x、部分 1.80 地图等）。这是资源包本身缺文件，不是站点引用错误：服务端 Warp 到这些图同样没有地形。站点在列表里标「缺 .map」并在说明里点明数量，详情页显示「缺少 .map 文件，无法渲染」。
- 性能：176 树 374 张有 .map 的图全量渲染约 3 分钟；`--skip-existing-maps` 复用已有 PNG（重建页面约 4s）。比奇省渲染图与官方小地图 `mmap.wil` 帧（MiniMap.txt 1-based，比奇省=第 101 项）按 70×70 网格做 8 种朝向匹配：正确朝向平均色差 18.5，其余朝向 ≥30，用于确认方向修复。

**NPC 与售卖物**：数据源 `Npcs.txt`/`MerChant.txt`（列义同 `world_extra.load_npc_spawns`），同名的多个坐标合并为一个 NPC 页。商品直接调服务端 `pyserver.npc_script.load_script(Market_Def/Npc_Def, script, map)` + `NpcScriptEngine.goods`（含 Gee `#CALL` 子脚本回退），所以价格与游戏内完全同链路：`Round(Price×1.1)` 再乘脚本倍率（`ObjNpc.pas CheckItemPrice/GetUserPrice`）。实测 165 条 NPC 记录 → 101 个 NPC，61 个有 `[goods]`，共 587 条商品。

**CSS 坑（本轮修复，影响全站所有表格）**：`table{overflow:hidden}` 会让表格成为 sticky 的滚动容器，粘性表头被下推 48px 并盖住首行（Chrome 实测：`th` top 应为 208 实为 256，首行永远看不见）。去掉 `overflow:hidden`、给首/末 `th` 单独加圆角即可；`thead th` 的 `position:sticky;top:48px` 保留。

**数据口径要点**：StdItems 分类按 StdMode（5/6=武器；10..26,52..54=装备；0..3,7,31,40=消耗品；4=书；其余杂项，字典在脚本顶部）；药水类 Ac/Mac 是**回血/回魔值**不是防御（首版误显示"防 20-0"已修）；需求列 Need 0=等级/1=攻击/2=魔法/3=道术（对齐 `handlers_items.py:268-277` 原版语义）；技能 Job 0/1/2=战/法/道。地图尺寸读 resources/Map/*.map 52 字节头。

**验证**：176 树生成 662 物品/39 技能/469 怪/445 图/4262 刷怪行/299 掉落表/165 NPC/101 NPC 页，1017 页 17MB；浏览器实测 index/items/skills/monsters/maps/map-detail/npcs/npc-detail/mon/dist/drops 页面截图正常；比奇省渲染图 700×700 与官方 mmap.wil 小地图逐格比对朝向一致（海水西南、河流南北），室内图与游戏内实拍一致；怪物标记（鹿/鸡等）透明背景+描边可辨认；71 张缺 .map 的地图标红提示；NPC「罗家铺子老板」21 件商品价格核对（金创药(小量) 88=80×1.1、轻型盔甲 3300=3000×1.1）。搜索+分类 tab 联合过滤（items 搜"剑"→16 行，叠"装备"→0 行符合预期）。176 数据里 17 个站点图名对应的洞窟（尸魔洞/骨魔洞等）本图集没有，属预期。

**用法**：`python scripts/gen_website.py`（当前生效树，默认全量重渲染地图）· `--skip-existing-maps`（复用已渲染图，快速重建页面）· `--tree gamedata`（私服主树）· `--out website --open`。单独渲染地图：`python scripts/render_maps.py --only 0 --out /tmp/maps`。

---

## §56 升级特效位置修正 + 战士被动技能修炼核查（2026-11-15）

**用户反馈两问**：① 人物升级特效对不对、位置是不是不太对；② 战士基本剑术/攻杀/刺杀的等级怎么提升。

### ① 升级特效：资源与时序都对，位置错了（已修）

对照本地 Delphi 源码逐项核对，特效本身 **没有问题**：
- `ClMain.pas:4825-4833` `SM_LEVELUP → DrawEffectHum(8)`；`ClMain.pas:6731-6735` `NewMagic(g_MySelf,-1,999,...,mtExplosion,False,80)` + `PlaySound(s_meltstone)`。
- `PlayScn.pas:1538-1546` 999 分支：`ImgLib=g_WChrSelImages`、`MagExplosionBase=4`、`NextFrameTime=80`、`ExplosionFrame=14`、一次性不重复、加色混合（`TMagicEff.DrawEff` 用 `DrawBlend`）。
- 项目实现 `PlayScene.mjs::_spawnLevelupFx` / `_renderEffects` 的 chrsel 帧 4 起 14 帧 × 80ms + `lighter`、按自身 `stats.level` 跳变触发、`SoundManager` 同步播 meltStone，帧号/时长/混合/音效/一次性全部对得上。

**位置错在**：旧实现按「图片几何中心对齐格中心再上移 20px」绘制（`- tex.height/2 - 20`）。292×350 的光效图里光柱下缘在 y≈300、扇形在 y≈250~350，这样算出来图片底部落在角色脚底下方约 155px —— 无头实测截图显示角色站在光柱中段，光柱和扇形一直穿到脚下很远，像扎进地面。

**权威定位依据**：选角场景 `IntroScn.pas:1485-1541` 里光效图与人物立绘几乎同左上覆盖（第一个角色 `ex-bx=19`、`ey-by=6`，第二个角色同样是 +19/+6）；WIL 实测 ChrSel 光效帧 292×350、人物立绘帧 60 是 300×360，两者同尺寸，人物脚底正好在立绘 y≈350 —— 即**光效图底部 = 人物脚底**。原版 999 分支把 `m_nTargetX+260 / m_nTargetY-55`（投掷目标格残留值，未投掷恒 0）当屏幕坐标传给 `TMagicEff.Create`，是私服作者 2020 年加的坏实现，不该照抄。

**改动**（`frontend/scene/PlayScene.mjs`）：levelup 分支 `sy` 改为 `screenY(y) + 16 - tex.height`，`sx` 保持 `screenX(x) + 24 - tex.width/2`；WIL 自带 `px/py=7/-44` 是选角场景占位锚点，继续不套用。等效把光效整体上移 155px，光柱下端接角色头顶、扇形包住全身。

**验证**：`D:/Python39/python.exe` Playwright 无头连 8080，GM `@等级` 触发跳变抓 16 帧，修复前扇形在角色脚下 ~155px、修复后正好罩住角色；测试后已把角色等级恢复为 4。`scripts/test_all_messages.py` 91 PASS / 0 FAIL。

### ② 战士三个被动技能：机制正确，无需改动（说明用）

对照 `M2Server/ObjBase.pas:21817-21850`（`TrainSkill` / `CheckMagicLevelup`）、`22283-22365`（`_Attack` 加点）、`Magic.pas:722-728`，项目 `handlers_combat.py::_can_train / _train_skill / _train_sword_skills` 口径一致：
- **加点只在命中后**：基本剑术(3) 每次物理命中 `Random(3)+1`；攻杀(7) 只在 `bo21`（攻杀/烈火发动那一击）`Random(3)+1`；刺杀(12)/半月(25)/烈火(26) 每次命中 `+1`；刺杀/半月扫到的副目标不加。
- **双重门槛**：`btLevel < 3` 且 `TrainLevel[btLevel] <= 人物等级`（`needLevel` 数组）才开始累积；点数到 `MaxTrain[btLevel]` 即扣点升级，并下发 `stats.skills` 让技能页 `train/maxTrain` 与等级同步刷新。命中加成 `world.py::hit_point` 实时读 `skills[].level`，升级即刻生效。
- 176 生效库实测数据：基本剑术 人物 7/11/16 级 · 100/400/800 点；攻杀 19/22/24 级 · 400/800/1600 点；刺杀 25/27/29 级 · 600/1200/1800 点。
- 三个都属 `IsWarrSkill`，按 F 键施放本来无效；攻杀是服务端每 `7-lv` 刀随机发动，刺杀需开关（`castMagic 12` 切换，登录时若已学会会自动开启）。
- **用户角色当前 4 级、只学会基本剑术**：低于 7 级门槛时打怪**一点都不会涨**，这是原版数据口径不是 bug；攻杀/刺杀还需先学技能书（学书本身也卡 `needLevel[0]` = 19/25 级）。

---

## §57 在线托管 AutoPilot：计划定稿 + 步骤 0 半开连接修复（2026-11-15）

- **需求定位（用户明确）**：托管是「在线代打」，不是离线挂机；登出 / 退出角色 / 断线立即停止，不 24h 离线升级。本期砍掉 LLM / 密钥 / sidecar、断线自动重连、每小时经验上限、加速时钟与教练台；策略用管理员预置默认档，任何手动操作自动暂停。计划全文 `docs/AUTOPILOT-PLAN.md`（≤50 行）。
- **硬约束**：AutoPilot 挂 WsSession / Player 内存对象，**绝不落库**；停止点唯一 = `service.py:197 on_disconnect`（登出 `_logout:562` / 退回选角 `_back_to_select:571` / 断线 `net.py:516 finally` 全部汇合）。可持久化「选了哪一档」偏好，不可持久化「托管中」运行状态；重登必须玩家显式再开。
- **本轮修复**：原 `net.py:_ws_loop` 无主动探活、无读超时，TCP 半开（手机息屏 / 切 Wi-Fi / 直接关页面）时 `on_disconnect` 不触发，服务端以为人还在线，「断线即停」是假的。现加 `WS_PING_INTERVAL=15s` 主动 ping（浏览器 WebSocket 原生自动回 pong，**前端零改动**）、`WS_IDLE_TIMEOUT=45s` 连续无帧即 `return` 走 finally 清场、`WS_FRAME_TIMEOUT=10s` 防半帧挂死；三者支持环境变量覆盖（`JOOTM2_WS_*`，便于测试与调优）。帧体 `readexactly` 统一走 `asyncio.wait_for`，超时直接放弃连接以免 StreamReader 错位。
- **验证**：新增 `scripts/test_ws_idle_timeout.py`（动态空闲端口 + 独立临时库，避免撞本机已有实例），短超时 1s/3s 起服务：登录后静默 5s → 在线列表清空、连接被关；对照「会回 pong 的客户端」5s 后仍在线，证明正常在线 / 未来托管不会被误踢。`test_all_messages.py` 91 PASS / 0 FAIL，`py_compile` 与 `git diff --check` 通过。
- 改动文件：`pyserver/net.py`、`scripts/test_ws_idle_timeout.py`、`docs/AUTOPILOT-PLAN.md`、本文 §57。步骤 1（默认策略档 + AutoPilot 内核）/ 2（游戏内开关 + HUD）待实施。

---

## §58 在线托管 AutoPilot：功能主体完成（2026-11-15 夜）

- **新增 `pyserver/autopilot.py`**（约 400 行，纯标准库）：三档默认策略 `安全/均衡/效率`（血线、危险度、拾取价格、刺杀开关）+ 按等级段练级路线（选怪加权/HUD 建议）+ 8 方向 BFS 寻路（限 1600 节点）+ 选怪效用评分 + 喝药/拾取/背包满回城/死亡/3 分钟零经验熔断。`AutoPilotManager._pilots` 是唯一状态容器，**不写 DB、不写 Player 字段**（Player 是 slots dataclass，动态挂属性会炸）。
- **接入点**：`service.py` 新增第 47 个 WS 类型 `autopilot`（同步 `MIGRATED_TYPES` / `_handlers` / `test_all_messages.py::messages_for` 三处）；`on_message` 收到 `MANUAL_TYPES`（move/attack/pickup/useItem/NPC/交易/仓库等）立即 `pause`；`on_disconnect` 开头 `autopilot.on_disconnect(p)`；`tick()` 在 `world.tick(now)` 后同 now 驱动 `autopilot.tick(now)`；REST `GET /api/autopilot` 只读快照；GM `@托管 [安全|均衡|效率]`。
- **不加速、不绕过校验**：所有动作直接调现有 handler `_move/_attack/_pickup/_use_item/_cast_magic`，pilot 自己控 540ms 走路节拍与 `1400-min(370,lv*14)`ms 攻速，并遵守 `_move` 的 350ms 换图锁。前端零协议改动即可在 WS 广播里看到托管角色正常打怪。
- **关键坑（已修）**：怪物模板 `level` 只用于经验惩罚，**不代表强度**——1 级新手打的就是 10 级稻草人 / 13 级多钩猫，模板里 lv≤6 的只有 5 种（鸡、蝙蝠、雪球等）。最初按 `lv <= 玩家等级+3` 硬过滤，导致新手开托管一只怪都选不到、数分钟后熔断停机。现改为按 `t.dc` / `t.hp` 判危险度，等级差只做 0.5/0.8 降权。另修了 BFS 路径缓存 key 误绑玩家坐标导致每 tick 重算的问题。
- **前端**：`PlayScene.mjs` 加 `#autoPilotBtn`（P 键等价）、`#autoPilotPreset` 三档下拉、`#autoPilotStatus` 一行状态条；`autopilotState` 回包驱动渲染；Digit1-6 / F1-8 与点击游戏画面立即 `pause`；方向键/Tab/F9 等 UI 与聊天键故意排除，避免翻聊天记录误停。档位偏好只存 `localStorage['jootm2.autopilotPreset']`，服务端不落库。
- **验证**（全部 3.10 服务端 + 3.9 Playwright）：`test_all_messages.py` 91/0；`test_autopilot.py` 25/0（虚拟时钟：开启→寻路→击杀→拾取→节拍→低血喝药→聊天不打断→手动接管→再开启→死亡停→断线停→落库→重登不恢复→无 DB 字段→熔断）；`test_autopilot_ws.py` 16/0（真实 WS + REST + 断线重登）；`test_autopilot_ui.py` 8/0（浏览器 HUD 截图 `docs/autopilot-hud.png`）；`--soak 60` 真实挂机 1→2 级 / 9 杀 / HP 满 / 未停机；`test_ws_idle_timeout.py` 未退化。`node --check` / `py_compile` / `git diff --check` 通过。
- 改动文件：`pyserver/autopilot.py`(新)、`pyserver/service.py`、`frontend/scene/PlayScene.mjs`、`scripts/test_all_messages.py`、`scripts/test_autopilot.py`(新)、`scripts/test_autopilot_ws.py`(新)、`scripts/test_autopilot_ui.py`(新)、`docs/AUTOPILOT-PLAN.md`、`docs/autopilot-hud.png`(新)、本文 §58。

## §59 提交分类标签 + 托管实测三修 + 渲染两修（2026-11-15）

- **提交标签约定（用户要求）**：`CLAUDE.md` 新增「Git 提交分类标签」一节，每个提交带且只带一个 `[标签]`，13 类：托管/渲染/调整/保真/逻辑/网络/数据/音频/前端/测试/文档/构建/修复；标签表是唯一来源，后续可按提交性质自行扩充并同步更新。已示范 `[文档] 04e81c5`（规则）与 `[托管] 6c70096`（一期成果）。
- **托管第 2 轮（用户实测反馈三修）**：① 不会跑 → `_step_to` 距离 >=3 格发 `run:True` 走 `RUN_INTERVAL_MS=360`（`broadcast_move` 本就带 run），贴近目标仍走 540ms；② 不主动拾取 → 新增 `_choose_loot`（价值/距离评分 + 8s 目标锁 + 三档 `loot_sight` 6/8/10），贴身威胁优先战斗；③ 隔墙卡住 → BFS 改 8 方向 A*（切比雪夫启发、6000 节点），`_walkable` 与 `world.try_move` 完全一致（去掉 `is_door` 例外），无路或连续被挡 4 步就把目标 id 拉黑 10s 换目标，**删掉 `direction_toward` 直线撞墙 fallback**。
- **渲染：升级特效闪得厉害**：根因是 `PlayScene._fxTexture` 每帧独立 key、首次同步写 `null` 占位再异步回填，`chrsel[4..17]` 又不在 boot 预热清单，进游戏的 `abortPending('enter-game')` 还会掐断请求 → 14 帧逐帧空白。原版 `magiceff.pas:889` 是同步 `GetCachedImage`。修法：`onEnter` 的 `enter()` 之后预取 14 帧 + 1.5s 补发仍为 null 的帧；`_fxTexture(..., total)` 缺帧借用最近已加载帧；`_spawnLevelupFx` 去重（连升两级不叠加两份 `lighter`）。新增 `scripts/test_levelup_fx.py` 6/0（14/14 帧就绪、连续 8 帧有光、亮度增益 +15.6%）。
- **渲染：怪物死后视效不对（真根因）**：`case 'attack'` 的 170ms 命中帧延迟回调在怪死后仍把尸体 `_action` 设成 `struck`，渲染循环 `ATTACK_ANIM_MS` 后归位 `stand` → 「怪死了又站起来」；同时服务端死亡后紧跟的 `entityAdd(corpse)` 用新对象替换实体，`_sprite` 按对象引用缓存（:3874）被重建、尸体帧纹理全丢 → 尸体消失一瞬再出现；1400ms 倒地动画因此只播约 150ms 就被腰斩。修法：延迟回调跳过尸体动作（伤害飘字照旧）、`entityAdd` 对已有尸体就地更新字段不替换对象、怪物按原版 `Actor.pas:1430-1434` SM_DEATH 直接定格 ActDie 末帧（仅玩家 SM_NOWDEATH 保留 1400ms 倒地动画）、渲染循环兜底把非玩家尸体强制 `death`。新增 `scripts/test_corpse_fx.py` 7/0（3 秒内动作恒 death、sprite 未重建、尸帧已加载、无火焰层）。
- **验证**：`test_all_messages` 91/0、`test_autopilot` 32/0、`test_autopilot_ws` 16/0、`test_autopilot_ui` 8/0、`test_levelup_fx` 6/0、`test_corpse_fx` 7/0、`test_ws_idle_timeout` PASS；`--soak 60` 真实挂机 1→2 级 / 12 杀（修复前 9 杀）/ HP 满 / 状态栏出现「4格 · 跑」；`node --check` / `py_compile` / `git diff --check` 通过。
- 改动文件：`pyserver/autopilot.py`、`frontend/scene/PlayScene.mjs`、`scripts/test_autopilot.py`、`scripts/test_levelup_fx.py`(新)、`scripts/test_corpse_fx.py`(新)、`docs/AUTOPILOT-PLAN.md`、`CLAUDE.md`、本文 §59。

---

## §60 服务端挂载数据库静态站点 /site（2026-11-15）

- **需求**：`scripts/gen_website.py` 生成的静态站（`data/website/`，§55）不另起 `http.server`，直接由 `python -m pyserver` 在同端口提供，挂在 `/site`。
- **实现**（`pyserver/net.py` + `config.py` + `__main__.py`）：
  - 路由在资源前缀之后、前端静态之前：`/site`、`/site/`、`/site/...` → `_serve_file(req.path, config.website_root, config.website_prefix, "index.html")`；复用既有 MIME / ETag / 304 / HEAD / Range / 异步分块发送。裸 `/site` 先 301 到 `/site/`——页面内链接都是相对路径，少了尾斜杠浏览器 base 会变成站点根，CSS/图标全部 404。
  - `_serve_file` 新增两个可选参数（默认行为不变，游戏侧零改动）：`allow_gzip=False` 关掉该路由的同步 zlib；`cache_override` 覆盖缓存头。另外目录路径（`rel` 为空）回落到 `default_name`，所以 `/site/` 能落到 `index.html`。
  - 配置：`website.root`（properties / `JOOTM2_WEBSITE` / `--website`），前缀固定 `/site`（`ServerConfig.website_prefix` 属性）；未生成站点时 `/site` 返回 404，启动日志打印「(未生成：先跑 python scripts/gen_website.py)」。
- **性能（用户硬性要求：不能影响主逻辑）**：
  - 首版让 `/site` 走统一 gzip 缓存，实测 `drops.html`(1.9MB) 冷压一次同步阻塞 **13.63ms**，且压缩在事件循环里串行执行，多个首次访问的页面会在同一个 loop iteration 里叠加 —— 改为 `allow_gzip=False`。站点 HTML/PNG 共 8MB/59MB，关压缩换来的是每次请求同步阻塞上限 **4.21ms**（tick 间隔 100ms）。游戏侧压缩不受影响：`/main.mjs` 仍 `Content-Encoding: gzip`。
  - 压测方法（诊断脚本在 `data/`，gitignore）：`data/site_load.py`（独立进程 keep-alive 压测）+ `data/site_tick_probe.py`（同进程包装 `service.tick` 记录间隔/耗时）。**压测进程必须独立**：同进程 16 个客户端线程会和服务端抢 GIL，把延迟算到站点头上（首版同进程测出的 244ms 最大间隔是假数据）。
  - 最终数据（8 keep-alive 线程独立进程压 12s，1892 次请求 ≈158 req/s，混合 374 张 PNG）：tick 间隔中位 **138.2ms vs 空闲 139.8ms**、p99 **203.3 vs 197.8**、最大 **203.3 vs 197.8ms** —— 与空闲不可区分；站点额外服务端 CPU **4.55ms/请求**（已扣除空转 tick）。本机 tick 中位 ~138ms 是 Windows asyncio 计时器粒度（约 15.6ms）+ tick 本身耗时，不是本次引入的。
- **缓存语义**：站点重新生成后文件名不变，PNG 默认规则是 `immutable` 一年，会让老访客一年看不到新地图渲染 → 该路由统一 `public, max-age=60, must-revalidate`。
- **安全**：复用 `_serve_file` 的 `resolve()+is_relative_to()`，`/site/../pyserver/net.py` 与 URL 编码的 `%2e%2e` 均 404。
- **部署**：部署 rsync 默认 `--exclude data`，所以静态站**不在**服务器上；需要时单独 `rsync -a data/website/ root@aliyun.notmmao.com:/root/jootm2/data/website/`（compose 已挂 `./data:/app/data`，无需重启）。也可以不部署——`/site` 404 不影响游戏。
- **验证**：`test_pyserver.py` **99 PASS / 0 FAIL**（新增 4 条：`/site` 301 补斜杠、`/site/` 落到 index.html、未知页 404、前缀阻止 `..` 穿越）；curl 实测 `/site`(301→200, 208KB)、`/site/assets/style.css`、`/site/assets/maps/0.png`(200, 206 Range)、`/site/maps/0.html`、HEAD 200、游戏 `/` 200 且 `main.mjs` 仍 gzip。
- 改动文件：`pyserver/net.py`、`pyserver/config.py`、`pyserver/__main__.py`、`scripts/test_pyserver.py`、本文 §60。

---

## 归档索引（历史轮次，逐字保留在 docs/handoff/，只读不改）

| 文件 | 内容 |
|---|---|
| `docs/handoff/archive-2026-11-a.md` | §10~§20：音频链路 / 渲染防闪烁 / 选角与地面缓存 / 保真度补齐 / 第 1~8 轮实测反馈 / 1.76·1.80 资源差异分析（另含早期 §1 分支提交表、§5 坑、§6 旧待办、§7 合并注意、§9 数字速查的原始上下文） |
| `docs/handoff/archive-2026-11-b.md` | §22~§30：公网加载持久缓存 / 首屏纹理预载 / 地面条纹收口 / 旁观者血条 / 怪物 AI 与中立怪 / 掉落倍率表 / 新角色初始装备 / 尸体绘制层级 |
| `docs/handoff/archive-2026-11-c.md` | §31~§34：现场导出诊断工具与材质根因修复 / 尸体生命周期与重生 / 商店·EXP 条·遮挡·掉线处理 / 稻草人尸体帧 |
| `docs/handoff/archive-2026-11-d.md` | §35~§38：音效全量对齐 / 第 10~12 轮反馈（遮挡半透明·刷怪密度·MISS 音·矿洞视野·怪物显示名等） |

> 早期章节（§1 分支提交表 / §4 Java 版改动清单 / §5 坑 / §6 旧待办 / §7 合并注意 / §8 阅读顺序 / §9 数字速查）随重构裁剪出主文件：§4 的合并冲突文件清单与 §7 合并注意事项在执行 `git merge origin/main` 前仍需参考，完整原文可从 git 历史（本重构提交的上一版）找回。

## 给下一位 agent 的阅读顺序

1. 本文 §0.5 强制规则 与 §0.6 已知缺陷 —— 先知道哪些不能碰、哪些不是 bug
2. `docs/handoff/archive-*.md` 对应章节 —— 追历史根因时再查
3. `HANDOFF.md`（远澜原文）—— 玩法数值、协议字段、特效帧号的权威参考
4. `pyserver/service.py` 的 `_handlers` 与 `MIGRATED_TYPES` —— 消息分派全貌
5. `pyserver/world.py::tick` + `world_extra.py` —— 世界循环
6. `scripts/` 下 `test_*.py` / `compare_stacks.py` + `scripts/golden/` —— 回归基线
7. `git log --oneline -20` —— 最新提交即版本（`/api/version` 同源）
