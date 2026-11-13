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
