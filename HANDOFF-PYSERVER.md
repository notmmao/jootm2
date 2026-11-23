# jootm2 Python 服务端分支交接文档

> **最后更新**：2026-11-23（§146 gp-bench-3 部署；文档瘦身：§89~§138 归档到 `docs/handoff/archive-2026-11-h.md`，主文件回到 523 行）
> **分支**：`feat/python-server`；远程默认分支 `origin/main`（**没有 master**）
> **本机多个 Agent 协作**：每轮改动在末尾追加 `## §N 标题（日期）`，N 只增不减

## 文档维护规则（每轮必读）

1. **硬上限 700 行**：收尾前跑 `wc -l HANDOFF-PYSERVER.md`；超过 700 行就先把最老的轮次归档，再结束本轮。目标区间 400~700 行。
2. **主文件只留最近轮次**：固定内容 = 规则 / 已知缺陷 / 架构 / 命令 / 部署 / 归档索引 / 阅读顺序；轮次只保留**最近约 8 个 §**，更早的整块搬走。
3. **归档操作**：把最老的一批 `## §N` 从标题到下一个 `## ` 之前（含前后分隔线）完整剪切，追加到 `docs/handoff/archive-<年>-<月>-<字母>.md` **末尾**；归档只读，不改写历史正文。
4. **命名与大小**：`archive-<年>-<月>-<a|b|c…>.md` 按时间顺序；单文件约 800~1500 行（20~30 个 §），满了就开下一个字母，不要无限追加。
5. **同步索引**：搬走后更新 `## 归档索引` 一行（文件 → 覆盖的 § 范围与主题）；主文件不留占位符，也不复用 § 编号。
6. **别把规则搬走**：§0.5（强制规则）与 §0.6（已知缺陷）永远留在主文件；发现新坑先加规则，再记录本轮。
7. **行文正常换行**（用户约定 2026-11-17）：一段文字写成一整行（靠编辑器软换行显示），**不要逐 40 字手工断行**；代码块/表格/引用行/列表项除外。查证过全库无行宽规定，断行是历史习惯。

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

CLI：`--config --host --port --db --gamedata --web --resources --tick-ms --no-resource-index --version`。环境变量：`JOOTM2_PORT / JOOTM2_HOST / JOOTM2_DB / JOOTM2_WEB_ROOT / JOOTM2_RESOURCES / JOOTM2_GAMEDATA / JOOTM2_ROOT / JOOTM2_CONFIG`。启用端点：`/` 静态、`/ws`、`/resources/*`、`/api/status`、`/api/players`、`/api/migration`。

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
- **一键脚本（2026-11-16，§88）**：`bash scripts/deploy_image.sh` 把上述流程（gen_version → build 双标签 → push → 阿里云 pull/tag/up → `/api/version`+`/api/status` 验证）串成一条命令，**只推镜像、无 rsync**；改了 gamedata 时仍需按 §21.8 追加 gamedata rsync。
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
- **⚠ gamedata 挂载覆盖镜像（2026-11-15 实测坑，用户反馈「调整没生效」）**：compose 把宿主 `./gamedata:/app/gamedata` 挂载进容器，**镜像里的新数据会被宿主旧目录整体压住**——只走镜像流程时 MonGen/MonItems/Market_Def/mud2 CSV（Magic.csv 等）**全都不生效**。每次改了 gamedata 数据后部署，必须追加：`rsync -az --delete --exclude 'Mir200/Map' --exclude '.gbk-backup' gamedata/ root@aliyun:/root/jootm2/gamedata/` → `docker compose restart jootm2` → 容器内 grep 抽查（如 MonGen D011 站点行、半兽勇士诱惑之光）+ `/api/status`。**绝不同步 `data/`（账号/人物数据库）**；宿主 `Map` 空目录无害（容器里被 `./resources/Map` 挂载覆盖），本地 junction 在 Windows rsync 下会以「symlink copied as ordinary file」告警、不会复制 204MB 地图。
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
| `docs/handoff/archive-2026-11-f.md` | §69~§88：第二轮数据清理 / 技能修炼点 / 骷髅战士掉落 / 新手线掉落整表恢复与商店分档 / Magic.csv 全列对齐 / 高级之家测验与托管六问题 / 尸体渲染 / 大地图入口标注与过滤（原文两节重号 §79，逐字保留）/ 误伤宝宝豁免 / 治愈自家宝宝 / 召唤熟练度 / 半兽古墓刷怪 176 / 背包满快捷栏自动填充 / 护身符耗尽核对 / 道士托管治愈+火符 |
| `docs/handoff/archive-2026-11-g.md` | §110（重号，触屏版）：移动端触屏控制移植自幸存者分支（与主文件原 §110 放风筝节重号，历史遗留，逐字保留） |
| `docs/handoff/archive-2026-11-h.md` | §89~§138：托管优化 / 头顶名字血条 / 女法师光头 / 挖肉距离 / 杂货铺审计 / 补给闭环 / 玛法领路人 give / GAMEDATA_GUIDE / 商人改名 / mir2-resource-converter / wiki 迁 list2 / 任务变量不落库 / 交易+组队 / 聊天气泡密语 / 弹窗数字键冲突 / PvP 尸体与 PK 惩罚 / DUserState1 / 托管四连调 / 大地图自动导航 / 放风筝 / 离线修炼一期二期 / GM 观战 / 施法保真 / 批量修复 / 名单地图名 / 同图聊天 / 回蓝偷跑 / 交易组队原版 UI / Bot 技能 / 宝宝掉落归属 / Bot 漫游 / 周末双倍 / 物理像素呈现 / 地图分辨率 / 太空滑步 / balance 三修 / 沉默剥离与黑名单 / wiki 接入剥离 / 盟重多图 / 房间图小地图 / 声效名字血条排查 / 离线长跑模拟 / 玛法领路人新增（2026-11-16 ~ 2026-11-21） |

> 早期章节（§1 分支提交表 / §4 Java 版改动清单 / §5 坑 / §6 旧待办 / §7 合并注意 / §8 阅读顺序 / §9 数字速查）随重构裁剪出主文件：§4 的合并冲突文件清单与 §7 合并注意事项在执行 `git merge origin/main` 前仍需参考，完整原文可从 git 历史（本重构提交的上一版）找回。

## 给下一位 agent 的阅读顺序

1. 本文 §0.5 强制规则 与 §0.6 已知缺陷 —— 先知道哪些不能碰、哪些不是 bug
2. `docs/handoff/archive-*.md` 对应章节 —— 追历史根因时再查
3. `HANDOFF.md`（远澜原文）—— 玩法数值、协议字段、特效帧号的权威参考
4. `pyserver/service.py` 的 `_handlers` 与 `MIGRATED_TYPES` —— 消息分派全貌
5. `pyserver/world.py::tick` + `world_extra.py` —— 世界循环
6. `scripts/` 下 `test_*.py` / `compare_stacks.py` + `scripts/golden/` —— 回归基线
7. `git log --oneline -20` —— 最新提交即版本（`/api/version` 同源）
8. `docs/GAMEDATA_GUIDE.md` —— 动 gamedata 前必读：目录结构 / 文件消费点 / 名字连接链 / 未消费文件与已知缺口（§97 新增）

---

## §139 阿里云线上应急：建 GM 账号 + 救卡图角色 Garden（2026-11-22）

**背景**：用户反馈「人物卡住没回城，GM 能解决么，去创建 GM 账号」。

**关键机制澄清**：GM 权限是硬编码白名单 `config.py:16 GM_ACCOUNTS = frozenset({"player"})`，登录时按**账号名**判定（`service.py:440`），不是数据库字段。建 GM 账号 = 在生产库注册用户名为 `player` 的账号。GM 命令 `@移动 x y`/`@移动 地图名 x y`（`service.py:674`）**只能传发命令的 GM 自己**，救不了别人账号下的角色——所以救 Garden 走的是直接改库，不是 GM 命令。

**卡图根因**：Garden（账号 24 / 2014）卡在地图 `01142` 的 (13,12)。`01142` 在 `MapInfo.txt` 里**没有登记**，属于不存在/无门的地图，角色进去后没有出口 → 回不了城。这类卡图 GM 命令也救不了（进不去那张图就用 `@移动` 也能救，但玩家自己不会）。

**操作（阿里云生产库，先备份）**：
1. 备份：`sqlite3 data/jootm2.db ".backup data/jootm2.db.bak-20261122-0735"`（含 WAL，备份文件 487KB）
2. 走容器内 `python` + 项目自己的 `Database.register('player','123456')` 建 GM 账号（id=25，`authenticate` 验证 True）；密码 SHA-256(salt+password)，salt 随机
3. `UPDATE characters SET map_name='0', x=289, y=618, home_map='0', home_x=289, home_y=618 WHERE name='Garden'`（1 行）
4. 验证：GM 账号 25 存在；Garden map=0 xy=289,618 hp=62；/api/status 200 在线 3（bot）

**如需重做**：账号名必须是 `player`（白名单写死）；`scp` 只同步 `gamedata`（compose 卷挂载 `./gamedata:/app/gamedata`），代码改动才走 `deploy_image.sh` 镜像。改库前务必 `.backup`，别直接 copyfile（WAL 会丢）。

## §139b 托管菜单布局优化：折叠成小按钮 + 状态条贴底（2026-11-21，用户要求）

**需求**：用户反馈「托管菜单布局优化，当前状态和菜单有点影响视野」。给三个方案（全折叠 / 整体贴底 / 右上紧凑）后用户选**折叠成小按钮**。

**现状问题**：两块元素都压在画面区中上部（`HUD_Y=349`，上方 0~349 是画面）。托管菜单 `autoBar` 在 `(496,126)` 宽 296×22，一行塞了开关/档位/不挖肉/离线四个控件；状态条 `autoPilotStatus` 在 `(170,96)` 宽 460，托管时横贯视野中上部。

**改动（全在 `frontend/scene/PlayScene.mjs`）**：
- `autoBar` 收成右上角 `(690,124)` 宽 102 的 split button：左侧 `#autoPilotBtn` 开关（80px，**id/语义/P 键/服务端协议全部不变**），右侧新增 `#autoPilotMenuBtn`（22px `▾`/`▴`）切换菜单。
- 档位下拉、不挖肉勾选、离线入口搬进折叠面板 `#autoPilotMenu`（默认 `display:none`，`right:0;top:23px`），原 id 全保留。点离线入口时自动 `_closeAutoMenu()`，避免和离线面板（z-index:70）叠加。
- 状态条 `#autoPilotStatus` 从 `(170,96)` 460×20 挪到 `(0,329)` 800×20，贴画面底边、只留一条 `border-top` 细带；文案与显示逻辑（`_renderAuto`）零改。
- 联动更新 3 个浏览器脚本：`test_autopilot_ui.py`（不挖肉勾选前先点 `#autoPilotMenuBtn`）、`test_afk_ui.py` + `snap_afk_guide.py`（进游戏后展开菜单才能看到 `#afkBtn`）；`snap` 关面板改用面板自身 `div[title="关闭"]`（`afkPanel` 盖住折叠入口，点不到 `#afkBtn`）。`test_corpse_fx.py` 只点常驻开关，无需改。

**验证**：`node --check` + `py_compile` 语法过；`test_autopilot_ui` 13/13、`test_afk_ui` 13/13 全过；`snap_afk_guide` 四张图正常产出。截图目视确认：右上角只剩紧凑开关+▾、画面中部视野清空、状态条贴底不再横贯中上部。

**本轮改 `PlayScene.mjs` + 3 个测试脚本 + 重生成 `docs/autopilot-hud.png` / `docs/images/afk-guide/*.png`（标签 `[前端]`），未提交。** 纯前端改动，服务端协议不变；部署只需更新镜像（gamedata/config 未动）。

---

## §140 玩家一批对齐/功能修正（2026-11-22：GM解卡 + 职业之家卡死 + 稻草人火焰 + PK衰减配置）

用户一批 5 类诉求，全部查过火炬 Delphi 权威源码后落地。按性质分 4 块，代码文件有交叉，提交按标签拆。

### A. GM 解卡三件套（用户要求设计）
- **背景**：`@移动` 只能传 GM 自己，救不了别人角色。线上 Garden 卡图只能直接改库。
- **实现**：`service.py` 加 `@解卡 角色名` / `@卡死扫描` / `@解卡 all`，双路（在线 `world.player` → SpaceMove+快照+广播+立即 persist；离线 `db.find_character` → 改库）。
  - 卡死判定 `_is_stuck()`：地图未登记（get_or_load_map=None）/ 坐标越界 / 坐标不可走。
  - 落点 `_rescue_dest()`：优先 home_map/home_x/home_y（校验存在+可走+`_free_near` 邻近空格），无效回比奇出生点（用户选「智能回家点」）。
  - `db.py` 加 `relocate_character()`（只更新 map_name/x/y 三字段，避免整行覆盖并发改动）+ `all_characters()`（扫描用轻量列）。
  - 在线解卡后立即 `persist()`，否则下次定时存档会把旧坐标写回、角色又卡回去。
- 新回归 `scripts/test_gm_rescue.py` 17/0。

### B. 三个职业之家测验任务卡死修正（用户：修正任务，不刷怪）
- **卡死根因**：三张任务图 `01142`(武士)/`01152`(法师)/`01132`(道士) 全 `exits=0` 无出口门，MapInfo 带 `NORECALL`（回城卷无效），唯一出路 `TimeRecall` 定时器**不写存档**（`persist()` 无此字段）——玩家断线/下线重登后 `time_recall=None`，永久锁死。Garden 就是撞这个坑的离线角色。
- 额外 bug：武士家刷的 `暗之沃玛护卫` 是死引用怪（Monster.csv 无此模板，只有 `火焰沃玛2`）。
- **修法**（用户三选一选定「去掉传送+刷怪」）：三个门外导师脚本的 `@Q706_1` 段从「清场+刷怪+TimeRecall+map 传送」改成 `#IF checkbaggage / #ACT give 技能书 + SET [757]`，复用屋内 NPC 已验证的发书结构。屋内 NPC 与任务地图文件保留不动（无人再进，无害）。保留任务链与剧情（7级→导师→对话发书→双击学技能→书店）。
- 改 `9Eht-高级道士-0113.txt` / `9Ewi-高级魔法师-0115.txt` / `9Ehw-高级武士-0114.txt`；重写 `test_npc_home_quest.py`（顺带修了基线就坏的 `npc_by_script` 精确匹配→前缀匹配，原测试因 NPC script_name 带中文后缀从来跑不通）23/0。

### C. 稻草人死亡火焰爆炸恢复（用户：以前会爆炸）
- **原版依据**：`M2Server/ObjBase.pas:2800` 怪死当下 `SendRefMsg(RM_DEATH, lparam3=1)` → `:12583` 转 `SM_NOWDEATH`；客户端 `AxeMon.pas:351-358` 置 `BoUseEffect:=TRUE` 播完整 ActDie 动画 + `THuSuABi.LoadSurface`(:752) 叠 `DEATHFIREEFFECTBASE=2860` 火焰。稻草人 Race=18≠22 命中。后进视野看到尸体才走 `SM_DEATH` 定格（:1865 lparam3=0），不叠火焰。
- **bug**：项目前端怪死一律设 `_action='death'` 定格，从不设 `die`，火焰（`ActorSprite.mjs:190` 的 `action==='die' && raceImg===18`）从未触发。
- **修法**：`PlayScene.mjs` 怪死时 `raceImg===18` 设 `_action='die'` + `_dieUntil=now+10*120`（MA14.die 10帧×120ms）；到期转 death 定格。同步放过两处会把 die 腰斩成 death 的归一逻辑（渲染循环 4866 段 + entityAdd(corpse) 分支）。Playwright 实测：死亡瞬间 `_frameSpec` = `mon3/2780 + mon3/2860@lighter`，到期后 = `mon3/2789` 无火焰；普通怪不受影响。

### D. PK 衰减缩短（用户：原版时间太长，确认后主动偏离）
- 查证原版 `ObjBase.pas:7719` = `2*1000*60`（2分钟/点），项目实现与之一致，黄名 200 分 / 红名 400 分清白。用户确认偏长，选「加配置项默认 30 秒/点」。
- `config.py` 加 `pk.decay_interval_ms`（`pk.decayIntervalSec`，默认 30，设回 120 恢复原版）；`world.py::_tick_pk` 改用配置值；`config/server.properties` 加注释行。`config.py` load 里曾误写 `max(1000, 30)` 把 30 秒钳成 1000 秒，已修为 `max(1, ...)`。
- 30 秒/点 → 黄名 50 分 / 红名 100 分清白。`test_pk_punishment.py` 改为跟随配置 22/0。

### 验证
- 服务端：`test_pyserver` 128/0、`test_all_messages` 95/0、`test_tick_systems` 180/0、`test_pk_punishment` 22/0、`test_gm_rescue` 17/0、`test_npc_home_quest` 23/0。
- 前端：`test_bigmap_nav` 19/19、`test_repair_ui` 14/0、`test_bag_ui` 12/0。`test_corpse_fx` 6/1 的 1 FAIL 经 stash 比对确认是基线既有（`--no-resource-index` 下纹理未加载），与本次无关。
- `git diff --check` 干净；临时诊断脚本/db 全清理。

### 部署提醒
- 本轮跨 服务端代码 / 前端 / 配置 / gamedata 四类，**服务端代码与配置（`service.py`/`db.py`/`config.py`/`world.py`/`server.properties`）要重启并更新镜像**；gamedata 三脚本走卷挂载可直接 scp 覆盖；前端 `PlayScene.mjs` 需 Ctrl+F5。
- 阿里云 PK 配置改动要生效同样需重启容器。
- 提交标签建议拆：GM解卡 `[逻辑]`、职业之家 `[数据]`+`[测试]`、稻草人 `[渲染]`、PK `[调整]`、文档 `[文档]`；按规范每提交只带一个标签。

**本轮未提交，等用户确认。**

## §141 PK 衰减再缩到 15 秒 + 平板托管后无声修复（2026-11-22）

### A. 为什么阿里云"没更新过来"（其实是生效了）
- 用户按部署指令跑完后反馈"没更新"。远程核验 `/api/version` = `dc2e9a8`（builtAt 08:12:41，正是那次部署），容器内 `pk.decayIntervalSec=30` / `config.py=30_000`——**30 秒/点已生效**。
- "看起来没变"是因为红名 pk_point=200，按 30 秒/点要 200×30s=100 分钟才归零变白，配置改了≠名字立刻变白；已积累的 pk 点要在线一点点扣（离线不补算）。先讲清这点再谈改数值。

### B. PK 衰减默认值 30 → 15 秒（用户要求）
- `pyserver/config.py` 两处（dataclass 默认 `15_000` + load 里 `as_int(...,15)`）、`config/server.properties`（`pk.decayIntervalSec=15`）、`world.py` 注释、`scripts/test_pk_punishment.py` 断言同步改。红名 200 点 → **50 分钟**清白，黄名 100 点 → 25 分钟。原版仍 120 秒/点（设回即恢复）。回归 22/0。

### C. 平板（iPad）打开托管后永久无声——SoundManager 两处移动端坑
- **坑1（主因）**：`install()` 的 `pointerdown`/`keydown` 解锁监听用了 `{once:true}`，首次手势后就移除；而 `_playUrl` 收到 `NotAllowedError`（移动端切后台/来电/低电量/并发过多会撤销音频权限）会把 `unlocked=false`。两者叠加 → 再次点击已无监听可重新解锁 → **永久静音**。托管面板点击恰好是解锁后的高频操作，容易踩中。
  - 修法：去掉 once，改常驻监听（幂等重试）+ 加 `touchstart`（老 WebView 只发 touch）+ 加 `visibilitychange` 回前台续播 BGM；`_unlock()` 已解锁时也借每次手势尝试 `_startBgm()`（BGM 被系统 pause 后能恢复）。
- **坑2**：音效槽 `voices` 只靠 `ended` 回收；iOS 上 audio 被中断时 `ended` 不触发 → voices 只增不减，撑满 `maxVoices=24` 后 `play()` 开头直接 return，所有音效静默。托管持续打怪音正好触发。
  - 修法：给每个非 bgm voice 加 15 秒 `setTimeout` reaper 兜底回收；`cleanup` 加 `done` 幂等标志，避免重复触发误删。
- 新回归 `scripts/test_audio_mobile.mjs` 6/6（Node 桩模拟 NotAllowedError / ended 不触发 / 手势重解锁 / 回前台续播）。原 `test_audio.mjs` 18/19 的 1 FAIL 经 stash 确认基线既有（spellCast 断言与本次无关）。
- **部署**：前端 `SoundManager.mjs` 走镜像 + 浏览器强刷；PK 代码/配置走镜像并重启（gamedata 无改动，本轮不用 scp）。

**本轮未提交，等用户确认。**

## §142 平板操作优化：说话按钮 + GM 辅助菜单 + 关闭体验（2026-11-23，ima 待办前两条）

**分支**：先在独立分支 `feat/touch-gm-menu`（从 `feat/python-server@b3ecd7f` 分出）开发，
后合并回 `feat/python-server`（与远端 20 个新提交一起，HANDOFF 冲突已手工解、本章节号原顺延至 §138，与本地新增 §138-141 撞车，合并后改为 §142）。三个提交：
`6c5c6b0 [逻辑]` 初始实现、`0288abc [修复]` 布局修复、`1dbad1a [修复]` 关闭体验优化。
仅改前端 `frontend/scene/PlayScene.mjs` + `frontend/input/TouchControls.mjs`，未动服务端。

**背景**：用户 ima 笔记 2026-11-22 待办前两条「gm 辅助菜单，简化操作」「平板操作优化：
当前无法说话」。云电脑已发布公网服务（链接见 §126 之前发布记录，链接不变），本地
浏览器可见。GM 账号 `player`（配置 `GM_ACCOUNTS={"player"}`，已存在，密码 `smoke123`）。

**一、平板说话（原「无法说话」）**：根因是聊天框只靠物理键盘回车/斜杠唤出，平板无键盘
唤不出。`TouchControls` 新增「说」按钮（`#tc-chat`）→ `onChat` 回调 `_openChatInput()`。
触屏下把输入框临时移到逻辑坐标 (24,270) 宽 596 高 38、字号 16px（避开软键盘遮挡 +
防 iOS Safari 聚焦自动缩放），关闭时复位到原版 (208,580)。

**二、GM 辅助菜单**：服务端已支持 @命令但需手打，做成图形面板降低操作成本。
`self.gm` 为真才可用（服务端 `_gm_command` 仍校验 is_gm，前端不追加特权）；`G` 键 /
系统菜单入口打开，命令统一走 `conn.send('say',{text:'@'+cmd})`。分区：快捷（满血蓝/
+10万/+100万金币/回城）、等级、传送、制造、刷怪、系统（托管/机器人/离线代理/帮助），
共 15 按钮 + 8 输入框。命令覆盖面已核对服务端 `_GM_HELP`，日志无异常。

**三、关闭体验（用户实测反馈「说完退不出 / GM 菜单关不掉」）**：
- 聊天框：软键盘回车常不触发 keydown → 加可见「发送」「×」按钮（触屏显示）；Enter
  提交抽 `_submitChat()`，加 `isComposing`/keyCode 229 保护防中文输入法选字误发。
- GM 面板：标题栏右上角加 × 按钮；纳入 `_closeTopWindow()` 使触屏「单」按钮可关；
  打开 GM 时互斥收起聊天框。
- GM 面板首版布局 bug（分区标题错位、系统按钮被裁）已修：分区各自独立行，面板
  420×540 top=40。输入框字号 12→16px，提示文案适配触屏。

**验证**：Playwright 两轮，首轮 21/21（触屏按钮/输入框位移复位/G 键开关/按钮实发 @满/
无 JS 异常），次轮 17/17（发送×按钮/×关闭/统一关闭链/互斥/输入法保护/无 JS 异常）。
截图复核 `shots/02_gm_panel.png`、`06_touch_chat.png`、`07_chat_buttons.png`、
`08_gm_close_x.png`。已重新发布上线（公网链接不变），线上确认含新代码、状态正常。

**注意**：公共资源仍为 1.76 客户端（缺 prguse3.wil 等，仅影响 F10 底图与部分怪物外观，
不影响玩法）。`PlayScene.mjs` 与远端 `dc2e9a8` 的丢金币/仓库 UI 改动自动合并成功，已回归验证。

## §143 八项功能/对齐批量改动（2026-11-23，用户一批需求）

用户 9 条需求（第 3/9 条重复，合并为 8 项）。动手前用 AskUserQuestion 拍板 4 个架构决策：
挂机后台=玩家自助（GM 兼全服）、模板角色=预置测试账号、登录=token 免密、精英比例=5%。

1. **去掉比奇城重叠的网络文明宣传员**：真正的重叠在 `MerChant.txt:29`
   `9snpc1-网络文明宣传员`（地图0，326,261）——上次只按用户「原地再放一个玛法领路人、
   其他不动」在 Npcs.txt 加了同坐标领路人，MerChant 里的宣传员没删，两者叠一起。
   注释掉该行（解析器跳过 `;` 开头行）。宣传员是纯公约 NPC，发金币在独立的
   `Npc_Def/玛法领路人-0.txt`，删它不影响领金币。

2. **还原原版名字显示 + 菜单切换**：查火炬 `DrawScrn.pas:283-354` 确认原版
   名字只画选中目标(FocusCret)+选中自己、位置 `SayY+30`=格顶-17（躯干）、无等级、
   血条 `SayY-10`=格顶-57 且**无数字**。`MapActor._drawHeadHud` 加 `hud.nameStyle`：
   - 'original'（**默认**）：选中才显示名字、无 Lv、名字在躯干 -17、血条显示 HP 数字
     （用户额外要求）、气泡跟随到 -33。
   - 'current'：保持旧改良（头顶 -71 全显示、带 Lv、无数字）。
   PlayScene 加 `_toggleNameStyle()` + localStorage `jootm2.nameStyle`，Esc 菜单主开关。
   顺手修快照未传 `openHealth` 的既有缺陷。新增 `scripts/test_name_style.mjs` 12/12。

3. **玩家自助挂机网页后台**：新建 `frontend/afk.html`（独立页，访问 `/afk.html`，
   静态服务 web_root=frontend）+ 4 个 REST 端点（service.handle_admin，复用登录 token）：
   `POST /api/dash/auth`（token→账号+角色列表+isGm）、`GET /api/dash/state`（charId 单角色
   或 GM `all=1` 全服）、`POST /api/dash/action`（启停/暂停/切档/设计划）。越权校验：
   角色必须属于 token 账号且在线。GM 按 `GM_ACCOUNTS`（player）判定。
   `scripts/test_afk_dashboard.py` 19/19（起真实服务端，含越权/GM/启停）。

4. **精英怪（5%）**：新机制，原版无。`config/server.properties` 加 `elite.rate=0.05`、
   hpMult=2、dcMult=1.5、expMult=3。Monster 加 `elite` 字段；`_spawn_monster` 天然刷怪
   掷骰（hp≤0/exp≤0 的摆设怪跳过），HP/exp 改实例值、DC 加成走 `slave_dc_bonus`
   （复用 roll_attack，不改攻击链路）；brief_json 下发 elite，前端名字加「精英·」前缀+
   绿色 `#5ef08a`（两种风格都生效）。`scripts/test_elite_monster.py` 18/18。

5. **离线挂机修装备 + 药水解捆**：`afk.py` 补给 SELL→BUY 之间插入维护动作
   `_supply_maintain()`：背包内耐久<50% 的可修装备找 repair NPC 走近后调既有
   `srv._repair_item`；StdMode31 且解捆目标为药水的卷包就地调 `srv._use_item`
   （解捆是双击物品，与 NPC 无关）。每轮用 `supply_repaired/supply_unbound` 防重复，
   无修理商人则跳过不卡死。开关 `afk.supplyRepair`/`afk.supplyUnbind`（默认1）。
   `scripts/test_afk_maintain.py` 17/17。

6. **法定节假日双倍经验**：新建 `pyserver/holiday.py`，内置 2026 国务院放假日期
   （国办发明电〔2025〕7号，联网核实：元旦/春节/清明/劳动/端午/中秋/国庆放假区间，
   调休上班日不加成）+ 兜底固定节日 + 自定义 `holiday.dates`。只加经验不加爆率，
   与周末倍率**取最大值**（不叠乘成×4）。config 加 holiday.enabled/expRate/force/dates；
   world_extra.kill_exp 用 `max(weekend_exp, holiday_exp)`；service 登录提示 + 状态翻转
   全服广播沿用周末链路。`scripts/test_holiday_exp.py` 26/26。

7. **预置各等级模板角色**：`scripts/seed_template_chars.py`（幂等）往库建账号
   `template/template123` + 1/7/17/25/35/40 级 × 三职业 18 个角色，装备按档位从
   StdItems 动态查（缺名跳过）、技能按职业等级配、赠 240 分钟离线时长、金币按等级给。
   实跑临时库验证 18 角色可登录进游戏（stats 显示 35 级 HP128/128），二次跑全跳过。

8. **用户名保存 / token 免密登录**：新建 `pyserver/auth_token.py`（HMAC-SHA256 签名
   无状态 token，30 天有效，密钥可用 `JOOTM2_TOKEN_SECRET` 覆盖，不落库不改 accounts
   表避免与 Java 共用库冲突）。WS `login` 成功改走 `_finish_login` 回包带 token+username；
   新增 `tokenLogin` 消息校验 token 后登录。db 加 `account_by_id`。前端 LoginScene
   读 localStorage `jootm2.authToken`/`jootm2.lastUsername`：预填用户名 + 自动 tokenLogin，
   失败静默清 token 回手输（不弹错误框）。`scripts/test_auth_token.py` 9/9。

**验证**：本轮新增 6 个测试全绿（12/18/9/17/19/26=101 项）；服务端核心回归
128/95/180/17/23/22 全过；JS/Python 语法、`git diff --check` 干净。

**部署**：本轮跨服务端代码/配置 + 前端 + gamedata（MerChant.txt）三类：
- gamedata `MerChant.txt` 走卷挂载，需单独 scp 覆盖；
- 服务端代码/配置 + 前端 `afk.html`/`PlayScene.mjs`/`MapActor.mjs`/`LoginScene.mjs`
  走镜像，`bash scripts/deploy_image.sh`；
- 模板角色需在服务器上跑 `D:/Python310/python.exe scripts/seed_template_chars.py`
  （容器内路径 /app，用容器内 python）才会写进线上库；
- 前端仍需 Ctrl+F5 强刷。

**注意**：合法节假日表目前只内置 2026 年（国务院通知）；2027 年需更新 holiday.py
或配 `holiday.dates`。精英怪是用户主动新增、偏离原版（原版无此机制），数值可配。
原版名字风格现在默认开启，若用户想回改良风格 Esc 菜单切「名字风格：当前改良」。

## §144 E2E 全链路验证 + GM 刷怪走统一构造路径（2026-11-23）

用户要求本地起服务端做 E2E 并截图，另在 CLAUDE.md 加部署规则。

1. **E2E 脚本** `scripts/e2e_session_20261123.py`（3.9 Playwright + 3.10 服务端，
   独立临时 db `data/_e2e.db`、elite 专用配置 `data/_e2e_elite.properties`
   只含 `elite.rate=1.0`）：**19/19 PASS**。覆盖 §143 全部八项：真实输入框登录 +
   token 落 localStorage、名字风格默认原版、GM @刷怪 稻草人×5 全精英（选中后
   绿色「精英·稻草人」躯干名 + 血条数字 50/50，未选中怪不显示名）、Esc 菜单
   切换风格双向生效、token 冷启动自动免密登录（无错误弹窗）、afk.html 后台
   （自动带 token→连接→刷新→开托管→GM 全服 2 行表格）。截图 9 张在
   `data/e2e_shots/`（01 选人～09 GM 全服视图）。
   坑：wait_for_function 在 __sceneManager 挂载前求值会抛 `reading 'current'`，
   条件要加 `window.__sceneManager &&` 前置守卫；后台点「开启托管」后 dashMsg
   会被随后的 refresh() 覆盖，断言应看状态卡片而不是消息行。

2. **修复 GM @刷怪 绕过统一构造路径**（service.py `_gm_command`）：旧代码手搓
   `Monster(...)`，漏掉 `init_meat_quality`/`_init_race_traits`/精英掷骰/掉落
   初始化（稻草人挖肉、精英标记都不生效）。改调 `w._spawn_monster(p.map, t, x, y)`
   + `broadcast_entity_add`。回归 gm_rescue 17/0、pyserver 128/0、all_messages 95/0。
   E2E 里 @刷怪 能出精英正是此修复的验证。

3. **CLAUDE.md 部署规则新增一条**（用户 2026-11-23 明确要求）：凡改动涉及
   资源/配置文本等不随镜像走的内容（gamedata 卷挂载、独立挂载的
   server.properties、data/、resources/），交付部署时除 `bash scripts/deploy_image.sh`
   外**必须同时逐文件给出 scp 指令**并提醒重启容器。

**本轮 §143+§144 改动均未提交。** 部署时 scp 清单（gamedata 卷挂载）：
`MerChant.txt`（去宣传员）；其余（服务端代码/配置/前端/afk.html）全走镜像。

## §145 模板角色拆分 9 账号（2026-11-23，用户指出单账号 18 角色违规）

服务端 `MAX_CHARS_PER_ACCOUNT=2`（选角界面按 2 设计），§143 脚本直写 DB 绕过限制、
单账号塞 18 角色不合理。`seed_template_chars.py` 改为 **tpl1..tpl9 / template123**
共 9 账号、每账号 2 角色（按等级×职业展平两两分组），并加旧 `template` 账号残留提示。
临时库验证 18 角色 + 幂等 + WS 冒烟（tpl7=2 角色，模板法师35 进游戏 Lv35 HP118）。
默认库和线上库均未写过旧 template 账号，无需清理。§143 中「template/template123 单
账号」描述以本节为准。

## §146 gp-bench-3 部署 7c74f51（2026-11-23，用户点名只更 gp-bench、阿里云不动）

- 走「代码同步 + 服务器本地 build」路径（rsync 坏了，见下）：
  `tar --exclude=resources --exclude=data --exclude=.git … -czf`（**必须排除
  `./gamedata/Mir200/Map` junction**，否则会以 symlink 入包、解包覆盖服务器真实
  目录风险）→ `scp` 12MB 包 → 服务器 `tar -xzf` → `DOCKER_BUILDKIT=1
  ~/.local/bin/docker-compose build jootm2` → `up -d`。
- **坑：本机 Windows rsync 3.5.0 对 gp-bench-3 全线 broken pipe**（code 53，连
  单文件都失败；与排除规则/协议版本无关，疑似 Git Bash 构建的 rsync 对 SSH 管道
  不兼容）。以后 gp-bench-3 代码同步直接用 tar+scp，不要再试 rsync。
- 验证全绿：`/api/version=7c74f51`（LAN 与 `https://mir2.notmmao.com` 隧道一致）、
  `/api/status` 200、`afk.html` 200、容器内 `pk.decayIntervalSec=15`、宣传员已注释、
  `smoke_ws.py --host gp-bench-3.hil` 8/8。
- 阿里云保持 `dc2e9a8` 未部署本轮内容。
- **模板角色已种入 gp-bench-3**（2026-11-23 用户点头）：`~/jootm2/data/jootm2.db` 是
  root:root 644，ota 直接跑脚本报 `attempt to write a readonly database`（无免密
  sudo）。解法：`docker cp` 脚本进 jootm2 容器（Python 3.12，纯标准库可跑）→
  `docker exec jootm2 python3 /app/scripts_seed.py --db /app/data/jootm2.db`，
  新建 18 角色成功、WAL 由运行中的服务自然可见（无需重启）。线上验证
  `POST /api/dash/auth {tpl7/template123}` 200。⚠️ 脚本是 `docker cp` 进容器的临时
  `/app/scripts_seed.py`，容器重建即消失；宿主机 `~/jootm2/scripts/` 也留了一份副本。

## §147 阿里云部署 c37d4b1（2026-11-23，用户要求查在线、无人则部署）

- 查在线：`/api/status` 显示 3 人，`online_count()` 不区分 Bot，对照服务器
  `gamedata/bots.json` 恰好 3 个 → **真实玩家 0**，符合部署条件。
- `bash scripts/deploy_image.sh` 一把过：ACR digest 正常，容器重建，
  `/api/version=c37d4b1`、`/api/status` 200、`smoke_ws.py --host 139.224.24.88` 全通。
- gamedata 卷挂载需单独 scp（dc2e9a8..HEAD 增量 5 文件）：`MerChant.txt`（宣传员注释）、
  `Npcs.txt`（玛法领路人）、`Market_Def/9Eht-0113 / 9Ehw-0114 / 9Ewi-0115`（职业之家商店）
  → `scp … root@aliyun.notmmao.com:/root/jootm2/gamedata/Mir200/Envir/[Market_Def/]`
  → `docker restart jootm2`（NPC/商店构造时加载）。
- 容器内抽查：宣传员 9snpc1 行已带 `;` 注释、`pk.decayIntervalSec=15` 在镜像 config 生效；
  `elite.rate` 走代码默认 0.05。gp-bench-3 模板账号（tpl1..9）本轮也同步可用（镜像同版本）。

## §148 选角「退出」真正登出：清免密 token（2026-11-23，用户反馈）

**问题**：自动登录后，选角界面点「退出」回到登录界面，LoginScene 检测到
localStorage 还有 token 又 `tokenLogin` 自动登回去——退出无效。

**修复**：
- 新文件 `frontend/core/authToken.mjs`：`LS_TOKEN`/`LS_USER` 键名 + `clearAuthToken()`
  独立小模块（LoginScene 与 SelectCharScene 互有引用，放 core 避免循环 import）。
- `LoginScene.mjs`：常量改为从 core/authToken import（行为不变）。
- `SelectCharScene._exit()`：`onReturnLogin` 前调 `clearAuthToken()`——只清 token，
  **用户名记忆保留**；下次手动登录后服务端重新签发 token。
- 语义边界：游戏内菜单「退出角色」（回选角）与「退出游戏」不动，保持登录态，
  与原版 Alt-X 一致；只有选角界面的「退出」（原版关客户端）才真正登出。

**验证**：`scripts/test_exit_logout.py`（3.9 Playwright + 3.10 独立 db）15/15：
表单登录→退出清 token 且 2.5s 内未自动登回→手动重登签发新 token→注入 token 冷启动
自动免密→退出再清→再登恢复；「退出角色」保留 token；无页面 JS 报错。
退出热区定位：SelectCharScene._buildButtons 最后 append 的 cursor:pointer 热区
（按钮 72），取 client 矩形中心真实 click。

纯前端改动，无 gamedata/资源；部署只需镜像。
**已部署阿里云**（2026-11-23 18:23，在线 3=3 Bot 无人时段）：`/api/version=619d2f5`、
容器内 authToken.mjs 在位、smoke_ws 全通；gp-bench-3 未点名不动。

## §149 衣服耐久掉太快：StruckDamage 磨损槽位对齐火炬（2026-11-23，用户反馈）

**根因**（对比火炬 `M2Server/ObjBase.pas:3464-3575 TCreature.StruckDamage`）：
旧实现受击磨损循环 `for slot in range(len(equipment))`（0..12）**把 U_DRESS(0)
再次卷入 1/8 概率磨损**——衣服每次被命中期望磨损 9.5×1.125，比原版多耗 12.5%，
且还错误地磨损 U_CHARM(12)。原版 :3527 是 `for i:=1 to 11`（从 1 起=衣服只吃
:3487 的固定磨损一次；`i<>U_BUJUK` 符不磨；12 不在循环内）。

**修复**（world.py `wear_struck`）：循环改 `range(1, 12)` + 跳过 `U_BUJUK(9)`
（world.py 新增常量 U_BUJUK=9，Grobal2.pas:1349）。

**顺带对齐**（火炬为准，用户 2026-11-17 定的蓝本优先级）：`do_damage_weapon`
节流 `Round(持久/1.03)`（GameOfMir :18975 老版）→ `Round(持久/1000)`
（火炬 :5194/5224，与衣服一致）。只影响 SM_DURACHANGE 推送频率，数值不变。
火炬武器到 0 也只是 `Dura:=0` 不删装备（Index:=0 段被注释掉），本项目维持
损毁清槽（与 GameOfMir 一致、玩家语义更清楚），未改。

**验证**：`test_durability.py` 重写断言后 19/19（新增：衣服不再二次磨损、
符(9)/护身(12) 豁免、/1000 节流同点不下发/跨点下发）；核心回归
test_pyserver 128/0、test_all_messages 95/0、test_pk_punishment 22/0、
test_amulet_depletion 9/0、test_afk_maintain 17/0。

纯服务端逻辑，无资源/gamedata；部署只需镜像。

## §150 原版名字风格补充：NPC 常显名不显血条、怪物永远显血条（2026-11-23，用户反馈）

`MapActor._drawHeadHud` original 分支：`showHp` 改「怪物恒真 + NPC 恒假 + 其余按
isFocus/isSelf」；`showName` 加 `e.type === 'npc'` 常显。当前改良风格分支不变。
注意：怪物血条常显是用户新增（原版仅 FocusCret/BoOpenHealth 画条），不是还原。
test_name_style.mjs 加 3 断言（非选中怪仍有血条 / NPC 有名无条），15/15。

## §151 OPFS 整文件资源缓存 + 载入失败重试（2026-11-23，用户反馈「载入慢/重复载入」）

**根因**：IndexedDB 持久层上限 384MB < resources/Data 全量 1.2GB——一个版本的 WIL
分片放不满触发淘汰，下次进游戏又走网络（重复载入）；且网络失败原先直接 reject，
瞬时抖动（隧道断/重启）会让格子/精灵长期空白。

**新文件**：
- `frontend/gfx/opfsStore.mjs`：OPFS（`navigator.storage.getDirectory()`）整文件缓存。
  版本目录隔离（asset-version 变→旧目录整体删）；文件名 = mangleKey(url)；启动恢复
  索引、请求持久存储；配额满淘汰一半。坑：`FileSystemFileHandle` **没有 getSize()**，
  必须 `getFile().size`（首版在这里抛 TypeError 导致整层静默降级，重载实测才暴露）。
- `frontend/gfx/fileCache.mjs`：`loadWholeBytes(kind,url)` 整文件加载链
  OPFS→IndexedDB→网络（重试 2 次指数退避）。Maps.mjs/.map、wil.mjs/WIX、boot.mjs
  warmMap 全部改走它（顺带修 wil.loadIndex 失败后 rejected promise 永久留在
  `_indexLoading` 的问题——加 catch 复位）。

**scheduler.mjs**：命中链改 mem→**OPFS**→IndexedDB→网络；网络失败自动重试 2 次
（abortPending 掐断不重试，保持条纹坑语义）；分片下载完成后 `opfsPrefetch(url)`
串行队列 + 每次间隔 2.5s 后台整文件回填（不抢首屏带宽）；stats/diag 加 opfsHits、
retries；`window.__opfs()` 调试钩子。main.mjs init 并行开两级缓存（OPFS 限 1s 超时）。

**验证** `scripts/test_opfs_cache.py`（3.9 Playwright + 3.10:8098）9/9：首开启用、
预取回填、**重载后索引持久 + 整文件命中零网络**、route 注入 502×2 后成功（retries=2）、
持续 502 reject 不卡死、无页面报错。

纯前端；部署只需镜像。后续可选：进游戏过场时批量预取本地图用到的 WIL 整文件。

## §152 大地图寻路后台续跑：页面隐藏不再停步（2026-11-23，用户反馈）

**根因**：寻路步进 `_tickAutoNav` 挂在 rAF 主循环（render 内）；Chrome 在标签页
隐藏/窗口最小化时**冻结 rAF**，人物原地停走。托管不受影响（服务端 tick 推进）。
WS 也不受影响（服务端 ping + 浏览器原生 pong，后台不断线）。

**修复**（PlayScene.mjs）：
- `_navBgEnsure/_navBgStop`：导航期间页面转后台 → `setInterval(250ms)` 专用定时器
  继续调 `_tickAutoNav(Date.now())`（本身按时间戳节流，与 rAF 无耦合）；
  回前台/取消导航/到达/场景 onExit 即停表。
- `visibilitychange` 监听在 onEnter 注册、onExit 注销。
- 后台时用 `navigator.locks.request('jootm2.nav-background', {signal})` 持锁申请
  豁免 Chrome「5 分钟后激进节流」（无豁免定时器退化为每分钟一次）；Web Lock
  在部分 Chrome 版本还能让持锁页面**完全不冻结定时器**。失败仅退回旧行为。
- 坑：`FileSystemFileHandle` 无 getSize 那类静默降级风险已规避——定时器异常只
  console.error，导航状态由 `_navPath` 空判定自然收敛。

**验证** `scripts/test_nav_background.py` 10/10（3.9 Playwright + 自起 3.10 临时
服务端/独立库）：覆盖 window.requestAnimationFrame 精确模拟后台冻结 + 伪装
document.hidden 派发 visibilitychange；对照组（前台+冻结=停步）、隐藏后定时器
接管且位置持续推进、Web Lock 在 held 列表、回前台停表无双驱动、取消导航后
隐藏无残留定时器、无页面报错。
顺带：`test_opfs_cache.py` 的 BASE 改 `JOOTM2_BASE` 环境变量（默认仍 8098）。

纯前端；部署只需镜像。

## §153 阿里云部署 a0a08a3（2026-11-23 20:37）

- 部署前 `/api/players`：在线 5 = 3 Bot + **2 真人（测试1、燕赤霞）**；经用户确认「直接部署」后执行。
- `bash scripts/deploy_image.sh` 33 秒一把过；无 scp（619d2f5..HEAD 零 gamedata/资源改动）。
- 带上 4 笔功能：`f182959` 衣服耐久对齐、`c1a38f5` NPC 常显名/怪物常显血条、
  `e2b5335` OPFS 整文件缓存+载入重试、`a0a08a3` 寻路后台续跑。
- 验证：`/api/version=a0a08a3`、smoke_ws 全通、容器内 opfsStore/fileCache 在位。
- 注意：真人玩家会被重启踢下线，重连即可（token 免密会自动登回）。

## §154 说话气泡移回头顶（2026-11-23，用户反馈）

原版风格名字在躯干（-17），气泡原实现贴名字上方（-33）视觉上压在人物身上。
改为独立锚定头顶：`baseY = min(nameY, anchorY-71) - 16`（原版/改良两风格统一
在头顶-87 起向上叠行）。test_name_style 15/15 + 真实浏览器注入 _sayText 截图
确认气泡在血条上方。纯前端；部署只需镜像。

## §155 精神力战法不涨修炼值：PSwordSkill 共享指针对齐（2026-11-23，用户反馈）

**根因**（火炬 `ObjBase.pas:4243-4297`）：原版扫描 MagicList 时，战士基本剑术(3)
与道士精神力战法(4) **共用同一个 PSwordSkill 指针**（case 4 注释「道士的基本剑术」），
命中怪物的加点（:5463-5471）加的是该指针 → 道士平A应涨精神力战法。旧实现
`_train_sword_skills` 只写死技能 3，道士永远不涨。

**修复**（handlers_combat.py）：`for mid in (4, 3): if _can_train: train; break`
（两者都学时 4 优先，与原版 case 顺序一致；现实中职业互斥不会都学）。

**全技能核查**（对照 Magic.pas SpellNow 全部 `train := TRUE` 行）：
1/5/11/13 命中动物怪、2/29 治疗生效、6 中毒、8 推开>0、9/10 穿透命中、
14/15 加持>0、16 困住、17/30 召唤成功、18/19 隐身生效、20 诱惑、21 瞬移、
22 火墙放置、23/33/24 范围命中、28 启示成功、31 开盾、32 圣言秒杀——
`_resolve_magic` 返回值一一对应，`_finish_cast` 统一加点，无遗漏。
两处已知差异（不改，记录）：①原版伤害法术只在 `RaceServer>=RC_ANIMAL` 时涨，
我们对守卫类怪（race<50）命中也涨——边缘；②原版施毒可中玩家（:790
`RC_USERHUMAN or >=RC_ANIMAL` 都 train），本服施毒拒绝玩家目标，属玩法差距。

**验证**：新增 `scripts/test_skill_train.py` 11/11（道士只学4涨4 / 战士涨3 /
3+4都学只涨4 / 满级与等级门槛不涨 / 攒满100升级扣点 / 攻杀不受影响）；
test_durability 19/19、test_pyserver 128/0。纯服务端；部署只需镜像。

## §156 死亡装备掉落阈值对齐火炬：红名掉率不再翻倍（2026-11-23，用户反馈「很容易掉」）

**根因**（火炬 `ObjBase.pas:2547` vs `:2398`）：原版 `PKLevel = pk_point div 100`
（:3865），两个红名阈值不同——
- 背包全掉：`PKLevel >= 2`（:2398）= pk_point >= **200**
- 装备加速掉（每槽 Random 30→15）：`PKLevel > 2`（:2547）= pk_point >= **300**（深红名）

旧实现把两者合并成一个 `red = pk_point >= 200`，导致 **200~299 红名段装备掉落
概率被错误翻倍**（1/30→1/15）。白名（<200）本就 1/30，与原版一致。

**修复**：util.py 新增 `DEEP_RED_PK_POINT = 300`；`_death_drops` 背包全掉仍用
`red`（>=200），装备 rate 改用 `deep_red`（>=300）。顺带更正 `_death_drops` 与
test_death_drop.py 头部注释——旧注释里「M2Share.pas:2019-2026 默认配置」那套
变量名（boDieScatterBag/nDieRedDropUseItemRate 等）在火炬源码根本不存在，是臆造
引用，已按真实实现（Die 掉落段 :2728-2760 / ScatterBagItems :2385 /
DropUseItems :2512）逐行更正。

**已知未实现差异**（记录，非 bug）：①原版安全区 FightZone/Fight3Zone 整体豁免
掉落（:2729），本项目地图无该标志；②原版 IDC_NEVERLOSE/IDC_DIEANDBREAK 物品标志
（永不掉落/死亡破损）未解析——普通装备这两个标志均为 0，不影响掉率。

**验证**：test_death_drop.py 新增 [3b] 分档断言（白名30/红名200仍30/深红名300→15），
29/29；test_pet_drop_pickup 8/8、test_pk_punishment 22/0。纯服务端；部署只需镜像。

## §157 阿里云部署 517cefa（2026-11-23 21:41）

用户确认立即部署（当时在线含 2 真人：测试1、燕赤霞，已接受踢线）。
`bash scripts/deploy_image.sh` 一次完成：镜像 `mir2-python:517cefa` 推送、
容器重建启动。本次含 §154~156 三笔改动（气泡头顶 / 精神力战法修炼 /
死亡掉落阈值），前端随镜像走、无 gamedata 变更故无需 scp。
验证：`/api/version` = 517cefa，`/api/status` 200，重启后 5 人（bot+重连）在线。
