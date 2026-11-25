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

## §158 整文件缓存线上静默失效：OPFS 需安全上下文，补 IndexedDB Blob 降级（2026-11-23，用户反馈「换图/弱网后还是重复下载、地图全黑」）

**实验实锤**（`scripts/debug_map_switch_cache.py`，公网实例 + Playwright 按窗口计数）：
新 context 登录→比奇→GM 传送 D401 矿区→回比奇，全程 `__opfs().enabled=false`、
`stored=0`；进矿区一段 **221 个网络 Range 请求**、IDB 分片仅命中 5 次——整文件缓存层
（§151）在线上从来没生效过。回比奇 50 请求是「已访问地砖」，同样大部分走网络。

**根因**：**OPFS（`navigator.storage.getDirectory`）是「安全上下文」专属 API**——只在
https 或 http://127.0.0.1 / localhost 下存在。线上是 `http://域名:8080`，属性直接
undefined，首版 opfsStore 只有 OPFS 一个后端 → 整层静默降级回 384MB 精确区间分片缓存。
§151 回归跑在 127.0.0.1（唯一免安全位的地址）所以全绿没暴露。弱网症状链：进矿区要现下
几百个分片（Hum/Hair/Weapon/Mon 各方向帧一批批失败）→ 精灵缺面；退图时新请求堵死
MAX_CONCURRENT=8 队列 + 地面缓存借像素失败 → 外面也黑。

**修复**（`frontend/gfx/opfsStore.mjs` 重写，对外 API 不变）：双后端——OPFS 可用走 OPFS
（`mode:'opfs'`）；否则**整文件 Blob 存 IndexedDB**（`mode:'idb'`，库名
`jootm2-whole-<素材版本>`，天然按版本隔离，其他版本库异步删除）。Blob 与 File 同样有
`.size/.slice()/.arrayBuffer()`，scheduler / loadWholeBytes 零改动。索引跨重载用 size
恢复、Blob 惰性读（init 不把所有 Blob 拉进内存）；配额写失败 → 按插入序淘汰后重试一次。
`opfsGetStats()` 新增 `mode/version` 字段。

**验证**：新增 `scripts/test_whole_cache_modes.py` 双上下文回归（A=127.0.0.1 期望 opfs，
B=局域网 IP 非安全上下文期望 idb；各 6 项：后端正确/Range 无异常/回填/重载索引恢复/
二次零网络命中/无 JS 报错）12/12；`test_opfs_cache.py` 9/9 无回归。
待部署后用 debug 脚本对线上复跑，`enabled=true, mode=idb` 应成立。

## §159 Cloudflare Tunnel 上线 https://mir2.notmmao.com（2026-11-23，橙云方案实测作废）

**为什么不能用橙云代理**：阿里云备案拦截是**全端口 DPI 查 HTTP Host 头**（实测 8080
带任意未备案域名 Host → Non-compliance ICP Filing 拦截页；带 IP Host 才放行），而
CF 橙云回源的 Host 头固定等于访客访问的主机名、产品上无法改写——「未备案 + 大陆源站
+ 橙云」无解。Tunnel 可行是因为最后一跳是 cloudflared → `127.0.0.1:8080` 本地回环，
不走公网入口、绕开 DPI。

**部署**：服务器上 `docker run -d --name jootm2-tunnel --restart unless-stopped
--network host cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <用户
在 CF Zero Trust 面板建的 token>`。4 条边缘连接注册成功（region2.v2 被墙自动避开）。
公网入口收敛为 https://mir2.notmmao.com/。

**验证**（debug_map_switch_cache.py 对隧道复跑，HEAD=dca8aa0）：
- Range 206 + Content-Range + gzip 头完整透传；WS 握手 101；
- **https = 安全上下文 → OPFS 整文件缓存首次真正生效**（`mode:'opfs'`）：
  首进矿区 221→19 请求；回比奇 31 请求且 opfsHits=40（Range 零网络本地切片）；
  后台预取持续回填（stored 20、pending 10）。
- 遗留观察项：CF 免费版 WS 空闲超时约 100s，长时间挂机若实测掉线需在服务端加 ping/pong
  或调 CF 配置；debug 脚本角色名改为 `JOOTM2_CHAR` 环境变量传入（线上= GmHero）。

---

## §160 离线修炼详细日志 + 拒绝原因文案修复（2026-11-24，用户要求）

**需求两条**：①「给离线挂机加入详细日志，单独存储，以便分析，为以后优化功能找到依据。内容、格式、存储路径你来定，可以用日期+角色名为文件名」；②「我发现拒绝原因没写好，你检查下」。

### A. 运行日志（新增 `pyserver/afk_log.py`）

- **格式**：JSONL，一行一事件，`kind` 区分类型，便于 grep / jq / 脚本聚合。每行同时记墙钟 `t`（`YYYY-MM-DD HH:MM:SS`）与游戏时间 `ms` —— 分析用 `ms`（模拟器会把墙钟压缩成几秒，见下文坑），排查宕机用 `t`。
- **路径**：`<afk.logDir>/<YYYY-MM-DD>_<角色名>.jsonl`，**追加**模式（同日同角色多轮修炼共用一份，靠 `session_start` 分段）；文件名清掉 `<>:"/\|?*` 等非法字符、截断 64。默认 `data/afk-logs`。
- **性能/鲁棒性**：内存缓冲 + 批量 flush；`session_start`/`session_end`/`death`/`error`/`reject` 立即落盘；IO 异常一律吞掉并置 `enabled=False`（**日志故障绝不能把挂机搞停**）。纯标准库。
- **事件类型**：

  | kind | 触发点 | 关键字段 |
  |---|---|---|
  | `session_start` | `promote()` 接管成功 / `resume_from_db()` 续挂 | preset、plan、level/exp/gold/hp/map/bag、resumed、bonus |
  | `sample` | 每分钟（`afk.logSampleMinutes`，0=关） | level、exp、**dExp**、kills、**dKills**、gold、**dGold**、hp/hpPct/mp、map/x/y、bag/bagFree、supply、supplyFails、remaining、deaths、status |
  | `death` | `handle_death` | deaths/limit、hp、supply、remaining |
  | `supply_begin` / `supply_end` / `supply_fail` | 补给状态机 | why、seq、map、anchor、gold、bag、potions、fails |
  | `session_end` | `_settle` | reason、planned、consumed、remaining、expGained、kills、goldDelta、deaths、supplies、errors、restartBonus |
  | `reject` | `request_start()` / `promote()` 被拒 | stage、reason、map、level、plan |
  | `error` | `tick()` 捕获异常 | where、type、msg、status、supply、remaining |

  `dExp`/`dKills`/`dGold` 是**区间增量**，聚合即得每分钟收益曲线。逐只击杀不记（体积会翻十倍，分钟级足够定位问题）。
- **配置**（`config.py` 三处齐改，`config/server.properties` 可覆盖）：`afk.logEnabled`=1、`afk.logDir`=data/afk-logs、`afk.logSampleMinutes`=1。
- **接入点**：`OfflineAgentManager._open_log/_log_event/_log_close/_log_reject/_log_sample`；`OfflinePilot` 加 `log`、`log_at`、`log_exp/kills/gold`、`log_supplies`、`log_errors` 槽位。`discard()`/`disable()`→`_settle()` 两条收尾路径都关文件。

### B. 分析器（新增 `scripts/afk_log_report.py`）

按会话输出：起止/实耗/计划、收益与**每分钟均值**、地图分布、**补给占时%**、低血量采样比、背包空位、分钟经验曲线、零经验分钟占比、补给成败与触发/失败原因分布、死亡时状态；`--timeline` 打完整事件序列；末尾按角色汇总。参数 `--dir/--day/--name/--timeline/--limit`。

```bash
D:/Python310/python.exe scripts/afk_log_report.py --day 2026-11-24
D:/Python310/python.exe scripts/afk_log_report.py --name 角色名 --timeline
```

### C. ⚠️ 日志上线即抓出一个真实老 bug：满包绕过补给冷却

- **现象**（`sim_afk_longrun.py --hours 1.5 --log` 首次产出）：90 分钟里 **2885 轮补给**、平均每 0.9 秒起一轮、单轮仅 800ms、73% 采样处于补给流程、63% 分钟零经验；背包恒为 46/46、药水恒为 28/27（卖无可卖、药已买满）。
- **根因**：`OfflinePilot._on_bag_full` 直接 `_supply_begin()`，**没检查 `supply_retry_until`**。而 `_survive` 每 tick 都会在满包时调它；本地商人在身边时一轮空转补给只需 800ms（GOTO_TOWN 已在本地 → SELL 空转 → BUY 已满 → GOTO_ANCHOR 1 格 → FINISH）→ 冷却还没设上就重起一轮。
- **修法**：`_on_bag_full` 里加冷却判断，冷却期内只 `set_status("背包已满 · 补给冷却中，继续打怪不拾取")`。`_supply_check_trigger` 早就有同样的冷却保护，只有这条 tick 级路径漏了。
- **修复效果**（同参数同种子重跑）：补给 2885→19 轮、击杀 87→213（+145%）、经验 9523→22914（+141%）、零经验分钟 63%→12%、补给占时 73%→0%。**这正是用户「加日志为以后优化找依据」的第一个产出。**
- 回归固化：`scripts/test_afk_log.py` §5「冷却期内不重起补给 / 冷却结束后可重起」。

### D. 拒绝原因文案修复（用户「拒绝原因没写好」）

实库审计 `afk_reports` 定位三类病根：
1. `promote()` 五类门禁全是结论式短句（「等级低于离线修炼门槛」「全服离线修炼名额已满」），玩家不知道差多少、是哪个图/哪个名额；
2. 「可用修炼时长不足 10 分钟」把**余额 / 每日额度 / 计划值**三个上限混成一条，玩家分不清该去领取还是等明天；
3. `takeover()` 正常上线收工写成「玩家上线，离线修炼结束」，读起来像被拒绝，也让 minutes>0 的正常记录被误判成异常。

改法：
- `request_start()` 拆出 `_validate_start()`（六个门禁集中一处），每条都带数据：`当前 X 级/需要 Y 级`、`仅剩 N 分钟（至少 10 分钟）`、`已用 X/Y 分钟`、`余额 A 分钟、今日剩 B 分钟`；计划被余额/额度压到 <10 分钟时单独给「余额/今日剩」两个数。
- `promote()` 五类门禁同样带数据：`地图（0）禁止`、`当前 10 级/要求 15 级`、`名额 10/10`、`本图 2 个上限`；时长不足细分为**余额不足 / 今日额度用完 / 计划值不足**三条独立文案。拒绝报告加 `notice="未开始的计划不扣除时长，可重新设置"`。
- `takeover()` → `reason="角色上线，本次修炼正常结束"`。
- 拒绝同时落 `reject` 日志（`_log_reject`，写一次即关，异常吞掉），日后可按 reason 聚合出「哪道门槛最常挡住玩家」，作为调 `afk.minLevel` / 排除图 / 每日额度的依据。

### E. 测试隔离（重要，别再踩）

`test_afk_offline.py` / `test_afk_supply.py` 的 `make_service()` 默认把 `afk_log_dir` 指到临时目录，**避免测试往仓库 `data/afk-logs` 堆文件**；需要验证日志的用例显式覆盖。`sim_afk_longrun.py` 默认 `afk_log_enabled=0`，加 `--log` 才写（角色名 `SimAfk`，与真实玩家日志可区分）。

### F. 验证

- 新增回归 `scripts/test_afk_log.py` **47/0**（单元行为 / 拒绝文案带数据 / 全生命周期事件序列 / 采样字段齐备 / takeover 文案 / 满包冷却）。
- 既有：`test_afk_offline` 50/0、`test_afk_supply` 43/0、`test_afk_maintain` 17/0、`test_afk_dashboard` 19/0。
- `py_compile` 全过；模拟器 1.5h 长跑 + 分析器实跑通过。

### G. 部署提醒

改的是服务端代码 + 新增配置项，**要更新镜像并重启**；`config/server.properties` 不必动（有内置默认值，想改 `afk.logEnabled`/`afk.logDir` 才需要，那种情况走 gamedata 卷 scp + restart）。日志目录 `data/afk-logs` 在容器 `/app/data` 下（compose 已挂 `./data`），随容器持久化，注意长期堆积（每角色每轮约 1 行/分钟，`--timeline` 分析后可定期归档清理）。

**本轮改 `pyserver/afk.py`、`pyserver/config.py`、新增 `pyserver/afk_log.py` / `scripts/test_afk_log.py` / `scripts/afk_log_report.py`、改 `scripts/sim_afk_longrun.py` / `test_afk_offline.py` / `test_afk_supply.py`、本文档。标签：服务端日志与 bug 修复 `[逻辑]`，文档 `[文档]`。**

---

## §161 离线修炼地图适配对比：骷髅洞 vs 僵尸洞（2026-11-24，用户要求）

**需求**：「多做几个模拟验证，例如 15-20 级挂骷髅洞或僵尸洞，并记录装备磨损情况、收益、怪物密度等。」

**新增工具 `scripts/sim_afk_matrix.py`**：7 组场景 × 地图 × 等级横比，单次跑完输出
收益/磨损/密度/风险四类指标。关键设计：

- **固定 `seed=42`**：不固定的话同参数两次结果差异全来自随机（第一次 F 组死了 1 次
  掉了项链、第二次没死，无法复现也就无法用于决策）。
- **每组独立临时 Database + 独立 Clock**：同进程顺序跑，跑完关闭，互不污染。
- **`afk_supply_repair=0`**：关掉补给时的自动修理，否则磨损数据被修理抹平。
- **`afk_log_enabled=0`**：矩阵不写 JSONL，避免与 §160 的日志混在一起。
- 输出：单组明细（含逐槽位持久起→止、折损%、预计耗尽时间）+ 汇总表 + 自动归纳
  的「最高经验/最高金币/最安全/最费武器」+ 提前停机清单。`--json` 可落机读结果。

**结果文档 `docs/AFK_MAP_COMPARISON.md`**（表格 + 机制解释 + 调优建议）。

### 核心结论（7 组 × 60 分钟实测）

| 场景 | 初始怪 | 密度/千格 | 经验/分 | 击杀/分 | 死亡 | 最低HP | 武器磨损/100分 | 衣服磨损/100分 |
|---|---|---|---|---|---|---|---|---|
| 15 级 骷髅洞 D011 | 545 | 3.4 | 1040 | 6.53 | 0 | 96% | 2628 | 0 |
| 18 级 骷髅洞 D011 | 545 | 3.4 | **1226** | 7.90 | 0 | 100% | 3100 | 0 |
| 20 级 骷髅洞 D011 | 545 | 3.4 | 1177 | 7.48 | 0 | 99% | 2978 | 22 |
| 15 级 僵尸洞 D401 | 197 | **4.9** | 2703* | 9.00 | 0 | 90% | 3788 | 44 |
| 18 级 僵尸洞 D401 | 197 | 4.9 | 391 | 6.12 | **1** | **3%** | 2520 | **435** |
| 20 级 僵尸洞 D402 | 197 | 4.9 | 1284 | 6.80 | **1** | **1%** | 2778 | **345** |
| 22 级 盟重省 3（对照） | 228 | **0.3** | 361 | 3.35 | 0 | 51% | 1967 | 128 |

（D 组 2703/分 只跑了 25 分钟就空转停机，样本短、参考价值打折。）

**结论**：
1. **15–20 级首选骷髅洞 D011/D012**：三组零死亡、最低 HP 96–100%，衣服几乎不掉。
2. **僵尸洞收益高但极不稳定**：单怪经验 336 vs 骷髅 179，但 18/20 级组各死 1 次、
   HP 掉到 1–3%，衣服磨损是骷髅洞的 10–20 倍。
3. **当前默认图（盟重省 3）效率最低**：361 经验/分仅骷髅洞 1/3，根因是面积
   800000 格而怪只 228 只，密度 0.3/千格（D401 的 1/16）。
4. **武器是唯一真正的消耗品**：每次命中扣 2–6 持久，2000–3800/100 分钟；
   4000 持久的武器约 70–150 分钟磨完 → 长挂必须备 2–3 把。
5. **衣服只在挨打时磨损**（`ObjBase.pas:3487` 挨打才扣 5~14），所以「站着不挨打」
   的骷髅洞衣服磨损≈0，僵尸洞（AC 2、伤害高）则飙升。

### 顺带查清的两个机制（**不是 bug**）

- **复活僵尸经验减半**（`ObjMon.pas:1305` / `world.py:2260`，race 96）：僵尸被反复
  打死复活，每次 `m_dwFightExp div 2`。E 组 367 击杀只拿到 23487 经验（64/只）
  正是因为那片区域是「残值僵尸」。→ 潜在优化：离线选怪跳过低残值僵尸（需用户拍板）。
- **死亡复活回出生点而非原地**（`afk.py::handle_death` 按 `UsrEngn.pas:576` 回
  `home_map`）：僵尸洞死一次的代价是「从比奇跑回矿区 3 张图」，这是「僵尸洞适合
  冲级不适合长挂」的核心原因。

### 途中修的两个统计口径错误（脚本自身，非产品 bug）

- **死亡掉落 ≠ 磨损耗尽**：第一版把所有空槽位记为「损毁」，F 组（死 1 次）项链/戒指
  被标成「100% 磨损」，实际是 `_death_drops` 掉了。现分 `dura_broken`（磨到 0）与
  `dura_lost`（死亡掉落）两栏，显示时明确标注。
- **最低 HP 采样漏采**：原本只在分钟整点采样，D 组「最低 HP 100%」却因血量过低停机，
  自相矛盾；改为**每 tick 采样**。

### 验证
`py_compile` 通过；7 组 × 60 分钟全跑通（总耗时 548s）；两次重跑（改统计口径前后）
已核对差异来源（种子 + 掉落归因）。

**本轮新增 `scripts/sim_afk_matrix.py` / `docs/AFK_MAP_COMPARISON.md`，未改任何产品代码。标签：`[测试]`。**

## §162 身上装备修理闭环 + 补给链路四项修复（2026-11-24，用户选定「只修身上那件 + 维持 50% 阈值」）

**需求**：「当前离线有自动卖物品和修装备的功能么？」——两功能 2026-11-23 已有，但实测
发现身上装备**永不修理**（原版 `ClientRepairItem` 只认背包 `m_ItemList`，`ObjBase.pas:24653`，
必须先脱下→修→穿回）。用户经选项确认只做这一项。

### 实现（`pyserver/afk.py`）

- 新常量：`SUPPLY_EQUIP_SLOTS=(U_WEAPON,U_DRESS)`、`SUPPLY_EQUIP_TRIGGER_PCT=25`
  （身上件低于 25% 才触发补给，比背包 50% 严——脱穿有裸装风险）。
- 新字段：`maint_slot/maint_mi/maint_stage`（""→off→on 三步状态机）、
  `gear_no_npc_maps`（本图无修理商缓存）、`gear_retry_until`（触发冷却）。
- 新方法：`_supply_maintain_equipped`（脱下→找商→修→穿回，**每步都做 ≤12 格距离
  校验**——原版 `_npc_and_player` 要求 ≤15，早先没校验导致修理请求被静默丢弃、
  「以为修了其实没修」）、`_maint_reclaim`（补给中断/失败/停机时兜底穿回，防止
  裸装打怪）、`_gear_needs_supply`、`_equipment_needs_repair`。
- `_supply_check_trigger` 增加「装备耐久过低」触发路径（带 `gear_retry_until` 冷却，
  防止退化回 §160 抓到的 2885 轮空转）。
- 覆写 `_ensure_equipment`：维护中（装备在背包待修）时**跳过自动补装**——否则
  刚脱下就被父类按「槽位空」穿回去，修理永远轮不到（实测活锁根因）。注意 3.10
  slots dataclass 不能用零参 `super()`，显式 `AutoPilot._ensure_equipment(self,...)`。

### 连带修掉的四个补给链路 bug（每个都有实测依据）

1. **商人距离预算错用常数**：`SUPPLY_SELL_MAX_DIST=40` 本是「挂机图就近卖杂物」
   的预算，被 `_supply_sell_npc`/`_supply_repair_npc` 复用到**跨图进城**场景——比奇
   省落点 (42,109) 距商人区 (300,250) 260 格，全部判「没人收」进黑名单，背包恒满
   → 腾不出格子 → 脱不了装 → 修理永不发生。改为 `_supply_npc_max_dist(p)`：本图
   补给 40 格、进城补给放开到全图。
2. **`_repair_allowed` 兜底误选**：脚本无 `[@repair]` 头时「是商人就放行」，机器人
   选中彩票员/仓库保管员。新增 `_npc_really_repairs`（严格段判定）两段式择优。
3. **修理商要按「真能修这件」选**：原版修理价过 `GetItemPrice` 过滤（`ObjNpc.pas:2392`），
   服装店（高家店老板 item_types=[10,11,15]）修不了武器、铁匠不修首饰 → 价 -1 →
   原版回「这个物品无法修理」。`_supply_repair_npc` 增加 `item` 参数按修理价≥0 过滤；
   候选侧同步（删掉旧 `_supply_repair_candidate` 里不看商人的筛法）。
4. **`_can_supply_locally` 误杀就地补给**：背包只剩一根卖不掉的蜡烛（stdMode 30、
   14/46 格）时判「不能本地补给」→ 跨图 11 段门撞不可达门 D5064 → 反复失败致死
   （D5063 组零金币的根因）。改为按真实需求分两类：有空位→有药商即可；满包→
   必须有商人肯收某件非药品。另给跨图门链加**段数上限**（>6 段判不值得，转长
   冷却继续打怪，不再每 10 秒重算门序列）。

### 验证

- `scripts/test_afk_maintain.py` 扩到 **46/46**（新增：就地补给判据两例、触发线、
  满包腾格、闭环脱下穿回）；`test_afk_supply` 43/43、`test_afk_offline` 50/50、
  `test_afk_log` 47/47 全过。
- 2 小时真流程追踪（脱下/修理/穿回逐调用打点）：修理 1 次调用 1 次成功、无
  「脱了不穿」活锁；`scripts/_tmp_*.py` 共 13 个为一次性探针，**不入库**（提交时排除）。
- `disable()` 兜底原写 `w2.players.get(...)`——`GameWorld.players` 是方法不是字典，
  改 `w2.actor(p.id)`。

## §163 四阶段挂机矩阵（20-25/25-30/30-35/35-40）与最佳挂机点（2026-11-24，用户要求）

**需求**：「继续测试 20-25、25-30、30-35、35-40 四个阶段挂机，分析日志，修正遇到的
问题，找出最佳挂机点；如果发现当前地图怪物数值、刷新等有问题，指出来。」

**新工具 `scripts/sim_afk_stages.py`**：阶段×地图矩阵，复用 §161 基建，另加刷怪密度、
补给占时、`stop_reason` 列。修 `sim_afk_matrix.py::pick_hunting_spot` 两处选点缺陷
（重心在墙上卡死→退怪物实际位置；怪脚下全 occupied→允许落怪旁，D405 曾因此整组
「零经验」假象）。

### 结论速览（详见 `docs/AFK_STAGE_COMPARISON.md`）

- **20-35 级最佳：E603 连接通道**，4300-4400 经验/分、金币净流入、零死亡，三阶段
  蝉联第一（原版 `CalcGetExp` 等级惩罚 ≥怪+10 才衰减，怪均 28 级可挂到 37 级）。
- **35-40 级没有合适挂机图**：祖玛三图（D505/D5063/D515）全部不可离线——无商人/
  商人在不可达房间（祖玛阁 12 房间互不连通是原版迷宫设计）/怪太强。表现为
  1 分钟触发「血量过低且没有药水」安全停机（保护生效，不再送死）。要支撑这个
  段位需要补测石墓 D013、沃玛 W001 等图（门链可达性未验证）。
- 刷新密度无异常：复活间隔统一 15s（原版语义），全部图 ≤1 死/小时。

**本轮改动集中在 `pyserver/afk.py`（§162 修复）与 `scripts/`（矩阵/回归），标签：`[逻辑]`。**

## §160 WS 直连源站架构落地：https 走 CF 隧道、wss 走 LE IP 证书（2026-11-24，用户提出「mir2 继续走 tunnel + 申请三级域名证书 + 程序改 WS 直连」）

**实测数据**（浏览器视角，修正过的干净测量）：CF 隧道 wss median=694ms
（min 410，物理绕路洛杉矶）；直连源站 wss median=11ms（min 10 max 40，
TLS 复用连接每帧零成本）。用户游戏内高延迟反馈与隧道数字吻合。

**三级域名方案实测否决**：`ws.mir2.notmmao.com` A 记录灰云指源站 + LE
DNS-01 证书 + nginx 443 反代 /ws 全部就绪后，外网 SNI=ws.mir2.notmmao.com
仍 **3/3 RST**——阿里云备案名单按**整个 notmmao.com 域名树**（任意子域）
在 TLS ClientHello 的 SNI 和 HTTP Host 两处匹配拦截；对照组陌生域名 SNI
到同 IP 同端口 101 正常。结论：**备案前任何 notmmao.com 子域都不能直连
阿里云 IP**，只能走 CF（代理或隧道）。（11-23 §159 的「SNI 不拦」是当时
mir2 解析指向 CF 边缘、不在名单扫描范围所致，A 记录一改即翻车。）

**最终出路 = IP 字面量直连（无 SNI）**：浏览器对 IP 主机名按 RFC 6066
不发 SNI → DPI 看不见任何域名 → 放行（实测无 SNI 200 OK）。证书校验用
**Let's Encrypt IP 地址证书**：`acme.sh --issue -d 139.224.24.88 --alpn
--cert-profile shortlived`（**TLS-ALPN-01** 是唯一活路：HTTP-01 的境外
验证器连大陆 80 超时 2/2 失败；DNS-01 不能签 IP）。签出 SAN=IP 受信证书，
6 天效期。

**部署形态**（阿里云侧）：
- `jootm2-nginx`（nginx:alpine, host 网络, 443）：唯一 server 出 IP 证书，
  仅 `location /ws` 反代 127.0.0.1:8080（Upgrade 透传、read_timeout 3600s、
  buffering off），其余 404。`--restart unless-stopped`。
- `jootm2-tunnel` 保留：mir2.notmmao.com CNAME 已恢复（中途我误删过 A/CNAME，
  已复原），页面+资源继续走隧道。
- 续期：`/root/jootm2-nginx/acme-cron.sh`（crontab `0 1 * * *`）——先
  `docker stop jootm2-nginx` 腾 443 再 `acme.sh --cron`（standalone ALPN
  要占端口，直接跑会「port 443 already used」；install-cert 的 hook 只在
  续期成功后跑，救不了这个）→ 起回 nginx。mir2/ws.mir2 域证书同 cron 统一续
  （DNS-01 无端口冲突）。
- ws.mir2.notmmao.com 的 A 记录已删（留之无用且触发名单）。

**前端改动**（`7384e60`）：新 `frontend/net/wsUrl.mjs`——hostname 为
`*.notmmao.com` 时 WS 用 `wss://139.224.24.88/ws` 直连；本地/局域网开发
保持同源 `/ws`；`?ws=<url>` 强制覆盖（`?ws=same` 回同源，排障用）。
`Connection` 默认参改走 `resolveWsUrl()`，main.mjs 两处 `new Connection('/ws')`
去掉硬编码。`scripts/test_ws_url.mjs` 11/11。
**线上验证**：https://mir2.notmmao.com 页面 → 实际连 `wss://139.224.24.88/ws`、
login/listCharacters 业务握手 OK、RTT median=11ms；OPFS mode='opfs' 不受影响；
默认同源路径回归正常。

**遗留观察**：①IP 证书 6 天一续，cron 失败无告警——下次巡检要确认
`/root/jootm2-nginx/acme-cron.log` 有成功续期记录；②玩家如果从 http IP:8080
旧地址进（非 https），WS 同源走明文，仍可用但无缓存优势；③DPI 政策是流动的，
若将来连 IP 无 SNI 也拦（技术上可做端口+JA3 统计），退路是整体收口回 CF 隧道。

## §161 无获取途径的物品/怪物清理（2026-11-24，用户要求「整理为脚本 + NONE.csv 备份 + 重生成 wiki」）

**脚本** `scripts/clean_unused_data.py`（dry-run 默认 / `--apply` 落盘 / `--restore` 恢复）。
判定「保留」的引用源（任一命中即保留）：
1. 存活怪物的 MonItems 掉落行（**两遍法**：先算存活怪，被清怪独有的掉落物跟着清）
2. Market_Def `[goods]` 商店货
3. Envir 各脚本 `give <名>` 行、MakeItem.txt 行首
4. **代码字符串常量**（ast 提取 pyserver/*.py + seed_template_chars.py，排除
   docstring/注释——防「火炬」「木剑」这类项目名/注释误伤；覆盖挖矿铜矿/铁矿/
   宝石矿、召唤变异骷髅/神兽、AVOID_NAMES 弓箭护卫等硬编码路径）
5. GuardList.txt / Castle/*.txt 提到的卫士/城门体系怪
6. 玩家库存保护：扫 data/jootm2.db characters.items/storage 的 index 集合

**结果**：物品 541→388（清 153，含 NewNecklace1..9 等）、怪物 273→240（清 33，
全是无刷新副本变体如 跳跳蜂1×7、鹿20/30、赤月恶魔1）。Idx 不重编号（库存按
index 引用）。备份 `StdItems-NONE.csv` / `Monster-NONE.csv`（与主表同 header 同列序）。
清单报告 `docs/CLEAN_UNUSED_REPORT.md`。

**已知边角**：MonItems 目录有 GBK 乱码遗留文件名（`¹.txt`=鹿、`˫ͷѪħ.txt`=双头
血魔）——这些掉落表本来就被游戏按正名查找不到（服务端从不加载成功），其独有的
鹿血/鹿茸 判定「仅被不刷新的怪掉落」一并清出，无行为变化。
`test_pyserver.py` 行数基线 500/250 → 380/230（清理后新基线，注释说明）。
test_all_messages 的 3 个 FAIL 经 stash 对照为**既有问题**（怪物选取+覆盖清单），与本清理无关。
服务端导入回归正常（content policy 后 items=300 monsters=199）；afk_supply 43/43、
afk_maintain 46/46、durability 19/19、death_drop 29/29 全绿。wiki 已按清理后数据重生成。

## §162 /site 跳转排除本地回环 Host（2026-11-24，用户反馈「127.0.0.1:8098/site 也会跳转」）

`net.py` 新增 `_is_local_host(req)`：Host 剥端口后为 `localhost` / `127.*` / `::1`
（或无 Host 头）时，`/site` 的 302→list2 不生效，直接走本地静态服务——本地检查
未发布的 wiki 内容才看得到。公网 Host（含伪造头）照旧跳转。
回归 `test_pyserver.py` 补 3 组用例（本地三种 Host 不跳、无 Host 不跳、公网仍跳），
132/132 全绿。本地 8098 服务已重启生效。commit `3784574`。

## §164 怪物死亡动画泛化 + 精英爆率 + 回血飘字 + 盟重省数据修正（2026-11-24，用户批量反馈）

1. **所有怪物播死亡动画**（`baf5305` [保真]）：此前只有稻草人（raceImg 18）特判
   播 ActDie。火炬证实原版**所有怪当场死都播**：`TCreature.Die`
   （ObjBase.pas:2800）无条件 `SendRefMsg(RM_DEATH,...,1)` → lparam3=1 →
   SM_NOWDEATH（:12582-12584）→ Actor.pas:1044-1050 不折叠 startframe 完整播
   ActDie。SM_DEATH 定格（:1036-1043）只用于「新进视野看已死 actor」（:1865
   lparam3=0）。稻草人特殊在额外叠火焰（AxeMon.pas:351-358），保留。PlayScene
   death 分支改按段表 `die.frame>1 && ftime>0` 判定，播完定格；
   `test_monster_death_anim.mjs` 9/9；`test_corpse_fx.py` 断言放宽 die→death
   两段 + 采样窗延到 4800ms（段表最长 MA34 20帧×200ms），8/8。
2. **精英爆率×2**：`elite.dropMult=2.0` 新配置（DEFAULTS/ServerConfig/load/
   server.properties 四处齐）；`drop_loot` 与 `roll_butch_loot` 判定时乘 boost
   （同周末机制，不回写 MonItems）。`test_elite_monster.py` [6] 段 21/21。
3. **回血绿色飘字**：`_regen` 药池/治愈池真实加血量经 `stats.healed` 下发
   （自然回复不带）；宝宝即时加血同款。前端 `case 'stats'` healed>0 绿字。
4. **道士优先治愈**：在线 `_tick_combat`→`_survive` 与离线补给帧同链路早已
   实现（`ee5e1e5`），本轮确认无需改动。
5. **盟重省数据修正**（`5664b23` [数据]）：
   - 9snpc2 宣传员（地图3,297,302）注释掉，Npcs.txt 同坐标放玛法领路人，
     复制 `Npc_Def/玛法领路人-3.txt`。NPC 总数持平 207。
   - 「城里被打」根因：`3 330 330 神鹰 3 3 20` 是我本沉默私货（176 神鹰只在
     h106），恰刷在 StartPoint(3,330,330) 安全区中心——删该行。**猎鹰是原版
     怪（176 map3 160,250/500,125 野外）保留**。
   - 飞火流星非 176 原版怪，删 `3 330 330` 刷新行；足球场 G003/G004 保留。
     map3 刷怪 228→224（重启日志实证）。
6. **不掉血却播挨打音效**：服务端 `noDamage` 标记本就正确（_Attack
   ObjBase.pas:5441 `if dam>0` 才 StruckDamage+RM_STRUCK）；前端 SoundManager
   attack 分支只挡 miss 没挡 noDamage → 受击三连音照播。修复
   `if (msg.miss || msg.noDamage) break`（挥刀音在门前，RM_HIT 无条件）。
   `test_struck_sound.mjs` 6/6。
Windows 大小写坑：目录真名 `Npc_Def`，git add 小写路径静默漏文件——提交后必须
`git show --stat` 核对文件数。

## §165 自然回血保真 / 耐久爆掉日志 / 中毒飘字 / 法师风筝 / 地面物品 TTL（2026-11-25）

1. **战士站着不动不回血**（`d475a06` [保真]）：双根因——①`GameWorld.last_regen_at`
   全服共享，一个 tick 只有一个玩家抢到 2s 窗口（多人在线/离线/bots 互相饿死）；
   ②1 点/2s 与原版差两个数量级。火炬 `TCreature.Run`（ObjBase.pas:7548-7587）：
   每角色独立 `HealthTick/SpellTick`，HEALTHFILLTICK=300（20ms×300=6s）回
   `MaxHP div 75 + 1`，SPELLFILLTICK=800（16s）回 `MaxMP div 18 + 1`；挨打置 0
   （:12559）、走路 -10（=200ms，:8995）、跑步 -60（=1200ms，:9049）。Player 新增
   `health_tick/spell_tick/regen_prev_at`，`wear_struck`/`try_move` 接线。
   `test_tick_systems.py` 段[2] 重写，181/181（既有 MonItems 尾随空格 1 FAIL 无关）。
2. **装备耐久爆掉进挂机日志**（`9f3c9d5` [托管]）：`GameWorld.on_item_break` 回调
   （world 不反依赖 afk），`do_damage_weapon`/`_wear_slot_struck` 归零清槽处触发；
   OfflineAgentManager 注册后写 `item_broken` 事件（slot/slotName/item/duraMax/
   map/deaths/supply）。afk_log_report 新增「耐久爆掉」按件汇总 + 件/分钟速率。
   注意 `SLOT_NAMES` 按 U_* 索引（util.py）。耐久掉太快本身是原版机制
   （StruckDamage 1/8 概率 ×5~14 磨损），等实测数据再谈调平衡。
3. **中毒掉血飘字**（并入 `d475a06`）：`_apply_poison` 每跳真实扣血经
   `stats.poisonDamage` 下发，前端紫色（#b45ce0）飘字；断言入 test_tick_systems [3]。
4. **法师风筝「被锁定即撤」**（`bc1297c` [托管]）：旧 `_kite_worthy` 只认模板强怪
   （maxDc≥12/hp≥200），沃玛/祖玛弱兵贴身站桩对砍。新口径：怪 `target_id ==
   self.player_id` 一律值得撤（挨打即证据）；弱怪没锁我不撤（别对鸡乱跑）。
   在线托管与离线挂机共用 `_tick_combat` 自动生效。`test_mage_kite.py` 5/5。
5. **地面物品「捡不了+前端残留」**（`a7ef47a` [修复]）：三因——①TTL 60s 是拍脑袋，
   原版 ObjBase.pas:1784 掉物 **1 小时**才删（视野扫描里 Dispose）→
   `GROUND_ITEM_TTL_MS=3600_000`；②`update_sight` 只做实体差分，地面物品只在
   map_snapshot 全量下发——走远/被删不发 itemRemove，前端 `groundItems` 越积越脏
   → Player 新增 `seen_ground`，update_sight 补 itemDrop/itemRemove 差分，
   `_broadcast_item_drop/remove` 同步维护各玩家 seen_ground，map_snapshot 播种；
   ③前端 `_touchPick` 支持 1 格外拾取但服务端要求站上格子且静默拒绝（点了没
   反应）→ 脚下没有就朝 1 格内最近掉落走一步。归属 2 分钟（ANTI_MUKJA_DELAY
   =2min，ObjBase.pas:45）原本就对，未动。
回归：afk_offline 50/50、afk_supply 43/43、afk_maintain 46/46、pyserver 132/132、
tick_systems 全绿（除既有 MonItems FAIL）、mage_kite 5/5。

## §166 挂机后台改为「离线修炼专用」页（2026-11-25，用户拍板三点）

用户反馈 afk.html 定位不清：①去掉在线托管（页面只管离线修炼）；②「设定
计划」在后台没有意义（离线没法设、在线本来就在游戏里设），删掉，只留取消/
中止；③主诉求 = 看**当前离线挂机状态**（挂了多久、收益多少），类似重新登录
时收到的那条报告。

改动：
- `frontend/afk.html` 重写：删在线托管卡与设定计划表单；新三段——「本次修炼
  （进行中）」（已修炼时长/剩余/击杀/经验/金币增量/死亡/档位，修炼中每 30s
  自动刷新）、「上次修炼报告」（末条 afk_reports，含结束原因）、「修炼时长」
  （余额/今日已用/今日剩）。顶部 banner 三态：修炼中 / 有待生效计划 / 空闲。
  角色下拉不再要求在线（`afkActive` 标「· 修炼中」）。唯一按钮「中止修炼 /
  取消计划」带 confirm。GM 视图只留离线修炼表（snapshot 新增 goldDelta/
  expGained）。
- `pyserver/afk.py`：新增 `OfflineAgentManager.dash_view(character_id, p=None)`
  （余额/今日量 + running 实时增量 + latest_afk_report，p 可为 None=人已完全
  离场）与 `dash_stop(p)`（运行中→disable 立即结算退余额；仅有计划→清
  afk_plan）。`snapshot()` 补 goldDelta/expGained。
- `pyserver/db.py`：`latest_afk_report()`（只读末条，**不动 delivered**，游
  客看不掉玩家登录报告）。
- `pyserver/service.py`：新只读路由 `/api/dash/afk?token&charId`（不要求在
  线，按账号校验归属防越权）；`/api/dash/auth` 角色项加 `afkActive`（区分
  离线代理与真人在线，离线代理也在 world.players() 里）；`/api/dash/action`
  的 afk 分支只留 stop 且改走 dash_stop（不再要求 `_dash_player` 在线匹配），
  afk start 从后台 REST 移除（游戏内 WS `afk` 消息的设定计划不受影响）。

回归：afk_offline 57/57（新增 §14 视图/中止 7 用例）、dashboard 24/24
（新增 [7] 离线视图 3 用例）、maintain 46、supply 43、log 47 全绿。

## §167 服务器 CPU 热点优化：寻路位图化 / 索敌候选集 / 名索引（2026-11-25，用户反馈线上 CPU 又高）

线上分析（只读，未重启）：py-spy 60s 火焰图显示忙时 CPU 大头是托管
寻路 PathPlanner（A*，约 15%，每 tick 每代理烧 20ms 预算）与
`_resolve_target` 全图扫描（每次复制 800+ 实体列表）；进程平均单核 16%，
无失控。用户要求先优化代码再本地起服务器验证收益。

改动（全在服务端热路径，行为语义不变）：
- `world.py` GameMap：新增 `summons` 索引（有主召唤物登记，add/remove/
  reposition 同步维护）与 `walk_bits_map()` 惰性地形位图（node→bool，
  地图加载后静态）；`_resolve_target` 候选集改走 `players/summons` 索引
  +保留怪互殴语义（只预过滤，不丢分支）。
- `autopilot.py` PathPlanner：`_ok`/`_h`/`walkable` 三层函数链删除，
  `advance` 内循环直接查 `walk_bits_map()` 位图 + `by_pos` 占用直查 +
  seen 集合免重复 dist 字典读写；`_step_to` 新增 dist≤6 贪心降级
  （正对目标方向格可走则一步直走，被挡仍回 A*——追游走怪不再每拍
  重建整场搜索）。
- `world_extra.py`：`item_template_by_name` 线性 genexpr → 惰性
  name→template dict 索引。
- `handlers_combat.py`：诱惑之爪转主人处同步 `summons` 索引。
- 测试适配：test_autopilot §11 造墙从 monkeypatch `GameMap.can_walk`
  改为数据层 `grid.flags` 置位 + 位图缓存失效（补丁式造墙已进不了
  位图热路径）；test_tick_systems 远程测试怪 hit=15 固定必中（消除
  飞矢用例 12/15 概率性 flaky）。

验证：
- 进程内 bench（`scripts/bench_tick_cpu.py`，3 在线托管+2 离线代理+
  E603 满怪，同一 GameService.tick）：8000 tick 墙钟 **66.6s → 9.0s
  （7.4×）**。
- 真实进程（`scripts/bench_server_cpu.py`：起 pyserver → GM 双角色
  `@离线 grant`+afk start → 断线转离线代理 → psutil 采样）：总量
  采样受代理随机行为影响方差大（busy 3.7~12.3s/60s 波动）；改看
  py-spy 火焰图相对占比（`scripts/bench_profile_ab.py`，90s/100Hz）：
  `_resolve_target` 从 TOP3（6.5%）跌出 TOP20，PathPlanner 0.3~0.7%
  持平（本地空图代理杀怪少，寻路触发远低于线上），事件循环空等占比
  22%→9.6%。
- 回归全绿：test_autopilot 39/39、hosted_opt 36/36、hosted_fixes 14/14、
  afk_supply 43、afk_maintain 46、afk_offline 57、bots 29、mage_kite 5、
  afk_log 47、pyserver 132/132、tick_systems 181（+基线预存 MonItems
  尾空格 1 FAIL，非本批）。

遗留：线上真实收益以部署后 py-spy 对照为准（本地代理负载形态与线上
多人+多代理不同，寻路收益在线上会更大）。

## §168 服务器重启后登录界面无反应（前端自动重连）（2026-11-25）

问题（用户报告）：服务器重启后，在线玩家窗口退回登录界面（正常），但输入
密码后无法登录、也没有任何报错；必须刷新页面才能登录。

根因（`frontend/main.mjs::watchDisconnect`）：旧逻辑只对「已在游戏里」的
断线做处理（回登录+新建连接）；**登录界面自身的 onclose 被直接忽略**
（当时担心服务端没起来时「连不上→onclose→再回登录」死循环）。服务器重启
瞬间在线玩家被踢回登录界面后，`this.conn` 仍指向已死的旧 WebSocket，
`Connection.send()` 对非 OPEN 连接返回 false 静默失败——输什么密码都发不出去，
`loginResult` 永远不来，界面无任何反馈。刷新页面=整页重跑 init()=新连接，
所以刷新就好了。

修复（`frontend/main.mjs` + `frontend/net/Connection.mjs`）：
- watchDisconnect 不再跳过登录界面断线：任何场景掉线都走「提示→自动重连」。
  死循环防护改由 onReturnLogin 的重连上限承担（6 次 × 3s ≈ 20s 窗口，
  覆盖部署重启时长；全失败则提示「请刷新页面重试」并保留原绑定）。
- onReturnLogin：先 connect 成功再换绑（bind/sound.attach/switchTo），
  失败抛错绝不带着打不开的新连接进登录界面；成功时 hideNetError() 撤横幅。
- Connection.close() 置 `_intentional`，watchDisconnect 对主动断开（登录窗
  关闭按钮、选角退出流程内 conn.close()）不触发自动重连。
- 登录界面掉线提示文案改为「正在尝试重连…」（区别于游戏内的「即将返回登录界面」）。

验证：`scripts/test_relogin_after_restart.py`（D:/Python39 Playwright +
D:/Python310 pyserver）：起服→登录界面→kill 服务器→原端口重启→**不刷新
页面**→断言自动重连成功→真实输入框填密码回车→进 SelectCharScene。
6/6 PASS（修复前该链路在第 5 步等待超时复现原 bug）。

## §169 手机端自动横屏（2026-11-25）

反馈：玩家手机打开浏览器没有自动横屏。

背景：浏览器不允许无手势自动改屏幕方向；`screen.orientation.lock()` 只在
全屏态可用（Chrome 系），iOS Safari/微信 WKWebView 两个 API 都不支持。
原实现只有 `#rotate-tip` 横幅提示手动旋转（且仅游戏场景显示）。

改动（`frontend/core/orientation.mjs` 新建 + `main.mjs` 装载）：
- 触屏设备 + 小窗竖屏（min 边 ≤560 判定手机，排除触屏笔记本）才安装助手；
- 监听 `pointerdown`（capture）：玩家进页面第一次点按即尝试
  `requestFullscreen({navigationUI:'hide'})` → `orientation.lock('landscape')`，
  成功即移除监听；失败静默、下次点按重试（部分 WebView 手势窗口已过才放行）。
- `orientationchange` 若被系统旋回竖屏则重新挂监听再锁。
- 环境无 `orientation.lock`（iOS）直接跳过不抢全屏——单独进全屏锁不了方向反而更糟。
- 横竖屏切换后缩放由既有 resize→fitStage 链路自动重排，零额外工作。

验证：Playwright 手机竖屏仿真（390×844 has_touch）：点按后进入全屏、无页面
报错；桌面触屏宽窗（1440×900 has_touch）：不触发全屏。6/6→2/2 通过。

## §170 半兽古墓/天然洞穴刷怪密度对齐原版（2026-11-25）

反馈：玩家称半兽人古墓一层刷怪点过于密集，怀疑「一层比二三层还密」。

排查（analyze_mongen_density.py + sim_mongen_actual_density.py + 线上 GM 探针）：
我们的 MonGen 相对原版（delphimir2 蓝本）在骷髅系洞穴做了两类改动：
1. 数量翻倍（D001 617 vs 原版 332、D002 611 vs 305、D003 622 vs 297、
   D011 546 vs 322、D012 692 vs 377）；
2. 大群条目散布被改窄：原版 `range=200` 全图散布 → 我们改 `range=50`，
   100+ 只挤在中心点 (200,200) 一片走道 ——「刷怪点密集」的直接来源。
附带：`_free_near` 撞墙 50 次回退中心，range 窄+墙多的洞穴额外向中心堆。

改动（gamedata/Mir200/Envir/MonGen.txt，仅 D001~D003、D011~D012 普通条目）：
- 数量与 range 逐条回原版（含原版洞口 range=30 教学小聚点，如
  D001 (250,260)/(190,125)/(250,90)/(30,30) 各 10 只）；
- interval 时间列维持我们的现状（8 分钟）不跟原版 3~4 分钟，避免复活提速抵消；
- 「0」后缀精英怪（骷髅战士0 等）count=10 **按用户要求保留不改**（高爆玩法）；
- 骨精灵（骷髅精灵）条数保留。
验证 diff：五图普通条目与原版逐条一致，仅精英 0 系差 count（有意保留）。

复跑 sim_mongen_actual_density.py（±12 视野窗口）：
D001 370 只 p50=3 峰值10 | D002 346 p50=5 峰值14 | D003 342 p50=4 峰值10
D011 291 p50=4 峰值11 | D012 392 p50=4 峰值13 —— 层间梯度恢复，无「一层最密」。

僵尸洞核查（用户追问）：D401~D406 与原版逐条一致（各 185~198 只），
仅我们多每层 1 只尸王3（私服 BOSS，非密度问题）；模拟视野 p50=8 峰值 18~20，
是矿洞小图正常水位，不需要调整。

未动的其他洞穴（此前已核）：沃玛/祖玛/石墓/香石古墓/矿区/地牢全部与原版一致
或为原版 BOSS 殿设计；D205* 牛魔已被 content_policy 剥离不加载。

## §171 托管「捡东西时莫名暂停」修复（2026-11-26）

反馈：托管经常莫名其妙暂停，玩家观察最多发生在捡东西时。

根因（复现于最小 WS 探针）：托管角色每捡起一件可用物品（药水/卷轴，
StdMode≤3）→ 服务端下发 `bagUpdate` → 前端按原版 AddItemBag 逻辑
**自动**把新物品填进快捷栏空位，代发一条 `quickSlot`（PlayScene.mjs
:2432-2449，非玩家手势）→ 服务端 `on_message` 把 `quickSlot` 列在
MANUAL_TYPES，按「手动优先」pause 托管。即托管每捡一瓶金创药就自己
把自己暂停，状态显示「已暂停 · 手动接管」。捡装备/金币不触发（不进
快捷栏），与「捡东西时容易停」的现象吻合（挂机捡得最多的就是药水）。

修复（`pyserver/autopilot.py`）：MANUAL_TYPES 移除 `quickSlot`。
- 玩家真的手动拖快捷栏也仅是本地引用变化，不会中断战斗决策，口径无损；
- 其余手势类消息（move/attack/useItem/takeOnItem…）照旧立即接管。

验证：
- `scripts/test_autopilot.py` §3 新增「quickSlot 自动补位不暂停托管」用例，
  40 PASS / 0 FAIL；
- `test_afk_maintain` 46 / `test_afk_supply` 43 / `test_afk_offline` 57 全过；
- 真实 pyserver WS 复现路径（enterGame→autopilot on→quickSlot→status）
  修复前 active=False reason=手动接管，修复后保持 active。

## §172 换图后尸体站起来（changeMap 快照漏还原）（2026-11-26）

反馈：怪物死后出尸体，切换地图再切回来，尸体变成站立状态；该问题早年
修过（23117b1），死亡动画泛化（baf5305/§164）后复现。

根因：前端把服务端 brief 的 `corpse:true` 翻译成渲染层 `_corpse` +
`_action='death'` 的逻辑存在于**三条路径**，但只补了两条：
1. PlayScene 构造（登录/重连快照）—— 23117b1 补的；
2. `entityAdd`（回视野）—— 一直有；
3. **游戏内 `changeMap` 消息（走门/传送/回城卷）—— 裸 for-set，没补**。
23117b1 当时 changeMap 分支大概率没尸体（换图即清场重刷），后来尸体留 3
分钟 + 同图传送也发 changeMap，路径 3 就暴露了。服务端 brief 一直是对的
（`o["corpse"] = not alive and corpse_until>0`，world.py:1002）。

修复（`frontend/scene/PlayScene.mjs` case 'changeMap'）：实体重建循环
对齐路径 1——`e.corpse` 则置 `_corpse/_action='death'/_actionAt/_dieUntil=0`
并 `prefetchDeathFrame(e)`。

验证：新回归 `scripts/test_corpse_change_map.py`（Playwright：@刷怪 鸡→
托管击杀→GM 同图传送离开再回原点→断言实体 `_corpse=true && action=death`；
二次换图复验）5 PASS；stash 掉修复复跑精确 FAIL 2 项（action=None
corpse=False，即站立），证明用例有效。`test_corpse_fx.py` 8 PASS 无回归。

## §173 道士自动喝蓝药（在线+离线）（2026-11-26）

需求（用户）：道士离线挂机不会自动喝蓝——治愈术施法没蓝就要补蓝药。

现状核对：`_survive` 补蓝段只认 `p.job == 1`（法师），道士蓝空了
`_taoist_heal`/`_taoist_fire` 直接放不出，只能喝血药硬扛；离线补给
`_supply_buy` 的 `want_mp = p.job == 1`、`_supply_check_trigger` 的
「药水耗尽」判据同样只覆盖法师。

改动：
- `autopilot.py`：`_min_mage_spell_cost` 改名 `_min_spell_cost` 并按职业取
  技能集（法师 MAGE_ATTACK_SKILLS / 道士新增 `TAOIST_MP_SKILLS=(2,13)`
  治愈术+灵魂火符），补蓝触发条件 `p.job == 1` → `p.job in (1, 2)`。
  仍是「空蓝才喝」口径（含 inc_spell 回蓝池连最低耗蓝一招都放不出）。
- `afk.py`：`_supply_buy` 的 want_mp 与 `_supply_check_trigger` 缺蓝判据
  同步改为 `job in (1, 2)`（道士离线也会因蓝药耗尽触发进城补给）。
- 引用点 `test_autopilot_hosted_opt.py` 随改名更新。

验证：`test_autopilot_hosted_opt.py` 新增 [2d] 道士喝蓝三断言（判据生效/
蓝够不喝/空蓝自动喝），39 PASS；其余回归全套无破坏。

## §174 在线托管一键维护（修装备+卖肉按钮）（2026-11-26）

需求（用户）：在线托管旁边加「自动修装备（武器/衣服/项链/戒指/手镯）+
自动卖肉」按钮，触发后自己找 NPC 对话交易。

实现（`pyserver/autopilot.py` 新增 `MaintenanceFlow`）：
- 触发：WS `autopilot {action:'maint'}`（service.py handler）。托管未开启
  时先自动开启再维护；背包没肉且身上无低耐久装备 → 明确拒绝回执。
- 流程：当前地图卖肉（StdMode 40，找肯收购的最近商人，≤120 格）→
  逐件修理身上装备（槽位 MAINT_EQUIP_SLOTS=武器/衣服/项链/左右戒指/
  左右手镯，耐久<50% 才修），每件「贴近→脱下→修理→穿回」严格串行，
  与离线 §162 同一套安全约束：先 ≤12 格再脱（_npc_and_player 硬限 15）、
  满包不脱、修不动记 failed、任何中断路径 abort 先穿回。
- 修理商判定 `npc_really_repairs` 从 OfflinePilot 上提为模块级（afk 侧
  改薄封装，防「彩票员被判能修」的老坑），两侧共用。
- 不跨图：在线玩家随时可能接管，绝不擅自跑回比奇；本图没修理商就跳过
  并在收尾文案说明。维护期间 `_survive` 保命照常、跳过 `_ensure_equipment`
  自动补装（防抢背包槽位活锁，同 §162）、不打怪、不做零进展熔断。
- 收尾（成功/超时 180s/全跳过）恢复战斗循环；pause/disable 均兜底穿回。
- 前端（PlayScene.mjs）：托管条正下方新增「修装备·卖肉」按钮
  （#autoPilotMaintBtn），点设置面板时自动隐藏避让。

验证：`scripts/test_autopilot_maint.py` 状态机单测 25 PASS（卖修顺序/
脱-修-穿串行/无商跳过/修不动不死磕/中断穿回/能力判定）；
`scripts/test_autopilot_maint_ws.py` 真实进程 WS 7 PASS（含线上同构路径
「前往屠夫→卖肉 3 块→收尾」实拍状态流）。Playwright 前端按钮渲染+点击
通过（点后托管正常进入维护态）。

顺带（用户插问）：精英怪爆率 2.0→5.0 已提交 `e6758f4 [调整]`
（elite.dropMult 四处口径同步，test_elite_monster 21 PASS）。
