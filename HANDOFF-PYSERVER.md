# jootm2 Python 服务端分支交接文档

> **最后更新**：2026-11-16（§88 一键镜像部署脚本；文档瘦身：§69~§82 归档到 `docs/handoff/archive-2026-11-f.md`）
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
| `docs/handoff/archive-2026-11-f.md` | §69~§86：第二轮数据清理 / 技能修炼点 / 骷髅战士掉落 / 新手线掉落整表恢复与商店分档 / Magic.csv 全列对齐 / 高级之家测验与托管六问题 / 尸体渲染 / 大地图入口标注与过滤（原文两节重号 §79，逐字保留）/ 误伤宝宝豁免 / 治愈自家宝宝 / 召唤熟练度 / 半兽古墓刷怪 176 / 背包满快捷栏自动填充 / 护身符耗尽核对 / 道士托管治愈+火符 |

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

## §87 /site 数据库站缓存分档：图片 60 秒 → 7 天（2026-11-16）

**问题**：用户反馈阿里云站每次打开地图页图片都重新加载。排查确认缓存头**不缺**：`pyserver/net.py` 的 `/site` 路由一直有 ETag / Last-Modified /304 条件请求，但统一用 `cache_override="public, max-age=60, must-revalidate"`覆盖所有文件——包括 893 张地图/图标 PNG。超过 60 秒后每张图都要发一次条件请求（公网逐张一个 RTT），带宽有限时观感就是「重新加载」。旧注释说明60 秒是为规避「文件名不变 + immutable 一年看不到新图」，方向对但取值过保守。

**改动**（`pyserver/net.py`）：
- 新增 `WEBSITE_STATIC_MAX_AGE = 7*24*3600`、`WEBSITE_IMAGE_EXTS` 与`website_cache_control(name)`：HTML `no-cache`；图片 `public, max-age=604800, must-revalidate`；JS/CSS/JSON 及未知类型 `max-age=300, must-revalidate`。
- 不用 `immutable`：站点重生成时文件名不变，7 天到期后靠 ETag 自动换新图。
- `_serve_file` 新增可选 `cache_fn: Callable[[str], str]` 参数，优先级`cache_fn > cache_override > cache_control`；其余调用点零改动。
- `/site` 路由改传 `cache_fn=website_cache_control`；仍 `allow_gzip=False`（站点同步 zlib 压到游戏 tick 的约束不变，见 §60）。
- `docs/WIKI-PLAN.md` 同步路由/缓存分档与验证命令。

**验证**：`test_pyserver.py` 扩到 **114 PASS / 0 FAIL**，新增 9 项覆盖分档表、图片不用 immutable、`/site/*.png` 长缓存 + ETag、条件请求 304、HTML `no-cache`。本地起 `python -m pyserver --port 18080` 实测响应头：PNG `public, max-age=604800, must-revalidate`、HTML `no-cache`、CSS `max-age=300`，带 `If-None-Match` 返回 304 且无 body。

**部署提醒**：改的是 Python 代码，阿里云需**更新镜像并重启**才生效（只rsync `data/website/` 不够）。验证：`curl -sI https://aliyun.notmmao.com/site/assets/icons/items/1.png | grep -i cache-control`。浏览器第一次刷新仍会重取一次图片（旧 60 秒缓存里的条目过期/覆盖），此后 7 天内零图片请求；若改完想立刻看新图，Ctrl+F5 强刷即可。

---

## §88 一键镜像部署脚本 + bed804c 只推镜像部署（2026-11-16）

**背景**：用户要求「推送到阿里云，只推送镜像，其他资源文件不变」。Agent 侧Bash 权限分类器（auto 模式依赖的远端模型）间歇性超时，构建命令连续被挡；用户提出改为一键脚本由本人在终端运行，绕开分类器。

**新增 `scripts/deploy_image.sh`**（bash，`bash -n` 通过）：
- 流程 = §21.6 标准路径的脚本化：`gen_version.py`（优先 `D:/Python310/python.exe`）→ `docker build` 短 hash + `latest` 双标签 → push ACR（失败提示`docker login --username=notmmao@gmail.com`）→ ssh 阿里云`pull → docker tag jootm2:python → cd /root/jootm2 && docker compose up -d` → 轮询验证 `/api/version` version == `git rev-parse --short HEAD`（3 秒 × 10 次）+ `/api/status` 200。
- **只推镜像，无任何 rsync**：不碰服务器 `data/`、`resources/`、`gamedata/`、`data/website/`。⚠ 因此 gamedata 改动不会随本脚本生效（§21.8 挂载覆盖坑），改了 gamedata 需另行 rsync；工作区不干净时脚本开头打印警告。

**本轮部署结果（bed804c，用户亲自运行脚本）**：
- ACR 双标签 `bed804c` + `latest`，digest `sha256:8f5ba39a77c9…`；阿里云容器 Recreated → Started。
- 验证：`http://139.224.24.88:8080/api/version` 返回 `"version":"bed804c"`（builtAt 2026-11-16T14:56:31），`/api/status` **200**。按约定不做深度回归。
- 本次镜像包含 §85~§87 的代码改动（修复油分流 / 消耗品审计 / /site 图片长缓存）；gamedata 未同步，若宿主 gamedata 落后于仓库，模板 CSV 直载（§84）读到的仍是宿主旧数据。

**文档瘦身**：主文件超 500 行硬上限（927 行），§69~§82 逐字归档到`docs/handoff/archive-2026-11-f.md`，主文件保留 §83~§88。§89 追加前再把§83~§84 归档到同文件，主文件保留 §85~§89。

---

## §89 托管优化：法师远程 / 背包限流清肉 / 武器与符自动补戴（2026-11-16）

**用户诉求**：① 背包容易满（尤其挖肉后），打一会就满包回城；② 战士靠被动没问题，道士已优化过远程，法师还贴身砍怪；③ 武器坏了、护身符没了要自动换背包里的下一个。

**方案（用户拍板：挖肉限流+智能清理 / 法师按等级选最优技能 / 武器+护身符补戴）**，全部改在 `pyserver/autopilot.py`：

1. **背包**：新增 `BUTCH_BAG_RESERVE=8`——背包剩 ≤8 格就停挖肉，改打怪捡金币（钱不占格）；`_clean_meat()` 在吃紧时按肉的耐久（=m_nMeatQuality，卖价正比）从差到好逐份丢，留 `MEAT_KEEP=6` 份最值钱的，2s 一份走 `_drop` handler 不刷屏；进入 `cleaning_meat` 模式后持续丢到达标。**不再满包即回城停机**——只有真塞满（全是非肉物品）才退回原行为用回城卷回城。顺带修 `_want_pickup`：StdMode 40（肉）和 1（干肉）不再主动捡回，否则清包丢的肉立刻被捡回来白清。
2. **法师远程**：新增 `MAGE_ATTACK_SKILLS=(11,5,1)`（雷电术>大火球>火球术，都吃 MC、单目标 RM_DELAYMAGIC、不吃符）与 `_mage_fire()`：施法半径 8 格内（Magic.pas:266）按「已学+有蓝+冷却好」选最高优先级招；**没蓝/没学绝不贴身砍**（法师物理输出极低），只等回蓝；超射程照常走近。自查 MP/冷却避免「魔法值不足」刷屏，`_cast_magic` 仍做最终校验。
3. **装备自动补戴**：`_ensure_equipment()`（1s 节流）在武器槽空时从背包挑StdMode 5/6 中耐久最高的一把走 `takeOnItem` 补上；`_ensure_amulet()` 补护身符（Shape=5）/毒粉（Shape≤2）到 U_BUJUK/U_ARMRINGL 空槽（**只补空槽，否则毒粉会把护身符顶回背包**）。`_taoist_fire` 施法前也即时补符，避免刚耗完一张符就回落近战。`_best_wearable` 只做候选筛选，等级/重量/槽位校验仍由`_take_on_slot._can_take_on` 统一把关，不绕过规则。

**新增字段**（AutoPilot dataclass）：`last_clean_at/cleaning_meat/last_equip_at`。**回归**（全用 D:/Python310/python.exe）：新增 `scripts/test_autopilot_hosted_opt.py` 14/0（法师技能优先级+没蓝不近战、武器换装、护身符毒粉双补、挖肉限流与清肉品质排序）；既有 test_autopilot 39/0、test_autopilot_taoist 18/0、test_autopilot_ws 16/0、test_hosted_fixes 14/0、test_all_messages 91/0 全绿。

---

## §90 边界村小店：行会回城卷 → 回城卷（2026-11-16，用户指正）

**根因**：§72 给边界村小店老板（`4Tdu-0.txt`，map 0 (291,610)）配货单时照抄「176 通用杂货铺模板」（`QuestDiary/商人/杂货铺.txt`），该模板只有**行会回城卷**没有回城卷；但此 NPC 对话 `@steleport`（:41-46）明说经营**回城卷**（"立刻返回村庄"），全文未提行会卷。新手村卖回行会城堡的卷对无行会新人无用。176 基线里回城卷在沙巴克杂货铺有售（`沙巴克_杂货-3.txt`：回城卷 100/10 + 行会卷 100/5）。

**改动**：`4Tdu-0.txt` [goods] 行会回城卷 → 回城卷（库存/刷新列不变），附注释。比奇杂货铺（7Bst-0）货单与 176 通用模板逐字一致，**不动**。

**验证**：引擎 `NpcScriptEngine.goods` 实解析：回城卷 110 金（100×1.1，%100）在列、行会回城卷已除；test_pyserver 115/0（原「对齐176」断言更新为回城卷 +新增「卖回城卷而非行会回城卷」断言）、test_tick_systems 180/0。

---

## §91 头顶名字/血条分类增强 + 系统菜单开关（2026-11-16）

**原版结论（本地 Delphi 源码核对）**：官方**不按 NPC/怪物/自己/别人分类配色**。名字只有一个服务端下发的调色板索引 `m_nNameColor`（`DrawScrn.pas:326-344` 用`BoldTextOut(..., fcolor, clBlack)` 画悬停目标和指到的自己），语义是关系/状态（`ObjBase.pas:19197 GetNamecolor`：PK1 黄 251 / PK2 红 249；`GetCharColor`再加 PK 标记棕 47、同盟蓝 180、敌对行会橙 69、攻城区绿 221；宝宝按`SlaveColor` 变色）。血条全实体同一张黑底红条（`DrawScrn.pas:306-319`），且缺省不全显示（`MShare.pas:429` 的 `boShowRedHPLable=False`）。

**改动**：
- `pyserver/util.py` 新增 `NAME_COLOR_*` 调色板常量；`world.py` 的`Player.name_color()` 按 PKLevel/PKFlag 下发关系色，`Player.brief_json` /`to_player_json`、`Monster.brief_json`、`world_extra.Npc.brief_json`统一带 `nameColor`（普通怪 255、NPC 淡紫 239、宝宝仍用原版等级色）。
- `frontend/map/MapActor.mjs`：HUD 偏好从模块常量改为可热更新的`this.hudMode`（`enhanced / showHpLabel / hoverNamesOnly`）。增强模式下名字按服务端索引取原版调色板色（自己白 255→金黄 251，PK 色优先不被盖）；血条填充**始终原版红**（避免「像魔法条、和左下血球不一致」的老问题），只给自己加白描边、宝宝加绿描边，NPC 不画血条。关闭时完全维持旧观感。
- `frontend/scene/PlayScene.mjs`：构造时读 `jootm2.hudEnhanced` /`jootm2.showHpLabel` / `jootm2.nameOnFocusOnly`，注入 `mapActor.hudMode`；系统菜单（Esc）新增三个即时生效开关：名字/血条增强（默认关）、头顶血条全显示/原版时机、名字全部显示/悬停才显示；`_setHudPref` 统一落盘 + 刷菜单。
- 名字快照新增 `masterId` 供宝宝描边判定。

**验证**：`test_map_view` 新增 6 条颜色断言后 40/0（首轮 2 FAIL 是随机刷怪占住测试门格导致，复跑通过）；`test_pyserver` 115/0、`test_tick_systems` 180/0、`test_all_messages --live 8080` 91/0 + WS ping PASS；Playwright（3.9）实机确认默认 `enhanced=false`、菜单三项文字正确、点击后 localStorage 与`mapActor.hudMode` 同步、无 pageerror；前后抓帧确认自己名字由白变金黄、血条多白描边，怪名/红条不变。

---

## §92 女法师光头回归：hair 3..5 前端未钳制越界（2026-11-16）

**现象**：用户新建女法师（DB `t3`：sex=1 job=1 hair=3）进游戏仍是光头；§37.1已修过一次（当时是建角默认 hair=0）。

**根因**：§37.1 只把建角默认 0 改成 1..5，漏了原版绘制前的钳制。`MirClient/Actor.pas:3183-3188` 先算 `haircount := Hair.ImageCount div 600 div 2 = 3600/600/2 = 3`，把 hair 钳到 0..2 再乘 2，只有大于 1 才画头发（hair=0 → offset=-1 光头）。建角协议虽发 `1+Random(5)`（`IntroScn.pas:1427`），3..5 在客户端一律按发型 2 画。项目`ActorSprite.mjs` 直接用 `hair*2+sex`：hair=3/sex=1 → 600*(6+1)=4200
> Hair 库 3600 帧 → 取不到贴图 = 光头。DB hair=3 本身正确，服务端无需改；
选角立绘 `Prguse 440/480 + hair div 2`（`FState.pas:6112`）与`PlayScene.mjs:2951` 一致，也不受影响。

**改动**：`actorData.mjs` 新增 `HAIR_STYLES=3`（附原版依据）；`ActorSprite.mjs` 按原版先钳 0..2 再乘 2，只在大于 1 时 push hair 层。

**验证**：Node 直接调 `_frameSpec` 定量验证 sex 0/1 × hair 0..5：hair=0 无头发层；hair=1 → 1234/1834；hair=2/3/4/5 → 2434/3034（均 < 3600，3..5 与发型 2 同帧，符合原版）。`node --check` 通过。

---

## §93 挖肉距离排查 + 交接文档行数上限放宽（2026-11-16）

**用户反馈**：视觉感觉大老远就能挖肉，问官方距离是多少。

**原版依据（本地 Delphi 逐行）**：
- 服务端 `ClientGetButchItem`（`M2Server/ObjBase.pas:17458-17504`）：`if (abs(nX - m_nCurrX) <= 2) and (abs(nY - m_nCurrY) <= 2)`，即**切比雪夫距离 ≤2 格**（不是欧氏、不是 1 格）。关键：`nX/nY` 来自`CM_BUTCH` 的 `nParam2/nParam3`，是**客户端鼠标格坐标**，不是尸体真实坐标。
- 客户端 `PlayScene.ButchAnimal`（`MirClient/PlayScn.pas:2354-2369`）只找鼠标格 ±1 格内的死亡动物；`ClMain.pas:2832-2845` 按住 Alt+左键先本地`CM_SITDOWN` 预测播蹲下动作，**不等服务端回包**。
- 因此原版真正能挖到的尸体最远可离玩家 **3 格**（鼠标点离玩家 2 格 +尸体在鼠标 ±1 格内）；服务端距离不合格时静默拒绝，但 `RM_BUTCH`（`ObjBase.pas:17503`）在 if 块外**仍广播**，所以远处点照样播动作。

**项目现状**：`service.py::_butch` 用**尸体真实坐标**判定 ≤2 格，比原版更严（不会挖到 >2 格的尸体）；前端 `PlayScene.mjs:1108-1120` 与原版同款「鼠标 ±1 选目标 + 本地预测蹲下」。所以「视觉上大老远能挖」是**原版就有的客户端预测表现**，不是服务端放水；项目还多了一条原版没有的「离尸体太远了。」提示。未改玩法代码（官方距离本就是 2 格，项目已等价或更严）。

**文档规则**：行硬上限 500 → 700，主文件轮次 5 → 8 个（用户反馈稍改即超）。

---

## §94 全服杂货铺审计：只卖行会回城卷的 7 家补回城卷（2026-11-16，用户口径）

**用户口径**：继 §90 边界村后，「检查其他的 NPC，例如银杏村 NPC 等，如果只卖行会回城券就不对」。即杂货铺**不能只有行会回城卷而无回城卷**。

**审计**（`data/audit_scrolls.py`，gitignored；解析全部 `Market_Def` [goods]，含 #CALL 子脚本，按 `MerChant.txt` 映射 NPC 名，正则 `(?<!行会)回城卷` 查对话）：全服 10 家卖卷轴的杂货铺里，**7 家只卖行会回城卷**，3 家两种都卖（正常）。

| 脚本 | NPC（地图坐标） | 对话提回城卷 |
|---|---|---|
| 7Gst-0 | 陈家铺老板@0(643,611) **银杏新人村** | 是（@steleport） |
| 7Bst-0 | 杂货铺老板@0(378,298) 比奇城 | 是（@steleport） |
| 7Dst-4 | 恶魔杂货匠@4(255,224) 封魔谷 | 否 |
| 7Est-3 | 老人@3(855,661) | 否 |
| 7Mst-3 | 流浪汉@3(346,334) 盟重 | 否 |
| 7Sst-2 | 罗家铺子老板@2(505,479) 毒蛇山谷 | 否 |
| 7Wst-1007 | 杂货店老板@1007(11,14) 白日门 | 否 |

注：7Est-3 的货单属**老人@3**；洞穴不法小贩用的是 `7Est-DM001.txt`（无卷轴，不在问题之列）。已正常的 3 家：7Ast-3 沙巴克小贩、7Yst-5 月崕岛杂货小贩、gmzh-28 新手村小贩（均回城卷+行会回城卷）。

**根因**：与 §90 同源——照抄「176 通用杂货铺模板」`QuestDiary/商人/杂货铺.txt`，该模板只有行会回城卷。176 Gee 基线树本身除沙巴克外**全部杂货铺也只卖行会卷**（私服模板失真，非原版权威）；ruchu8 站点 `site_npc.csv` 的杂货铺行 Sell/Buy 列**为空**（站点只记录了仓库类 NPC 的买卖），无直接货单证据，但银杏/比奇两家 NPC对话 `@steleport` 明说经营回城卷，与边界村同款。

**改动**：7 家 [goods] 在行会回城卷行前**补一行回城卷**（保留行会回城卷），库存沿用各文件本地约定（7Est-3 老人=10，其余=100，刷新 10），附注释。`data/fix_guild_scrolls.py`（gitignored）CRLF 二进制插入 + `goods_rows()` 复核。**此举取代 §90「比奇杂货铺不动」的结论**——用户新口径优先于「与 Gee 模板逐字一致」。

**为何「补」而非「替换」**：用户口径是「只卖行会卷就不对」（缺回城卷是问题，并非要求删除行会卷）；项目既有的 3 家正确店（gmzh-28/沙巴克/月崕岛）都是**两种都卖**；补一行是最小改动，且不让盟重/比奇/白日门等城的行会玩家失去行会卷补给。边界村 §90 是**替换**，因用户当时明确「卖的不是行会回城卷，应该是回城卷」（逐店指令）。若用户希望银杏等新手村也改成只卖回城卷，可再单独替换。

**验证**：复审审计 `只卖行会回城卷=0 家 / 两种都卖=10 家`；7 文件纯 CRLF 无混合换行；test_pyserver **122/0**（新增 7 条「杂货铺 X 售回城卷」断言，银杏/边界村旧断言不破）、test_tick_systems **180/0**、test_all_messages **91/0**。

**提交**：商店货单已提交（`60bd938 [数据]`，7 个 Market_Def txt + test_pyserver.py 断言）；§90 边界村改动见更早的 `be6c3f1 [数据]`。

## §95 托管法师：自动补蓝 + 补回原版施法输入节拍（2026-11-16）

**用户反馈**：法师托管应该自动喝蓝药；火球像机关枪，怪都死了还在放。

**根因**：服务端技能 CD 本身没错——项目 `MagicTemplate.resolve()` 把 DB 的`Delay` ×10 存成毫秒（`templates.py:162`，火球 60→600ms），`_cast_magic:996`按 600ms 拦截。但原版客户端另有一层输入节流：对目标施法要求`CanNextAction and ServerAcceptNextAction`（`MirClient/ClMain.pas:2421`），随后 `g_dwMagicDelayTime := 200 + pcm.Def.dwDelayTime`（:2428）；F1-F8 再按`g_dwSpellTime(500) + g_dwMagicDelayTime`（:2040，`MShare.pas:415` 默认 500）限制下一次施法，即火球真人节拍约 **760ms**。托管直接调 `_cast_magic`绕过客户端这层，加上火球飞行特效重叠，观感就成了机关枪。

**改动**（`pyserver/autopilot.py`）：
- `SPELL_INPUT_BASE_MS = 700`，新增 `_spell_ready()`：相邻施法间隔`max(t.delay, 700 + t.delay / 10)`（除以 10 是把项目毫秒值还原成原版DB 口径）。火球 max(600,760)=760ms；雷电 max(1000,800)=1000ms。法师 `_mage_fire`、道士 `_taoist_fire` 都接；`_taoist_heal` 施法后也更新`last_cast_at`，避免治愈术紧接火符。技能自身 CD 仍由 `_cast_magic` 管。
- `_mage_fire` 目标已死直接早退，不再对尸体补一发。
- 三档 preset 加 `potion_mp`（安全 0.50 / 均衡 0.35 / 效率 0.30）；`_find_mana_potion()` 挑 StdMode 0 且 MAC>0 的药（Shape1 太阳水优先，其余按回复量从小到大）；`_survive` 蓝线把 `inc_spell` 回蓝池算进当前蓝量，免得池子没滴完就连灌；1s 节流，喝药仍走 `useItem` handler。

**验证**：`test_autopilot_hosted_opt` 19/0（新增 5 条：连点只 1 发 / 过一拍才第 2 发 / 低蓝自动 useItem / MP 提升 / 蓝药消耗）；autopilot 39/0、taoist 18/0、ws 16/0、hosted_fixes 14/0、all_messages 95/0、pyserver 122/0。

---

## §96 NPC 脚本 give 奖励不下发客户端：玛法领路人「领了没送」（2026-11-16，用户反馈）

**用户反馈**：玛法领路人（`Npc_Def/玛法领路人-0.txt`，map 0 (280,610)）3 级去领补助，「我已经三级了，但是没有送！」。

**根因（不是没实现，是实现了没下发）**：脚本 `@next1` 的 `#ACT give 金币 5000`确实执行——`NpcScriptEngine._run_action`（npc_script.py:466）→ `world.give_item`（world_extra.py:481）把 `p.gold += 5000`。但 `give_item` 是**纯服务端状态修改、不发任何网络消息**；而 `handlers_npc.py::_handle_npc_result` 执行完脚本只发 `npcSay`（text/options/shop），**从不补发背包/金币同步**。于是服务端金币涨了、客户端毫不知情。对照 `_buy_item:160`/`_sell_item:278` 都在改 gold/bag 后调 `send_bag_update`，唯独脚本give/take 这条路径漏了。**Java 对照版同样漏**（`GameService.java:650-687 handleNpcResult`只发 npcSay），所以不是 Python 移植回归，是两栈共有的隐藏缺口（CLAUDE.md 铁律 5「写了但没人消费」）。

**原版依据（本地 Delphi 逐行）**：`M2Server/ObjNpc.pas:8981 ActionOfGiveItem`——给金 `IncGold(nItemCount)` 后 **`PlayObject.GoldChanged()`（:9001）**；给物`m_ItemList.Add(UserItem)` 后 **`PlayObject.SendAddItem(UserItem)`（:9028）**。即原版每给一笔就同步客户端，本项目脚本路径缺这一步。

**改动**：`handlers_npc.py::_handle_npc_result` 在 `session.send_json(o)`（npcSay）之后补一行 `self.send_bag_update(p)`（全量背包+金币+快捷栏+负重，world.py:1437）。一次同步覆盖脚本里任意 give/take/金币变动，对所有 NPC 脚本奖励生效（不止玛法领路人）。

**验证（TDD 复现）**：`test_all_messages.py` 新增玛法领路人 4 断言（3 级主对话含 @next1 /@next1 服务端到账 5000 / **领取后下发 bagUpdate 且 gold==5000** / 一次性标记 [001] 置位）。临时去掉修复行复跑：「服务端到账 PASS、客户端 bagUpdate **FAIL**」——精确复现用户症状；恢复后全绿。test_all_messages **95/0**、test_pyserver **122/0**、test_tick_systems **180/0**。

**注意**：① 需**重启服务端**才生效（代码改动）；浏览器 Ctrl+F5。② 本轮**只修同步 bug，未改脚本内容**——玛法领路人仍是「3 级领 5000 金」。用户要的「新号送 1000 金+回城卷+随机传送卷+小蓝小红」涉及领取门槛（1 级 vs 保留 3 级）与药水数量，仍待用户澄清后再改。

**提交**：已提交（`6f95fa8 [修复]`），含 handlers_npc.py + test_all_messages.py 断言。

## §97 gamedata 目录结构参考文档 docs/GAMEDATA_GUIDE.md（2026-11-17）

**需求**：用户要求整理 gamedata 下各文件作用与关系的参考文档，方便 AI Agent 和人类阅读。

**产出**：`docs/GAMEDATA_GUIDE.md`（纯文档，零代码改动）。结构：30 秒速览 → 目录总览 →数据流图 → 文件速查表（每个文件给出加载/消费代码 `文件:行号`）→ **名字连接链**（怪物链/物品链/NPC 链/任务链/地图链/技能链，全部是字符串匹配无外键）→ 分组格式详解（CSV 列义/脚本引擎/价格链路/掉落语义）→ 未消费文件清单（附 Delphi 原版用途行号）→ 已知缺口 →编码/多树/周边工具 → 维护规则清单。与 `docs/GAME_DATA_REFERENCE/`（外部视频实测资料）分工不同，已在文档头部互相说明。

**编写时全量 grep 核验出的现状**（已写入文档 §6/§7，**只记录不修复**，修复需另立轮次）：
- 两栈均未消费：AdminList/GuardList/MakeItem/LineNotice/SabukW/AttackSabukWAll/StartupQuest/沙巴克备份（原版用途考证到 LocalDB.pas:96/216/414/1586、Castle.pas:333/540、svMain.pas:717）；GuildBase 仅 Java 消费（GameWorld.java:363）。
- 解析了但没消费/没持久化：① quest_params 不在 persist() 存档字段（service.py:742-764）→ 任务旗标重登清零；② MapQuest 拾取触发（item_name 列）只有杀怪触发调用点；③ _save_guild 写 data/pyserver_guilds/*.json 但启动无加载代码 → 行会实为内存态；④ 沙巴克状态不回写 Sabuk.txt；⑤ StartPoint 只取第一行（原版 g_StartPointList 整表）；⑥ SAFE/FIGHT 标志无消费；⑦ Monster.csv WalkStep/WalkWait 列未映射；⑧ 主树无QuestDiary（#CALL 子脚本落空，176 树才有）。
- 关键口径复述：CSV 每次启动重灌（db.py:118）；商店售价=round(Price×1.1)×%N/100（npc_script.py:544）；MiniMap 1-based；MonItems 文件名尾随空格 strip；Magic.csv GM 变体 INSERT OR IGNORE 保留先出现行。

**验证**：纯文档改动，跑离线回归确认基线未受影响：test_pyserver **122 PASS / 0 FAIL**（D:/Python310/python.exe，与 §96 数字一致）。

**挂接**：本文「给下一位 agent 的阅读顺序」新增第 8 条指向该文档。

## §98 商人 NPC 清理魔改 + MerChant/Market_Def 重命名（2026-11-17，用户指令，待确认提交）

**用户指令**：① 清理魔改 NPC；② MerChant.txt 及对应脚本按「类型(原编码)-名字-地图编号」重命名，同名同图多类型合并为有商品的模板；③ 修正单元测试固化名。**改完自查，不提交，用户确认后提交**。

**清理（85 行 + 78 脚本）**：赌场 54 行（9Ega 系：B010-B013/B101-B140 赌场小妖女、T105 古惑仔×5、T118/T125/T1363/T138、0101 赌场老人）；封魔谷(图4)测验 17 行（kljl 骷髅精灵/klzj1-4/klzs1-4/kl1-8）；白日门(图11)道馆 14 行（9Wae1-6×武术/道术学员、9Wtt×武术/道术教头）。魔改判定依据（上一轮核验）：MapInfo `[B011 赌场]` 图名、古惑仔港片命名、筹码经济、破关珍剑不在 StdItems.csv 且脚本零动作（死内容）、282/283 脚本带我本沉默私服头、176 Gee 包无此三类内容、Delphi 引擎无筹码逻辑。

**合并（3 组，删 4 个空货单脚本）**：屠夫@0 三份→`1Gme`（1Gme/1Tme 功能全同仅排版差，1Bme 只收肉无货单）；屠夫@3 两份→`1Ame`（有干肉+买卖）；铁匠铺老板@0 两份→`2Twe`（有武器六件+买卖修理）。**段级差异（自查基线对照，用户已知悉待确认）**：1Bme-0 丢 Kcom_1/Kcom_2/kxwpz 段、得货物 2；1Tme-0 零差异；1Mme-3 丢Q300/Q301/Q302/Q304/Q313/main-1、得干肉+Q303/Q350/buy；2Bbl-0 丢 talk/kundo/next* 等15 段、得武器 6+buy/sell。**特殊修理**：两脚本均无 s_repair 段（原 2Bbl 的<特殊修理/@s_repair> 按钮走 merchant 兜底开 UI，handlers_npc.py:69 _repair_allowed），合并后按钮入口消失但兜底能力仍在；比奇室内武器店 `2Are-铁匠铺老板-0151` 有真s_repair 段，特殊修理服务不缺失。老人@1（9Wqu/9w）均无商品，不适用合并规则，各自保留。

**重命名（159 文件，零代码改动）**：`<编码>-<地图>.txt` → `<编码>-<名字>-<地图>.txt`，MerChant 第一列 `<编码>` → `<编码>-<名字>`（如 `1Gme-屠夫 0 313 271 屠夫 …`），命中 load_script 主候选（npc_script.py:241 / Java NpcData.java:86 同约定）。迁移后保留行 (编码,图号)→名字 唯一，无命名歧义。Market_Def 283→201 文件，MerChant 有效行 248→163。

**测试修正**：test_pyserver.py 18 处固化基名/路径（2Bwe-0103→2Bwe-卫家店-0103 等16 个 stem，含 :499 杂货铺 stem 列表）。

**自查**：迁移前快照全部 248 行货物/段/能力签名（data/_merchant_baseline.json），迁移后逐行重解析对照——保留行签名==原签名、合并行签名==幸存者签名、无 MISSING，**全部通过**（data/migrate_merchant_names.py + _migration_report.txt，均 gitignored）。回归：test_pyserver **122/0**、test_tick_systems **180/0**、test_all_messages **95/0**（D:/Python310/python.exe，与 §96 基线一致）。

**未动 / 待用户决策**：① 5 个 Npc_Def 传送员脚本（传送员-0/-2/-3/-11、静寂传送员-28）的 `<比奇赌场/@dcmove>` 按钮仍在，指向已无 NPC 的赌场图（超「清理 NPC」范围，未改）；② 43 个孤儿脚本未动（含 24 个赌场残留 9Ega*-T2xx/T3xx）；③ 筹码/筹码包物品、B011/B105/T105 等赌场图及 MapInfo 条目保留；④ MerChant 注释行仍用旧编码。

**文档**：GAMEDATA_GUIDE.md §1/§3/§4/§5.4 同步新命名约定与计数。

**提交**：**未提交**（用户明确要求确认后提交）。改动面：gamedata/Mir200/Envir/MerChant.txt、Market_Def/（159 改名+82 删）、scripts/test_pyserver.py、docs/GAMEDATA_GUIDE.md、本文。

## §99 新增 mir2-resource-converter skill：WIL/WIX 格式规范 + 转换工具 wil_pack.py（2026-11-17）

**需求**：整理当前游戏资源格式（WIX/WIL 等，重点是精灵组织形式），形成 skill 让AI Agent 能把外部资源快速转换成可直接使用的格式，并生成可验证的预览。

**产物**：
- `.claude/skills/mir2-resource-converter/`——**自包含可分发**的 skill：`SKILL.md`（工作流/工具用法/验证清单）+ `references/format-spec.md`（WIL/WIX 二进制规范、精灵帧号契约、特殊精灵逻辑全文，实测核验）+ `references/palette.json`/`tables.json`（调色板/动作表快照）+ `references/upstream/`（wil.mjs/palette.mjs/actorData.mjs 逐字快照，离线可查权威实现）+ `scripts/wil_pack.py`（纯标准库，`info/dump/pack/preview`，内嵌 WIL 解码器与 PNG 编解码，无外部 import）。`--tables <actorData.mjs>` 可实时解析动作表保持新鲜；`--merge` 把新帧字节级保真地拼进现有库；打包后走同语义解码路径读回逐帧比对自校验；`preview` 产出自包含 HTML 动画预览（动作 × 8 方向、48×32 格锚点虚线可视化）。项目相关引用收敛到 SKILL.md 末节「在 jootm2 仓库内使用」。
- 同轮用户指令：① skill 改为独立可分发，不再引用仓库文件（上述快照即为打包产物）；② HANDOFF 与 SKILL 文档改**正常换行**（一段一行）——全库查证（用户全局规则/项目 CLAUDE.md/本文档维护规则/各 skill）**不存在行宽规定**，逐 40 字断行是早期 Agent 书写习惯（本文档最初版本即正常换行，§0/§0.5 也一直是正常换行），已全文还原为段落单行（rewrap 脚本去空白后逐字符校验一致，仅代码块/表格/引用行/列表项保留结构），并在「文档维护规则」新增第 7 条固化。

**格式核验结论**（对照 `frontend/gfx/wil.mjs`、原版 `MirClient/Actor.pas:2089`）：
- WIL 头 1080B = 44 标题 + u16 帧数@44 + u16 colorcount@46(=0) + u32 256@48 + u32 1024@52 + 256×4 内嵌调色板@56（字节序 B,G,R,0）；**前端不读内嵌调色板**，统一用 `palette.mjs` 硬编码表（index 0 = 透明，其余 alpha=255）。
- 位深无标志位，按 `下一帧偏移−本帧偏移 ≥ 8+w×h×2` 判 16 位 RGB565（黑=透明），故帧间不得有填充；行序自底向上，4 字节行对齐由字节数反推（写时不补最稳）。
- `px/py` = 绘制左上角相对实体格**左上角**的偏移（原版与前端都是相加），原版美术数据不统一（Hum 帧0：60×71 px=8 py=-48），新美术缺省「水平居中、脚踩格底」。
- 空图 = WIX 偏移 <1080 或 h==1 占位帧。
- 全部 58 个原版库首图偏移恒 1080、v1.0 头，实测一致。

**验证**：工具链实测——Hum dump→pack 回环像素全等；合成 MA11 怪物（232 帧，appr205→mon21 偏移 1800）打包/校验/预览通过，agent-browser 截图确认动画与锚点渲染正确；merge 进现有库后旧帧字节保真（1800 帧原样）。回归 test_pyserver **122/0**（D:/Python310/python.exe）。预览页样例：`data/wil_pack_test/*.preview.html`（gitignored，可随时用 preview 命令重新生成）。

## §100 阿里云 gamedata 热同步：商人改名迁移上线（2026-11-17）

**背景**：§98 商人清理+重命名（`6c9eeb4`）是纯数据改动。gamedata 虽 COPY 进镜像，但 compose 的 `./gamedata:/app/gamedata` 挂载会整体压住镜像内数据（§21.8 坑）——只推镜像不生效，必须 rsync 宿主目录 + 重启。用户指令：同步阿里云，涉及改名，必要时远端 rename 后全新上传。

**执行**：先 `--dry-run -i` 审查删除清单——241 个删除全部是 Market_Def 旧名（159 改名旧名 + 82 被删脚本），159 新文件 + MerChant.txt 更新，Map/CSV/Notice 零变更，无意外删除项；再执行 §21.8 标准命令 `rsync -az --delete --exclude 'Mir200/Map' --exclude '.gbk-backup' gamedata/ root@aliyun.notmmao.com:/root/jootm2/gamedata/`（`--delete` 天然处理改名：删旧名+传新名，无需手工远端 rename）→ `docker compose restart jootm2`。junction 告警按 §21.8 无害（已排除、未传输 204MB 地图）；`data/`（账号库）绝不同步。

**验证**：容器内 Market_Def **201** 文件、新名（`1Ame-屠夫-3.txt` 等）、旧 `1Bme-0.txt` 已清除、MerChant **250** 行且含 `1Gme-屠夫 0 313 271`；启动日志 `242 drop tables, 207 npcs, castle=沙巴克/3, quests=10/6`（207 = 163 商人 + 44 Npcs.txt）；公网 IP `/api/status` **200**（48ms）。

**版本说明**：`/api/version` 仍为 `3ad46df`（2026-11-16 镜像）——本轮是数据热同步，未重建镜像（零代码改动，无需）。用户的 `1d71c72` 前端光照/缩放及后续提交不在镜像内，如需生效跑 `bash scripts/deploy_image.sh`（镜像更新后 version 指向最新 hash；gamedata 挂载仍压镜像数据，但宿主目录已是最新，不冲突）。

**提交**：本节 [文档] 提交；数据改动本体在 `6c9eeb4`。

## §101 wiki 站迁到 list2（Vercel CDN）+ /site 302 跳转（2026-11-17）

**背景**：用户证实 /site 图片在公网仍被重新下载（本地验证缓存头正确、浏览器二次访问 100% 命中，但阿里云带宽有限）。指令：把 wiki 部署到 list2（Vercel）并把 /site 跳转过去；先发布拿地址再跳转。

**第一步 · 发布到 list2**：
- 产物 `data/website` 72MB/1967 文件，超 list2 发布脚本 60MB 上限。用 Pillow **无损**压缩生成 `data/website-list2/`（54MB）：RGBA 且颜色数 ≤256 的图（289 张地图）转 P 调色板——先按 `sorted(set(RGB))` 造精确调色板（**不能用 ADAPTIVE 量化，会改色**），逐像素校验 RGB 差异为 0 才写盘。文件数不变、无空文件。
- 发布踩坑：wiki 含中文文件名，`git diff --cached --name-only` 默认输出八进制转义（`"mon/\344\270\255..."`），发布脚本前缀校验误判越界并回滚。在 list2 仓库执行 `git config core.quotepath false`（只改本地 git 配置，不动受控文件）后解决。
- 线上 `https://list.notmmao.com/mir2-wiki/`，commit `ed0c779`，1967 文件全在 `mir2-wiki/`，其他同级目录零改动；`--verify-only` 复查 200。

**第二步 · /site 跳转**：
- `pyserver/config.py` 新增 `website.redirect`（默认 `https://list.notmmao.com/mir2-wiki/`；properties / `JOOTM2_WEBSITE_REDIRECT` / `--website-redirect`；设空关闭），`website_redirect_enabled` 属性 + describe 输出；`__main__.py` 加 CLI 参数。
- `pyserver/net.py`：`/site` 前缀请求在跳转开启时返回 **302**（非 301，方便观察期回退），Location 保留子路径 + query（中文路径 quote 编码），`Cache-Control: no-cache`；STATUS_TEXT 补 302 Found。跳转关闭时回退原本地 `_serve_file`（缓存分档逻辑保留）。
- 阿里云**无需再 rsync wiki**，只需更新镜像（默认值已内置代码）；回退 = 设空 `website.redirect` 重启。

**验证**：`test_pyserver.py` 128/0（新增 8 项：裸路径/子路径/中文编码/query 透传/HEAD 无 body/关闭回退等）；本地 18081 实测 `/site`→302 list2、`/site/maps.html`→302 对应路径、中文路径正确 percent-encode；tick 180/0、all_messages 95/0。`docs/WIKI-PLAN.md` 已同步。

**未提交的外来改动**：`.claude/skills/mir2-resource-converter/`（wil_pack.py / format-spec.md，时间戳 18:24，非本轮），提交时已排除。

## §102 玛法领路人 5000 金币可重复领：CHECK 语义 + 任务变量不落库（2026-11-17，用户反馈两问）

**用户反馈（两条）**：① 问「对比原版，怪攻击力 < 我防御是不是永远打不掉血？」；② 「玛法领路人给的一次性奖励可以重复领取（3 级后 5000 金币）」。本条记②与①的结论。

**问题① 结论：是的，符合原版。** 火炬 `M2Server/ObjBase.pas:3431` `GetHitStruckDamage`：
`armor := LoByte(AC)+Random(HiByte(AC)-LoByte(AC)+1); damage := _MAX(0, damage-armor)`。
`ObjAxeMon.pas:67-72` 里保底 1 点的代码 `//if dam<=0 then if dam>-10 then dam:=1` **被注释掉**，原版物理攻击确实没有保底伤害——攻击掷点 ≤ 防御掷点就一点不掉（也不播受击/飘字）。本项目 `world.py:2543 _ranged_damage` = `roll_attack()-ac`，`dmg<=0` 走 noDamage 分支（`world.py:2177`），已对齐；魔法另走 `GetMagStruckDamage`（扣 MAC）同理。注意 PvP/玩家打怪仍用 `max(1,…)`（见已知边界），此处是「怪打玩家/召唤物」路径。

**问题② 根因（两个 bug 叠加，均自 Java 上游继承）**：
1. **CHECK 语义错**：原版 `QI_CHECK`（ObjNpc.pas:670-679）是「零/非零匹配」——`n := GetQuestMark(param)` 只返回 0/1，`(quest==0)==(tag==0)` 才通过；**不是**数值 `>=`。Java `NpcScriptEngine.java:192` 和本项目 `npc_script.py:431` 都写成 `quest >= tag`，于是 `check [001] 0` **恒真**。玛法领路人 `@away`（「未领过则补领」）因此永远 `goto @next1` → 无限 `give 5000`。全局 214 处 `check [N] 0/1`（24 个 0、190 个 1）全是布尔标记用法；tag=1 时 `>=1` 与「非零」等价，行为不变，只 tag=0 被修。
2. **任务变量不落库**：`Player.quest_params` 只在内存——`service.py` 读档构造 Player 不加载、`persist()` 不写、`db.py` characters 表无该列；Java 侧 `Player.java:54 questParams=new int[1000]` 同样从不存档。即使修好①，重登后 `[001]` 归零仍可再领。

**改动**：
- `npc_script.py::_eval_condition` check 分支 → `(quest==0)==(tag==0)`（原版零/非零匹配）；注释写明依据与旧 bug。
- `db.py` 新增独立表 `character_quests(character_id PK, params TEXT, FK→characters ON DELETE CASCADE)` + `load_quest_params` / `save_quest_params`（`ON CONFLICT DO UPDATE`）。**独立表不动 characters 结构**——该表与 Java 共用，绝不往里加列。
- `service.py` 读档构造 Player 后 `p.quest_params = db.load_quest_params(rec["id"])`；`persist()` 末尾 `db.save_quest_params(p.character_id, p.quest_params)`（空 dict 也写，避免读到缺省）。

**验证**：新增 `scripts/test_npc_mafa_guide.py`（18/0）：CHECK 四种组合 + 值 2、2 级不发钱、3 级首领 +5000 且 SET [001]、@main/@away 重进不再发、存档→重登标记保留、新角色不串号。`test_pyserver` 128/0、`test_tick_systems` 180/0、`test_autopilot` 39/0、py_compile 通过。`test_npc_home_quest.py` 报「找不到高级道士 NPC 9Eht」——**git stash 还原源码后同样失败，是基线问题，与本修复无关**。

**注意**：① 需**重启服务端**才生效（引擎/DB 改动）；浏览器 Ctrl+F5。② 老存档第一次启动会自动建 `character_quests` 表，存量角色 `[001]` 视为未领——若有玩家已领过但库里无标记，会再领一次（一次性，之后正常）。③ 原版 `@next1` 只判 `checklevel 3`、未二次校验 `[001]`，UI 正常路径进不去（@main 已分流到 @away），按保真不擅自加校验。

**提交**：见本轮 git log（标签按 CLAUDE.md：跨脚本语义+数据持久化，拆为 `[逻辑]` 引擎语义 + `[数据]` 持久化）。

## §103 交易 + 组队功能落地：服务端协议补齐 + 前端 UI（2026-11-17，用户要求）

**用户要求**：「现在支持组队么？交易、组队这些功能都要加上。」

**调研结论**：服务端原有骨架——`world_extra.py` 有 Party/TradeSession、`handlers_social.py` 有 6 个交易 + 3 个组队 handler、经验共享/同队拾取已实现；但①交易缺开窗下发/状态同步/移除物品/距离校验，②组队是「邀请即入队」无确认、无队员状态推送，③**前端完全没有 UI**（只有「交易功能未开放」「编组功能未开放」两个占位按钮）。

**服务端改动**（原版依据火炬 ObjBase.pas:13918 ServerGetDealTry → :14241 SM_DEALSUCCESS；EnterGroup/LeaveGroup :6073-6084；HAM_GROUP Grobal2.pas:580）：
- `handlers_social.py`：交易新增 `tradeReject/tradeRemoveItem/tradeSetGold`，`_push_trade` 在每步发 `tradeStart`（开窗）+ `tradeState`（双方物品/金币/确认，B 视角左右对调）+ `tradeEnd`（success/cancel）；加同图/距离≤2 校验、背包容量校验、取消退还。组队改为邀请须 `partyAccept` 确认（新增 `partyReject/partyInfo/partySys`），`partyInfo` 含成员 hp/maxHp/level/map/online。
- `world_extra.py`：新增 `party_invites` 字典、`party_snapshot_msg`/`broadcast_party_info`，`tick_extra` 每 500ms 重发队伍血量（队员血条实时）。
- `service.py`：新增消息分派；`on_disconnect` 退队 + 取消交易退还，避免对方窗口卡死/物品凭空消失。
- `handlers_combat.py`：`_is_teammate` 在近战/刺杀/魔法三处 PvP 入口过滤同队目标（原版 HAM_GROUP 语义，本服无攻击模式切换 UI 用「同队互助」兜底）。

**前端改动**：新增 `frontend/ui/TradePartyUI.mjs`（独立模块，挂 GUI 容器 800×600 随 stage 缩放）：
- 交易：发起输入名字框、收到请求弹确认、双方窗口（self 可点格子取回物品/设金币/锁定确认，other 只读）、tradeState 增量刷新、tradeEnd 关窗。物品图走 items/looks，名称按原版 tooltip（title 属性）。
- 组队：编组按钮开面板（队员名/等级/血条/离线标记）、邀请输入、退队、队伍聊天（回车）、收到邀请弹确认。
- `frontend/scene/PlayScene.mjs`：import + `_buildHud` 里实例化 + 两个占位按钮接线 + `onMessage` 顶部转发 + `onExit` 销毁。

**协议（前端↔服务端）**：上行新消息 `partyAccept/partyReject/partyInfo`、`tradeReject/tradeRemoveItem/tradeSetGold`；下发 `partyInfo/partySys/partyInvite`、`tradeInvite/tradeStart/tradeState/tradeEnd`。

**验证**：新增 `scripts/test_trade_party.py` 33/0（邀请确认/拒绝、开窗状态对调、setGold 可下调、取回物品、取消退还、成交互换、距离校验、不伤队友）+ `scripts/test_trade_party_ui.py` 17/17（Playwright 3.9 无头，合成消息驱动 UI：面板/血条/聊天/邀请确认/交易窗口/关窗，零 JS 异常）。`test_all_messages` 95/0（更新组队断言为邀请+确认两步）、`test_pyserver` 128/0、`test_tick_systems` 180/0、`test_npc_mafa_guide` 18/0。

**注意**：① 需重启服务端 + Ctrl+F5 生效。② 交易栏 14 格（原版 DDGrid 上限）。③ `tradeSetGold` 是设置交易栏金币**总额**（原版 CM_DEALCHGGOLD 语义），可下调取回；旧 `tradeAddGold` 累加语义保留兼容。④ 原版交易/组队窗口视觉用 DOM 近似（项目一贯做法），坐标非原版 DFM 像素级复刻。⑤ 行会/攻城仍未做前端 UI（本轮不在范围）。

**提交**：见本轮 git log（按 CLAUDE.md 标签：服务端逻辑 `[逻辑]`、协议保真 `[保真]`、前端 `[前端]`、测试 `[测试]`、文档 `[文档]`，分提交）。

## §104 多人功能：聊天气泡 + 密语 + 在线列表 + 查看他人装备（2026-11-17，用户四问）

**用户要求**：「原版聊天又气泡框么？如何密语？是否考虑加入一个在线列表（显示在线人物、等级和所在地图）？如何查看别人装备？这些多人功能你都来排查和实现。」

**排查结论（火炬 Delphi 权威）**：
- **聊天气泡：原版有，但不是贴图气泡框**——是纯文字白字黑描边（`BoldTextOut(..., clWhite, clBlack, ...)`），最多 5 行、行距 14px、单行宽 150px、显示 `4*1000=4` 秒（`Client/Actor.pas:20,2365-2420` + `Client/DrawScrn.pas:331-352`）。由 `SM_HEAR=40` 同时写聊天板 + `actor.Say()`（`ClMain.pas:4588-4594`）。本项目此前只写聊天板，气泡未做。
- **密语：原版有**——统一走 `CM_SAY=3030`，输入格式 `/玩家名 内容`，服务端 `Say()`→`Whisper()`→`SM_WHISPER=103`，接收方看到 `名字=> 内容`（`ObjBase.pas:11680-11691,10670-10692`；`Grobal2.pas:668,890`）。`@` 是 GM 命令通道，不列姓名。本项目此前无。
- **在线列表：原版无此功能**——客户端窗口（FState.pas:51-265）无在线/Rank 窗口，全源检索在线名单 0 命中；GM 命令 `who/total/Human`（ObjBase.pas:11151/11680/11685）只报在线人数不列姓名。**属本项目新增**，呈现复用行会/队伍列表风格。
- **查看装备：原版有**——Ctrl+右键点玩家（`ClMain.pas:2476-2489`）→ `CM_QUERYUSERSTATE=82`（Grobal2.pas:896）→ 服务端 `CretInNearXY` 近距校验 → `SM_SENDUSERSTATE=751`（Grobal2.pas:795）回 `TUserStateInfo`（名字/颜色/外观 + `UseItems[0..12]` 13 槽）→ 独立 `DUserState1` 窗（FState.pas:1722）。本项目此前全链路缺失。

**改动**：
- 服务端 `service.py::_say`：`/` 开头解析为密语 → 新增 `_whisper`（按名字定向发双方 `whisper` 消息，接收方 `incoming=true`，发送方回显 `incoming=false`；目标离线给系统提示；`@` 仍走 GM 通道）。不进世界频道。
- 服务端 `handlers_social.py`：新增 `SOCIAL_EXTRA_TYPES={onlineList,queryUserState}`（并入 SOCIAL_TYPES）+ 两个 handler：`_online_list`（回全体在线玩家 名字/等级/职业/地图，按等级降序，不暴露坐标/血量）；`_query_user_state`（近距校验同图+`distance_to<=SIGHT_RANGE`、活着、目标存在，回 `userState` 含身份 + `items_json` 里带 `slot` 的装备项）。`world.py` 无需改（Player.brief_json / items_json 字段齐全）。
- 前端气泡：`case 'chat'` 把 text 写进说话者实体 `_sayText/_sayUntil=now+4000`；snap 构建注入 `say` 字段；`MapActor._drawHeadHud` 在名字上方画白字黑边（死亡灰字，最多 5 行/行距 14/宽 150，4 秒自动消失）。自己说话也画（原版 SendRefMsg 含自己）。
- 前端密语：`case 'whisper'` 聊天板写 `[密语] 名字: 内容`，紫色 `#a020d0`（调色板前景 252 近似），不弹气泡。
- 前端在线/查看装备：`TradePartyUI` 加 `toggleOnlineList()`（HUD「在线玩家」按钮 x=339/icon142）、`_renderOnlineList`、`_showUserState`（13 槽坐标表 + stateitem 图标 + tooltip）、handleMessage 加 `onlineList/userState` case、destroy 清理。`PlayScene` 加 Ctrl+右键玩家 → `queryUserState`（原版 ssRight+ssCtrl，在右键「跑」分支前拦截）。
- HUD 底部原「属性加点」隐藏按钮 x 从 339 挪到 369，避免与新增「在线玩家」重叠。

**验证**：新增 `scripts/test_multiplayer_features.py` 30/0（气泡 id 字段、密语双方定向/离线/空内容/@、在线列表字段与排序、查看装备近距/13槽/远距/死亡/不存在）+ `scripts/test_multiplayer_ui.py` 15/15（Playwright 3.9：气泡数据注入、密语着色、在线窗渲染、装备窗 tooltip、Ctrl+右键发消息且普通右键不触发、零 JS 异常）。回归 `test_all_messages` 95/0、`test_pyserver` 128/0、`test_tick_systems` 180/0、`test_trade_party` 33/0、`test_npc_mafa_guide` 18/0。

**注意**：① 需重启服务端 + Ctrl+F5 生效。② 在线列表是本项目新增（非原版），只给名字/等级/职业/地图，无坐标/血量（隐私与带宽）。③ 气泡是纯文字白字黑边（原版无贴图气泡框），4 秒；服务端 `chat` 仍是全体广播（`broadcast_all`），但气泡只在本地有该实体（视野内）时才画。④ 密语用 `/名字 内容`（原版格式），前端按 `/` 唤出输入框天然对齐；无跨服、无屏蔽名单（原版 `@拒绝私聊` 未做）。⑤ 职业编码 0=战士/1=法师/2=道士（曾误写成 1 基数组，测试抓到已修）。⑥ 查看装备近距用 `SIGHT_RANGE`，与原版 `CretInNearXY` 近似。

**提交**：见本轮 git log（按 CLAUDE.md 标签：服务端协议 `[保真]`、前端 `[前端]`、修复职业映射 `[修复]` 并入前端提交、测试 `[测试]`、文档 `[文档]`）。
