# jootm2 Python 服务端分支交接文档

> **最后更新**：2026-11-15（文档瘦身：§39~§68 归档到 `docs/handoff/archive-2026-11-e.md`）
> **分支**：`feat/python-server`；远程默认分支 `origin/main`（**没有 master**）
> **本机多个 Agent 协作**：每轮改动在末尾追加 `## §N 标题（日期）`，N 只增不减

## 文档维护规则（每轮必读）

1. **硬上限 500 行**：收尾前跑 `wc -l HANDOFF-PYSERVER.md`；超过 500 行就先把最老的轮次归档，再结束本轮。目标区间 350~500 行。
2. **主文件只留最近轮次**：固定内容 = 规则 / 已知缺陷 / 架构 / 命令 / 部署 / 归档索引 / 阅读顺序；轮次只保留**最近约 5 个 §**，更早的整块搬走。
3. **归档操作**：把最老的一批 `## §N` 从标题到下一个 `## ` 之前（含前后分隔线）完整剪切，追加到 `docs/handoff/archive-<年>-<月>-<字母>.md` **末尾**；归档只读，不改写历史正文。
4. **命名与大小**：`archive-<年>-<月>-<a|b|c…>.md` 按时间顺序；单文件约 800~1500 行（20~30 个 §），满了就开下一个字母，不要无限追加。
5. **同步索引**：搬走后更新 `## 归档索引` 一行（文件 → 覆盖的 § 范围与主题）；主文件不留占位符，也不复用 § 编号。
6. **别把规则搬走**：§0.5（强制规则）与 §0.6（已知缺陷）永远留在主文件；发现新坑先加规则，再记录本轮。

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

来自归档 e 的 §39.7 / §41.5（权威细节按需查）：

- `m_nMagLight`（魔法瞬时光）未做；火墙/火堆 `TClEvent.m_nLight` 光源待接（特效光源钩子已预留）
- lig0 贴图 additive 合成是 canvas 近似，非原版 `FogCopy` 逐像素 ColorLevel 压暗；极暗场景层次感与原版有可见差异
- 商店未扣库存（原版 `List20.Delete(ii)`）
- 天狼蜘蛛(118) 原版 `THighRiskSpider` 高风险双倍暴击未做
- 鹿 1/30 `TChickenDeer` 变种品质未做；矿石卖价超上限 1.3 倍分支实际不可达（纯度上限恒低于 DuraMax）
- `ET_PILESTONES` 碎石堆视觉事件未渲染；挖矿无专门重击动画（复用 attack 帧）；武器耐久挖到 0 无损坏提示（原版一致地不能再挖）

更早的已知边界（PvP 永不 miss、逐技能施法音近似、受击/死亡固定男声、大地图/系统菜单 DOM 简化实现、0 伤害 `max(1,…)`、`_regen` world 级共享计时等）——详见归档 a/c/d/e 各节「边界/遗留」小节（§13.3 / §16.4 / §18.3 等）；部分可能已被后续轮次处理，以最近轮次为准。

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

## 归档索引（历史轮次，逐字保留在 docs/handoff/，只读不改）

| 文件 | 内容 |
|---|---|
| `docs/handoff/archive-2026-11-a.md` | §10~§20：音频链路 / 渲染防闪烁 / 选角与地面缓存 / 保真度补齐 / 第 1~8 轮实测反馈 / 1.76·1.80 资源差异分析（另含早期 §1 分支提交表、§5 坑、§6 旧待办、§7 合并注意、§9 数字速查的原始上下文） |
| `docs/handoff/archive-2026-11-b.md` | §22~§30：公网加载持久缓存 / 首屏纹理预载 / 地面条纹收口 / 旁观者血条 / 怪物 AI 与中立怪 / 掉落倍率表 / 新角色初始装备 / 尸体绘制层级 |
| `docs/handoff/archive-2026-11-c.md` | §31~§34：现场导出诊断工具与材质根因修复 / 尸体生命周期与重生 / 商店·EXP 条·遮挡·掉线处理 / 稻草人尸体帧 |
| `docs/handoff/archive-2026-11-d.md` | §35~§38：音效全量对齐 / 第 10~12 轮反馈（遮挡半透明·刷怪密度·MISS 音·矿洞视野·怪物显示名等） |
| `docs/handoff/archive-2026-11-e.md` | §39~§64、§65~§68：多光源 / 村店城店品类 / 肉品质挖矿 / 可挖尸 / 第三方 DB 对照 / 传送特效 / 怪物出场动画 / 守卫 AI / 昼夜+仓库+寻路 / 攻击转向 / 命中判定 / 僵尸洞口 / 复活经验 / 技能书与被动技能 / 骷髅召唤 / 多 gamedata 树 / 33 技能对齐 / 176 脚本方言与 MonGen / 静态站点与 wiki / 升级特效 / AutoPilot / 持久磨损 / 死亡惩罚掉落 / 掉落归属 / 托管挖肉 / 数据体检 / 倍率机制与前两轮清理 |

> 早期章节（§1 分支提交表 / §4 Java 版改动清单 / §5 坑 / §6 旧待办 / §7 合并注意 / §8 阅读顺序 / §9 数字速查）随重构裁剪出主文件：§4 的合并冲突文件清单与 §7 合并注意事项在执行 `git merge origin/main` 前仍需参考，完整原文可从 git 历史（本重构提交的上一版）找回。

## 给下一位 agent 的阅读顺序

1. 本文 §0.5 强制规则 与 §0.6 已知缺陷 —— 先知道哪些不能碰、哪些不是 bug
2. `docs/handoff/archive-*.md` 对应章节 —— 追历史根因时再查
3. `HANDOFF.md`（远澜原文）—— 玩法数值、协议字段、特效帧号的权威参考
4. `pyserver/service.py` 的 `_handlers` 与 `MIGRATED_TYPES` —— 消息分派全貌
5. `pyserver/world.py::tick` + `world_extra.py` —— 世界循环
6. `scripts/` 下 `test_*.py` / `compare_stacks.py` + `scripts/golden/` —— 回归基线
7. `git log --oneline -20` —— 最新提交即版本（`/api/version` 同源）

---

## §69 第二轮数据清理：商店/僵尸技能/坏行（2026-11-15）

- **商店**：72 个商人，`[goods]` 整表替换为 176 清单（比奇/盟重/毒蛇谷/白日门/恶魔系；D 开头
  脚本用 `176/Market_Def` 子目录 + `QuestDiary/商人/*.txt` 的 `#CALL` 解析）。
  删掉 Lv20+ 装备与私服加货，补回 176 基础货（布衣、皮制手套、鹤嘴锄、卷轴等）。
  N1 高等级商品 229→**94**、N2 新增商品 227→**117**；**76 对同图同坐标商店 0 差异**。
  - **踩坑（用户反馈「重启后商店没变」）**：审计脚本原来按 `(地图, NPC 名)` 去重，比奇两个
    「铁匠铺老板」中先出现的 `2Bbl`（只有对话、无 goods）把卖装备的 `2Twe` 遮住了；边界村小店
    又因为没有 Lv20 商品、没进 N1 目标。已改为 `(地图, 名, x, y)` + 坐标就近配对
    （`pair_goods`），补改 40 个商店（含 `边界村小店老板@0(291,610)`、`铁匠铺老板@0(297,612)`）。
  - **同名审计**：再按「同图 + 精确坐标」全量复核，发现 5 个同坐标不同名的商店
    （`商人@1`↔`沃玛商人`、`老人@3`↔`红名村老人`、`毒药老板@0153`↔`炼药老板`、
    `服装店打杂@1003`↔`服装店老板`、`不法小贩@DM001`↔`商贩`），已一并对齐。
    剩余 176 有商品的商店都是 main **没有该 NPC**：苍月岛 5 家、幻境/影之道 3 家、
    桃源门 1 家、封魔谷修理师 1 家——需要补 NPC spawn，改 goods 解决不了。
- **僵尸技能书**：僵尸20 15 行（高级书 1/10→1/100、基础书 1/2→1/20）、僵尸30 5 行
  （基础书 1/2→1/20），对齐 176 同怪掉率；僵尸10/40/50 原本就一致。
- **MonItems 坏行**：删除 123 行 / 45 文件（`夏浦儿手镯`、`治愈戒指`、`兰花*`、`敌难*`、
  `战神甲(女)`、`栗子9/10`、`金币8000` 等），物品名在 main 物品库不存在，永远不会掉。
  报告坏行 current 131→**0**。
- **复查后未改**（证据见 MAIN-CHANGELOG）：
  - 176 有而 main 缺的 6362 行 / 220 物品（通用低阶掉落），属整套 itemization 决策，不做机械补行。
  - **此前误判**：HP/MP 强化水、万年雪霜、雪莲、修炼石/手镯不是「自创物品」，176 物品库与
    掉落表里都有（分别 2/15/6/10/12 处）；main 只是把它们加到了更多 boss，暂留。
  - 商店坏名 18 行（`护身符(大)`×7、`超级灰色/黄色药粉`×6、`皮手套`×3、`召唤死神`、`木料`，
    main 物品库缺定义）、`精灵头盔`、StdItems 基础价差（main 偏低），均记录未动。
- **验证**：`test_pyserver` 100/0（更新边境村小店/书店断言为 176 口径）、`test_tick_systems`
  180/0、`test_all_messages` 91/0、`test_autopilot` 39/0；`MODS-REPORT` 最终
  G1 4 / G2 1 / N1 94 / N2 117 / 坏行 0、缺失基线 174 怪。
- **已提交**：`be6c3f1`（数据清理）+ `caa2c65`（倍率机制）。

---

## §70 技能修炼点清单 + magicTrain 倍率（2026-11-15）

- **差异**：`mud2/DB/Magic.csv` 的 `L1Train/L2Train/L3Train`（技能升级所需修炼点）
  main 与 176 大面积不同——33 个技能中 **32 个**被下调，倍数 2.7×~26.7×
  （火球术 10/20/30 vs 50/150/300；基本剑术 10/20/30 vs 100/400/800）。
  `scripts/compare_mods.py` 新增 **M1 技能修炼点异常** 分类，已写入 `docs/MODS-REPORT.md`。
- **倍率**：`config/game_rates.json` 新增 `magicTrain`（默认 1.0）。语义是
  **修炼速度倍率**：2.0 = 所需点数减半，0.5 = 翻倍，0 = 不再升级。
  `DropRates.magic_need(raw) = max(1, round(raw / magicTrain))`；`WorldExtra.init_extra`
  调用 `apply_magic_train_rate()` 折算 `magic_templates.maxTrain`，升级判定
  （`handlers_combat._train_skill`）与客户端技能页（`world.py` skills_json）读同一份值，
  不会出现「显示需求/实际升级」不一致。
- **未还原数据**：现有 SQLite 的 `magics` 表已固化私服值（实测 `data/jootm2.db`
  火球术 `maxTrain=[10,20,30]`），只改 CSV 不会生效；还原需数据迁移/重建库，列入待办。
- **验证**：`test_tick_systems` 180/0（新增 5 条 magic_need 断言 + 1 条模板折算断言：
  `[10,20,30] -> [5,10,15]`）；`test_pyserver` 100/0、`test_all_messages` 91/0、
  `test_autopilot` 39/0；`MODS-REPORT` M1=32，其余计数不变
  （G1 4 / G2 1 / N1 100 / N2 172 / 坏行 0）。
- **已提交**：`caa2c65`（倍率机制）+ `be6c3f1`（数据）。

---

## §71 骷髅战士掉落还原 + 关闭传送/招财树（2026-11-15）

- **骷髅战士**（`MonItems/骷髅战士.txt`）：5 行 → 32 行，按 176 原版表恢复
  （装备 1/400~1/1800、金创/魔法药小量 1/15~1/25、金币 1/2 200）。
  `魔力强效神水/体力强效神水/魔力神水/攻击神水` 4 种 main 物品库没有，未写入。
  装备期望 0.003→0.039 件/只（约 26 只一件），药约 9 只一次。
- **关闭传送/招财树**（用户要求跑图；`;` 注释保留原行，不删除）：
  - `Npcs.txt`：6 个主城 `传送员`、`静寂传送员`、`招财树`。
  - `MerChant.txt`：2 个 `商店传送员`、`盟重老兵`（同为城内商店直送）。
  - 保留 `会议厅/决战场/一层东/西传送员`、`新洞穴传送人`——需要凭证/金币的副本入口。
- **掉落审计**（除骷髅战士，按 176 对比）：
  - 零装备但 176 有：僵尸10（17 图）、僵尸20（16 图）、虎蛇0。
  - 只掉金币：蜜蜂、蛤蟆（176 有整套基础装）；神鹰 176 也只有金币，正常。
  - 表被砍到 ≤6 行而 176 ≥15 行：骷髅(2/34)、骷髅战将(5/38)、掷斧骷髅(4/34)、
    骷髅0/骷髅战士0/掷斧骷髅0、半兽人、半兽战士、沃玛战士/勇士、森林雪人、
    多角虫、盔甲虫、红蛇0、跳跳蜂、巨型蠕虫、月魔蜘蛛、天狼蜘蛛、血巨人、稻草人等 29 只。
  - Boss：双头金刚(4/87)、骷髅精灵(25/71)、邪恶钳虫(33/83)、虹魔教主(40/102) 等差距大；
    176 表里这些怪有大量 1/2~1/3 装备行，整体恢复前需确认是否接受该爆率。
- **验证**：`test_pyserver` 100/0、`test_tick_systems` 180/0、`test_all_messages` 91/0、
  `test_autopilot` 39/0；NPC 解析 292 个，已无生效的普通传送员/招财树。
- **已提交**：`be6c3f1`。

---

## §72 新手/骷髅/僵尸线掉落整表恢复 + 新手村商店分档（2026-11-15）

用户口径：**boss 先不动**，只改新手/骷髅/僵尸线；边界村小店没药不像话。

- **恢复 40 个 MonItems 文件**（176 原表照录，保留首行 `;monster` 注释，加一行
  `;176 原版口径（原表照录）`）：
  - 新手线：多钩猫、钉耙猫、半兽人、半兽战士、森林雪人、多角虫、盔甲虫、稻草人、
    蛤蟆、蜜蜂、红蛇、虎蛇（10~48 行）。
  - 骷髅线：骷髅、骷髅0、骷髅战士、骷髅战士0、骷髅战将、掷斧骷髅等 9 只（22~34 行）。
  - 僵尸线：僵尸1~5、僵尸10/20/30/40/50（30~41 行）。
  - `蜜蜂` 文件名带尾随空格（`蜜蜂 .txt`），单独处理；`蛤蟆` 176 的
    `体力强效神水/攻击神水/疾风神水` 在 main 物品库无定义，跳过 3 行（13→10 行）。
- **每只期望（按 176 行逐条相加）**：装备 0.015（稻草人）~0.135（半兽战士）件/只；
  骷髅战士 0.039、骷髅战将 0.056、掷斧骷髅 0.074、红蛇 0.131。
  僵尸线显著偏高：僵尸10 装备 **5.08 件/只** + 技能书 3.5 本/只 + 药 1；僵尸20/30 装备 5.07。
  骷髅0/骷髅战士0 装备 3.05 件/只（刷在 D001/D002）。这是 176 表原值（僵尸在 1.76 就是
  「爆一堆低阶垃圾」），先按原表落地；觉得过头再改 `MonItems` 或上倍率。
- **新手村商店分档**（用户第 16 轮口径：新手村种类必须少于主城，不能共用模板）：
  - 两个新手村都改：边界村（176 `比奇村`）与银杏村（176 `新人村`，map 0 621~649/591~642
    片区 + map 0119）。
  - `4Tdu-0`（边界村小店）：补回 `金创药(小量)`/`魔法药(小量)`（100/3）
    和 `蜡烛 10/10`、`火把 50/10`；**只卖小量药**，不卖中量/强效/超级/药包
    （边界村没有药店，小店兼小量药）。蜡烛/火把对应 NPC 对话「我们只经营蜡烛和
    地牢逃脱卷」且夜间视野必需，176 通用杂货铺模板反而漏了。
  - `2Twe-0`（边境村铁匠铺）、`2Gwe-0`（银杏村精武馆老板）：12 行 → 6 行，
    只留 `木剑/青铜剑/铁剑/短剑/匕首` + `鹤嘴锄`，删 `乌木剑/八荒/海魂/半月/凌风/破魂`。
    Lv16+ 武器去比奇城（`2Bwe-0103` 仍是 12 行）。
  - `3Tdr-0`（白家服装老板）、`3Gdr-0`（高家店老板）：6 行 → 4 行，
    删 `中型盔甲(男/女)`，保留布衣 + 轻型盔甲。
  - `7Gst-0`（银杏村陈家铺老板/杂货）：补 `蜡烛 10/10`、`火把 50/10`，不卖药
    （银杏村有药店）。
  - `4Gdu-0119`（银杏村许家店/药店）：13 行 → 2 行，只留小量红蓝药；
    删中量/强效/小包/中包/超级/太阳水。176 里它就是 `新人村_药店`。
  - `4Gdm-0119`（银杏村药房小老板）**不动**：卖的是施毒术药粉（少量~超级），
    176 里是 `新人村_毒药`，与「药店不卖大量药水」无关。
  - 没有新建 NPC 代号：各店本来就各有独立脚本，直接精简即可；主城店不动。
  - 边界村**没有药店 NPC**（176 同区域也只有小店/铁匠/屠夫/服装 4 个），
    由小店承担「只卖小量药」角色；独立药店需新增 NPC spawn，未做。
- **背景**：176 的 `Market_Def/比奇村|比奇城/*` 全是 7 行 `#CALL` 模板，
  12 家武器店共用同一份 `武器店.txt`（含破魂），**不是逐店清单**——
  这正是新手村和主城货单一样的原因。
- **`compare_mods.py` 重生成**：G3 同怪新增 397→**385**、掉率暴涨 239→**238**、
  缺失基线掉落怪 174→**140**；M1 仍 32（修炼点固化在 SQLite，见 §70）。
- **验证**：`test_pyserver` **107/0**（新增新手村武器/服装/杂货/药店断言，边界村+银杏村各 4 条）、
  `test_tick_systems` 180/0、`test_all_messages` 91/0、`test_autopilot` 39/0
  （首次 36/3 是已知时序抖动，重跑 39/0）。
- **仍需用户验证**：重启本地服务后生效（掉落/商店读文件，技能模板需重灌）；已提交 `be6c3f1`。

---

## §73 Magic.csv 全列对齐 176（2026-11-15）

`gamedata/mud2/DB/Magic.csv` 逐列对比 `data/gamedata/176/mud2/DB/Magic.csv`（该文件由
REDM2.DB 合并而来）。33 个正式技能里，差异只有三类，**全部按 176 还原**：

1. **修炼点（32 个技能）**：main 是私服快速口径，普遍低 3~16×。
   火球术 10/20/30→50/150/300、基本剑术 10/20/30→100/400/800、
   大火球 80/100/200→800/1400/2500、攻杀剑术 50/80/120→400/800/1600、
   半月弯刀 200/400/800→800/1600/2400 等。只有 `诱惑之光` 的 L3 与 `烈火剑法` 的 L1 相同。
2. **伤害/耗蓝（5 个技能）**：
   - 灵魂火符 Power 8→15、MaxPower 8→28、DefPower/DefMaxPower 3→10（道士主攻约 2.3×）；
   - 雷电术 Power 8→14、DefPower/DefMaxPower 9→10；
   - 疾光电影 Spell 30→25、DefSpell 30→20（Lv1 蓝耗 38→26）；
   - 治愈术 MaxPower 14→20（14→14~19）；
   - 冰咆哮 DefPower/DefMaxPower 14→12。
3. **6 行伪技能删除**：`天尊之兽/惊寂之雷/惊寂之光/静寂圣剑/天尊之疗/天尊之盾`
   的 MagId 与正式技能重复（30/11/20/26/29/15），DB 导入用 `INSERT OR IGNORE`，
   这些行永远进不了 `magics` 表，属死数据。**技能书物品没删**：main/176 的 StdItems 都有，
   牛魔教主0 等还按 1/500 掉，但两棵树的 Magic.csv 都没有对应技能，学不会——基线遗留问题，未扩大改动。

**生效方式（重要）**：`magics` 模板固化在 SQLite，`seed_game_data` 只在 `std_items` 为空时导入。
已执行 `python scripts/switch_data.py use main` 清空三张模板表，**重启 pyserver 后**从主树 CSV 重灌
（日志会打印 `[db] imported CSV: ... magics=33`）。部署到阿里云时如果沿用旧库，也要在服务器上
对 `data/jootm2.db` 执行同样清表，否则线上仍是旧的快速修炼值。

**节奏影响**：修炼点回到原版后，技能升级比之前慢 3~16×。想保留快速体验就在
`config/game_rates.json` 把 `magicTrain` 设成 >1（2.0 = 所需点数减半，0 = 不再升级），
这属于阶段 b，不要改回 CSV。

**验证**：`compare_mods.py` 的 **M1 技能修炼点异常 32→0**；N1 高等级商品 94→92
（新手村武器店删掉凌风/破魂）；`test_pyserver` 107/0、`test_tick_systems` 180/0。
已提交（`be6c3f1`）。

---

## §74 高级之家测验任务跑通 + 托管六问题修复（2026-11-15）

### 高级武士/法师/道士之家（新手测验送第一本技能书）

**原版语义**（本地 Delphi 源码逐条核对）：
- `MonGen`（ObjNpc.pas:7856-7862）：x = Random(range*2+1) + (Param2-range)，
  逐只 `RegenMonsterByName(Param1地图, x, y, 怪名)`（UsrEngn.pas:1359）；
  `MonClear` 清图怪；`CheckMonMap`（:7153）图上怪数 ≥ n；`CheckHum`（:7163）
  图上玩家数 ≥ n；`CheckBaggage`（:7168）背包有空位。
- `TimeRecall`（ObjNpc.pas:7800-7806）：记录当前 map/x/y，n 分钟后 SpaceMove
  送回（ObjBase.pas:6440-6443）。取消点只有到期执行与重登；脚本 `map` 换图
  **不**取消——原版取消在 `BaseObjectMove:23614-23616`（回城类才走它），
  change_map 里加取消会让进测验房瞬间解除限时，已踩过又回退。
- 流程：756（已引导）→ 7 级 → 传测验房杀怪 → 屋内 NPC `checkmonmap` 不过 →
  `give` 免费第一本书（战士=基本剑术/道士=治愈术/法师=火球术）+ `set 757` →
  引导去比奇书店买后续书。测验链(756/757)与命运链(204-210)在 @main 互斥，
  756/757 优先——做了测验的战士永远 Q707，这是原版设计。

**实现**（`ccf0895`）：
- `npc_script.py` 补 7 条命令：`mongen/monclear/checkmonmap/checkhum/
  checkbaggage/param1-3/timerecall`；Param 存 NPC 实例，TimeRecall 存
  `Player.time_recall`，world tick 到期 SpaceMove 送回。
- `world.py`：`regen_monster_by_name`（落点自指定格环形全扫描，测验小屋
  密度高，随机尝试会漏放）、`clear_map_monsters/count_map_monsters/
  count_map_humans/start_time_recall/_tick_time_recall`；`spawn_monsters`
  抽出 `_spawn_monster` 复用。
- 入口：本数据集 756 无任何设置点（新手引导 NPC 缺失，全库仅 3 处 check），
  按原版意图补在三之家 @main 末尾（`ccf0895`）：本职业未开始测验 →
  `set 756 + goto @Q706`；命运链 204-210 玩家优先走原分支，203 阶段
  （卫家店→铁匠铺途中）维持 busy 文本（需要 `#ACT break` 短路，不然
  busy 文案后面还会接测验分支）。
- 数据笔误修正：`9Eht-01132.txt` 的 `checkmonmap 01152` → `01132`
  （法师房，复制粘贴错误；不改就永远「无怪」直通给书）。
- 死引用：战士测验怪「暗之沃玛护卫」Monster DB 无模板（全库仅脚本一处
  引用）——按原版 RegenMonsterByName 语义静默失败 → 空房 → checkmonmap
  不过 → 直通送书，任务仍闭环。**未造怪未改文案**。

**验证**：`scripts/test_npc_home_quest.py` 21/0（入口/checkhum 拦截/
刷怪 40/屋内给书/包满拒发/TimeRecall 送回/战士死引用闭环/职业与命运链守卫）。

### 托管六问题（`9f0c782`..`1cd059f`，4 个提交）

1. **捡拾死循环**（a 去捡没捡到→b 反方向→a 掉头）：根因是落点格被活物
   占位（怪站金币上）→ exact 寻路不可达 → 拉黑 10s → 换目标 → 循环。
   `_choose_loot` 现在跳过「被**别的**活物占位」的掉落（自己站的不算，
   那是捡拾位），等怪走开再捡。
2. **没靠近就攻击 / 3. 道士打自己召唤物**：同一根因——托管选怪没排除
   `master_id >= 0` 的召唤物。`_choose_target/_nearest_monster/缓存目标/
   _nearby_threat` 全部跳过。
4. **随机卷轴/回程无特效**：`use_scroll` 缺两段 SpaceMove 特效
   （spaceMoveHide/show），只发 entityRemove+changeMap。已改走
   `space_move` + `space_move_show`，与宝宝跟随传送完全一致。
5. **护身符数量**：护身符/毒药粉 StdMode 25 耐久 100=1 张（UseAmulet 每次扣
   count*100），前端 `_itemDesc` 加 std 25 分支显示「剩余 N/M 张」。
6. **怪物不打宝宝**：`_resolve_target` 只扫 players。现在保留目标与扫描
   都含召唤物（master_id≥0），宝宝打怪走 `Struck → SetTargetCreat` 语义
   记仇转火；普通怪打宝宝走通用近战结算，远程怪伤害公式兼容 Monster
   目标，延迟飞矢结算对宝宝扣血。守卫（112）仍只管红名玩家（原版
   TGuard 语义）。道士练级从此有真肉盾。

**验证**：`scripts/test_hosted_fixes.py` 12/0；六套回归
（autopilot 39 / death_drop 26 / durability 16 / spell_fx 11 /
npc_home_quest 21 / hosted_fixes 12）全绿。

---

## §75 尸体渲染两处修正（2026-11-15，`23117b1`，纯前端）

1. **重进地图尸体变活物**：`onEnter` 把快照 nearby 实体直接塞 entities，
   没走 entityAdd 的 corpse 标记处理（:1312 那套只在增量消息路径有）→
   brief_json 的 `corpse:true` 被无视，画成活物站着不动，直到尸体到期
   entityRemove 才消失。修复：onEnter 对 nearby 逐个套用与 entityAdd 相同的
   `_corpse/_action='death'/prefetchDeathFrame`（跳过 self）。
2. **掉落物被尸体遮盖**：原版 AxeMon.pas:444-446 在 TSkeletonOma.GetDefaultFrame
   对 `m_wAppearance in [30..34,151]`（沃玛战士/勇士/将军/火焰沃玛/教主/护卫）
   设 `m_nDownDrawLevel := 1`，行循环 `j = Ry - Level` 就画、屏幕位置不变 →
   **同行（Ry）掉落物画在尸体之上**；其余怪 Level=0，同行尸体盖住物品是
   原版行为。实现：渲染快照给沃玛系尸体/濒死实体带 `drawRow: y-1`，
   MapActor 按行分桶用 drawRow、屏幕锚点仍用 e.y。

## §76 大地图入口标注 + 误伤宝宝豁免（2026-11-15，f2e7800..b15f558）

1. **大地图入口显示下张地图名**（增强，原版客户端无此标注）：map_snapshot
   的 map 对象新增 `gates`（来自 MapInfo.txt 连接行 `src x,y -> dst x,y`，
   标题取 `[dst 名称]` 定义），大地图在入口格画黄色门点 + 「→ 目的地图名」
   （右侧放不下自动画左侧）。
2. **误伤宝宝豁免**（原版 IsProperTarget，ObjBase.pas:21508-21513）：玩家打
   有主的怪，主人 = 自己且攻击模式 ≠ HAM_ALL(全体) → False。本服无攻击
   模式开关 → 近战 attack / 点目标法术（自家宝宝目标作废 = 对地软失败，
   特效照放无伤害）/ 范围法术（爆裂火焰 23/33、地狱雷光 24）/ 野蛮冲撞
   （_push_around/_rush_rush）一律跳过 `master_id == p.id`。客户端点击攻击
   路径（普通左键 + Shift 强攻）按 masterId 过滤自家宝宝 —— 点宝宝落到
   拾取/移动分支，治「捡东西点到宝宝挥刀」。原版客户端靠宝宝名字里的
   「名(主人名)」括号识别（ClMain.pas:2852-2858），本服宝宝名无括号 →
   Monster.brief_json 显式下发 masterId。

**验证**：`scripts/test_slave_protection.py` 9/0；既有六套回归全绿。

## §77 治愈术治疗自家宝宝（2026-11-15）

§76 的误伤豁免写过头：castMagic 目标解析里对自家宝宝无条件作废，治愈术
点宝宝 → 目标被清 → 回退自疗。原版治疗系走 IsProperFriend
（ObjBase.pas:24192-24196）：玩家施放者对 `m_Master = Self` 的宝宝**恒 True**
（Magic.pas case 2 治愈术直接调它，:307；群体治疗 :593 同）。修复：
- 豁免加 `not is_heal_magic(magic_id)` 条件，只拦攻击系；
- 治愈术（2）：接受自家宝宝目标，宝宝没有 inc_healing 池 → 按
  RM_MAGHEALING（SendDelayMsg 800ms 即时加血）直接加 hp 并广播 stats；
- 群体治疗（29）：目标集合补上主人=自己的宝宝；
- 野生怪/别人的宝宝仍不可治疗（IsProperFriend=False，无人回血）。
前端无需改：magicTarget 悬停本来不过滤宝宝，F 键治愈悬停宝宝即带 targetId。

**验证**：test_slave_protection.py 扩到 12/0（新增治疗宝宝/野生怪不可疗）；
spell_fx 11、hosted_fixes 12、autopilot 39 全绿。

## §78 召唤术熟练度与重复召唤对齐原版（2026-11-15）

**原版语义**（Magic.pas / ObjBase.pas 逐条核对）：
- 熟练度：`boTrain := MagMakeSlave(...) 成功`——**只有实际造出新宝宝**
  （m_SlaveList.Count < nMaxMob，默认 1）那次施法才 TrainSkill(Random(3)+1)
  （受人物等级门槛约束）。宝宝活着时施法不造新 → MakeSlave 返回 nil →
  不加熟练度。
- 护身符在造宝宝**之前**消耗（13~19 共用段 :432-435 扣 1 张、神兽 :541-542
  扣 5 张）——宝宝活着时再放**护身符也照样扣**。
- 宝宝存活期间再施放：骷髅（17，MagMakeSlave :1387-1421）**什么都不做**，
  旧骷髅原地保留；神兽（30，MagMakeSinSuSlave :1382-1384）走
  **RecallSlave**（ObjBase.pas:21940-21958）把旧神兽 SpaceMove 到主人
  **面前一格**（GetFrontPosition）——这是「把跑远的宝宝叫回来」的原版玩法。

**本服 bug**（§65 遗留）：旧实现先 `remove_summon(old)` 删旧宝宝再造新的
→ 每次施法都「免费刷新满血宝宝」+ 每次都加熟练度，与原版完全不符。

**修复**：
- handlers_combat.py：宝宝存活 → 骷髅直接 return False（旧宝宝保留）；
  神兽 `w._slave_space_move(old, p, front=True)` 召回面前（复用宝宝跟随
  瞬移的两段特效消息，落点改 GetFrontPosition 语义）；两分支都 return
  False 不加熟练度。护身符消耗保持在召唤尝试之前。
- world.py `_slave_space_move` 加 `front` 参数（False=跟随落背后格
  GetBackPosition，True=召回落面前格 GetFrontPosition）。

**验证**：test_slave_protection.py 扩到 21/0（召唤首放加熟练度、存活期
再放旧宝宝同 id 保留/不加熟练度/神兽召回身边）；spell_fx 11、
hosted_fixes 12 全绿。

## §79 半兽古墓/天然洞穴五层刷怪换 176 口径 + 半兽勇士掉落对齐（2026-11-15，未提交待验收）

**起因（用户三连）**：①洞穴不法小贩卖价/回收面核对（结论：脚本头 `%180` 倍率与
`+N` 八分类在 `npc_script.goods`/`handlers_npc._get_item_price` 已正确消费，买价
=Round(Round(原价×1.1)×1.8)、回收不收倍率加成——均与原版
`ObjNpc.pas:1385-1416/2117/2156` 一致，无改动）；②半兽古墓（D001-003）太稀疏；
③半兽勇士强效太阳水掉太多。

**MonGen 全局替换试验→回退为只换三层**：用户先选型 B（全表换站点口径），实测暴露
两类问题——build_176_envir 的「站点同名图→单一图号」塌缩使 E601/602/603（都叫
连接通道）、香石古墓、石墓阵等只有一张图拿到站点行、其余同名图旧行被丢弃；牛魔寺
5 图站点行全是牛魔系**无模板死行**整图清零——22 图反而比替换前稀疏，需按
(map,怪) max(旧,新) 回填 528 行旧树行兜底。用户判定**全局改动太大不好验证**，
已全部回退（`git checkout`），改为只换 D001-D003（脚本 `data/mongen_tomb_swap.py`，
gitignored 可复查）。

**最终 MonGen 改动**（`data/mongen_tomb_swap.py` + `data/mongen_cave_swap.py`）：
`;半兽人洞穴1/2/3` 三区块换站点口径行，保留原节注释原文（含 `;半兽人洞穴2 ` 尾随
空格）；天然洞穴 D011/D012 在旧树里**无节注释、是连续数据块**，按「首个 D011 行 →
首个 D021 行前」整段重建（夹 `;特殊` 分段照旧）。两脚本共同规则：0 系变体与骷髅
精灵归入 `;特殊`。**五图之外全部行与 HEAD 逐字段一致（已程序校验），五图内怪级
零回退**。可刷密度（离线加载实测一致）：D001 216→617、D002 50→500（名义 611，差
111=骷髅战将/战将0/精灵无模板死行）、D003 49→490（名义 622）、D011 122→545、
D012 234→560（名义 692，差值同为战将系死行）。沃玛森林（map 1）等其余地图密度
维持旧口径未动，验收后如再推广走 max 合并路线。

**掉落**：`MonItems/半兽勇士.txt` 整文件替换为站点口径（176 树同文件）。旧 Gee 表
9 行强效太阳水（5×1/1+4×1/2，每杀期望 7.5 瓶）→ 站点口径期望 3.5 瓶，并补回
1/16 技能书/首饰、1/40 低档装备整段。其余沃玛森林（map 1）13 怪逐项期望值对照
全部一致；唯食人花 `食人树果实` vs 站点 `食人树的果实`——后者在 176 物品库根本
不存在（死行），主树名字正确，不改。

**踩坑（全局试验期，留给后续 B 路线）**：①回填行若写成 `;回填(...): D001 …` 注释
前缀会被 `gamedata._rows` 按 `;` 跳行、**一行都不生效**，Envir txt 追加生效行绝不
能带 `;` 前缀；②文本模式 `open().read()` 的通用换行会先吃掉 CRLF，再
`split('\r\n')` 整文件变单「行」——批量改写这类文件必须二进制读写。性能顺带实测：
全局口径下 5 图常驻 5743 怪 tick avg 8.9ms / max 29ms（300ms 预算内），探针
`data/mongen_perf_probe.py` 可复用。

**遗留数据任务**：站点口径 37 个怪名（牛魔系/暴牙蜘蛛/暗之系列/骷髅战将/骷髅精灵
等）主树与 176 树 Monster 库都无模板（约 7168 只死行怪），补模板前古墓二层实际
只有 500/611。

**验证**：test_pyserver 107/0、test_tick_systems 180/0；范围核对如上。改动=
`MonGen.txt`（仅半兽 3 层+天然 2 层区块）+`MonItems/半兽勇士.txt`；数值改动按约定
**未提交**，待游戏内验收（建议 `[数据] 半兽古墓与天然洞穴刷怪换176口径`、
`[调整] 半兽勇士掉落对齐站点口径` 两个提交）。

## §79 大地图入口过滤：只标洞穴/野外大图（2026-11-15，用户约定）

§76 的大地图入口把比奇省 97 个入口（皇宫/商店/研究所等房间）全标出来了，
用户要求只显示进洞穴的入口。判据 = **目标图 max(w,h) >= 100**：

实测分布两端干净分离——房间图最大 55（皇宫 0122 51x55），洞穴/野外
最小 200（D401 东部矿区 200x200），中间无过渡，阈值 100 稳分离。
剩余的比奇省入口：1/2（600x600 大区域）、D001/D011（蜈蚣洞 400x400）、
D401（矿区）等 41 个入口行（同一洞穴多入口各占一行）。

实现：`gamedata.map_size(map_id, map_dir)` 只读 .map 头 4 字节拿宽高
（比加载整张 MapGrid 便宜几个数量级，97 个入口每快照都要判定，不能把
目标图全加载）；world init 预计算 `cave_maps`（所有 gate 目标图过一遍
阈值），`map_snapshot` 的 gates 只保留 dst ∈ cave_maps 的行。前端零改动。

**验证**：test_slave_protection.py §7 改断言后 22/0（比奇省 41/97、
皇宫已过滤、剩余全为大型图）。

## §80 背包满还能继续购买：前端溢出不可见，补原版 AddItemBag 快捷栏自动填充（2026-11-15）

**现象**：用户报告「背包满了还可以一直买」。排查结论：**服务端拦截完全正确**——
BAG_SIZE=46（原版 MAXBAGITEM，Common/Grobal2.pas:70，注释「包含快捷栏上的
6 个物品」），`_buy_item`（handlers_npc.py:132）满包拒绝、不扣金币，
新回归 `scripts/test_bag_capacity.py` 8/0 验证。

**根因在前端显示层**：背包窗口只画 40 格（8×5，原版 TDGrid ColCount=8，
`idx = col + row*8 + 6` 覆盖 ItemArr[6..45]，快捷栏 0..5 是屏幕底部 DBelt），
而服务端可装 46。网格显示满（40）时还能再买 6 个，买来的物品落进
**不可见的第 41-46 格**——看起来「一直买得进」。

**原版为什么没这个问题**：AddItemBag（ClFunc.pas:165-171）新获得的物品
StdMode ≤ 3（药水/食物等可用物）**优先放进快捷栏 0..5 空格**，快捷栏满才进
背包网格；装备类直接进网格。所以原版网格满时买药水会出现在快捷栏上，
直到 46 全满服务端才拒绝。

**修复**（frontend/scene/PlayScene.mjs `bagUpdate` 分支）：对齐原版
AddItemBag——登录首包不自动填充（服务端 quickSlots 已持久化玩家排布），
之后每次 bagUpdate：新增 makeIndex 且 StdMode ≤ 3 的物品自动放进空快捷格
（含失效引用清理：栏内 makeIndex 指向已不存在的物品视为空格，本地清 0 +
新物品补位），本地乐观置位并发 `quickSlot` 消息持久化；服务端回包覆盖为
权威状态，无回环（第二轮无新增即不再发送）。装备类不进栏（服务端
_quick_slot 本来就拦 StdMode > 3）。

**验证**：
- 离线：`scripts/test_bag_capacity.py` 新增 8/0（BAG_SIZE=46、满包购买拒绝
  且不扣金币、差 1 格能买 1 个再买 2 个被拒、前端 slice(0,40) 存在）。
- 浏览器：`scripts/test_bag_ui.py`（Playwright 3.9）12/0——首包不自动填充、
  3 瓶新药水入栏 0..2、6 格满后第 7 瓶不入栏、装备不入栏、失效引用清空补位。
- 回归：test_slave_protection.py 22/0 不受影响。

## §81 护身符耗尽行为核对：与原版一致，无自动补戴（2026-11-15）

用户问「护身符打完了会怎样？背包里还有会自动换上么？」核对结论：**本服与
原版逐条一致，无需改动**。原版依据（M2Server/Magic.pas）：

- **CheckAmulet（:81-131）**：只查**已装备**的两个槽——U_ARMRINGL（手镯位，
  兼容旧版）与 U_BUJUK（护身符位），StdMode=25 且 Shape 匹配（护身符=5、
  毒药≤2）、`Dura/100 >= 张数`。**不翻背包，不自动补戴**。
- **UseAmulet（:134-144）**：`Dura > nCount*100` 扣减并发 RM_DURACHANGE；
  否则 Dura=0 + SendDelItems **整件消失** + 槽清空。边界：剩 100 耐久
  （1 张）再用 1 张时 `100 > 100` 为 False → 直接删除，不留 0 耐久的空符。
- 耗尽后背包里还有符 → 下次施法 CheckAmulet 找不到 → boSpellFail 施法失败
  （本服提示「没有护身符。」）。**需要玩家手动重戴**（背包双击符 →
  装备到护身符格；前端 TAKEON_POS[25]=9=U_BUJUK，服务端 slot_accepts
  25 → U_ARMRINGL/U_BUJUK，均与原版 IsTakeOnAvailable 对齐）。

本服实现：handlers_combat.py `_find_amulet`/`_use_amulet`（:933-958），
两槽顺序、Shape 判定、`durability//100 >= count`、整件删除+recompute
+bag_update 全部对应。

**验证**：新增 `scripts/test_amulet_depletion.py` 9/0——200→100 正常消耗、
剩 100 用 1 张整件消失、背包符不被自动补戴且耐久不变、施法失败提示、
手动 takeOnItem 重戴后恢复消耗 500→400。

## §82 道士托管：治愈术优先回血 + 灵魂火符远程攻击（2026-11-15）

用户要求「道士托管，优先使用治愈术回血，灵魂火符攻击」。按职业自适应实现
（`pyserver/autopilot.py`，不改档位配置，战士/法师路径零变化）：

**治愈术回血（`_taoist_heal`，新增）**
- `_survive` 血线 ≤ potion_hp 时：道士有蓝且冷却好 → 施治愈术（magicId=2，
  目标空默认自己、**不吃符**——Magic.pas:301-317 SKILL_HEALLING 无
  CheckAmulet）；没学/没蓝/冷却中 → 喝药兜底（药效即时，保命优先）。
- 没药到撤退线（retreat_hp）：道士有蓝仍可治愈自救，不再立即熔断；
  治愈冷却中才停机。
- 治愈成功的 tick 置 `acted=True`，`_fight` 跳过——一个 tick 一个主动
  动作（原版节拍，治愈+火符同 tick 双动作视为加速）。
- MP 不足提示会刷屏（handlers_combat:1002），托管侧先自查 `p.mp < cost`
  才发；冷却自查与 `_cast_magic` 首行拦截（:986，静默）同源真实时钟。

**灵魂火符攻击（`_taoist_fire`，新增）**
- `_fight` 选定目标后：距离 ≤ 8（施法半径，Magic.pas:266）→ 优先火符
  （magicId=13，每次 1 张符）；没学/没蓝/**没符**（`_find_amulet(1,1)`，
  CheckAmulet 只查装备槽，背包不自动补戴）→ 近战兜底。
- 冷却中 → 保持远程站位等下一轮；超射程 → 走近（原 `_step_to` 逻辑），
  进入射程自动转远程。

**验证**：新增 `scripts/test_autopilot_taoist.py` 18/0（真时节拍 600ms
步进）——火符远程不出近战且符 500→400、治愈优先且本 tick 不攻击、
治愈冷却中喝药兜底、卸符贴身近战、没蓝近战不刷「魔法值不足」、超射程
走近进射程转远程、卸符治愈照放。回归 test_autopilot.py 39/0（两次），
test_hosted_fixes.py 12/0。注意 test_autopilot 的「转而击杀同侧可达怪」
用例存在与本改动无关的偶发时序波动（战士路径未动，复现率低）。
