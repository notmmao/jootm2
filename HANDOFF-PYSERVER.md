# jootm2 Python 服务端分支交接文档

> **最后更新**：2026-11-16（§88 一键镜像部署脚本；文档瘦身：§69~§82 归档到 `docs/handoff/archive-2026-11-f.md`）
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
| `docs/handoff/archive-2026-11-f.md` | §69~§82：第二轮数据清理 / 技能修炼点 / 骷髅战士掉落 / 新手线掉落整表恢复与商店分档 / Magic.csv 全列对齐 / 高级之家测验与托管六问题 / 尸体渲染 / 大地图入口标注与过滤（原文两节重号 §79，逐字保留）/ 误伤宝宝豁免 / 治愈自家宝宝 / 召唤熟练度 / 半兽古墓刷怪 176 / 背包满快捷栏自动填充 / 护身符耗尽核对 / 道士托管治愈+火符 |

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

## §85 修复油被当祝福油 + NPC 修理没有投放框（2026-11-16，用户反馈）

**反馈**：①「修复油怎么用？我看到是幸运+1，体现在哪？有什么用？」②「NPC 的修复 UI 有问题，没有地方放装备」。

**根因（本地 Delphi 源码逐条核对）**：
- `StdItems.csv:178` 修复油 = `StdMode=3 Shape=9`，`135` 祝福油 = `Shape=4`，
  `133` 战神油 = `Shape=10`，`Shape=12` 是临时增益药水。原版
  `ObjBase.pas:17359-17438 ClientUseItems` → `EatItems` → `EatUseItems(Shape)`
  （:23436、:23513-23591）按 Shape 精确分流：4=`WeaptonMakeLuck`（:23621）、
  9=`RepairWeapon`（:23673）、10=`SuperRepairWeapon`（:23692）。旧 `_use_item`
  只把 1/2/3/5 当卷轴，**其余一律走 `_use_bless_oil`** → 修复油/战神油/增益
  药水全被拿去掷幸运，这就是用户看到「幸运+1」的原因。
- 幸运在界面上「看不到」：`instance_json` 不下发 `lucky`，前端 `_itemDesc`
  没有幸运行。原版 `FState.pas:4238/4346` 在物品说明里显示「幸运+N」。
- NPC 修理链路整体缺失：`npc_script/handlers_npc` 只认 buy/sell/storage，点
  `<修理/@repair>` 只回一句文本。原版 `TMerchant.UserSelect`
  （ObjNpc.pas:1419-1465）对 `@repair/@s_repair` 发 `RM_SENDUSERREPAIR` /
  `RM_SENDUSERSREPAIR` → 客户端 `ClientGetSendUserRepair`
  （ClMain.pas:6235-6241）置 `SpotDlgMode=dmRepair` + `ShowShopSellDlg`
  （复用卖物投放圈），再由 `CM_MERCHANTQUERYREPAIRCOST`/`SM_SENDREPAIRCOST`
  与 `CM_USERREPAIRITEM`/`SM_USERREPAIRITEM_OK|FAIL` 完成问价和扣钱修复。

**修复**：
- `pyserver/handlers_items.py`：StdMode=3 按 Shape 分流；新增 `_repair_weapon`
  ——修复油双击修**当前佩戴的武器**（`Dec(DuraMax,(DuraMax-Dura) div 30)` +
  单次最多 +5000），战神油直接补满且不掉上限，只有真修成才消耗油；Shape=12
  明确提示未开放且不消耗。
- `pyserver/world.py instance_json` 下发 `lucky`（模板 `specialPwr` + 实例
  `lucky`，等于 `Player.recompute` 的 `p.luck`）；`frontend/scene/PlayScene.mjs`
  的 `_itemDesc` 显示「幸运+N」。
- `pyserver/npc_script.py` 解析脚本头 `(@buy @sell @repair @s_repair)` 能力
  声明并新增 `Result.repair_mode`；`handlers_npc.py` 新增
  `queryRepairCost`/`repairItem`（原版价 `Round(nPrice div 3 / DuraMax *
  (DuraMax-Dura))`、特修 ×3、普通修理掉上限再补满、特修补满不掉上限、
  满持久/StdMode=43 拒修、只认背包物品）；`service.py` 注册消息。
- 前端 `shop:'repair'/'s_repair'` 复用 DSellDlg 投放框（`shopRepairMode`）：
  拿起物品点圈问价、OK 发 `repairItem`、`repairResult` 清圈、`sellPrice`
  显示「修理费: N 金币」。

**玩法口径（回答用户）**：修复油双击直接修手上的武器，不用 NPC、不用放装备；
NPC 修理必须先把**装备卸下放进背包**再拖进圈（原版 `ClientRepairItem` 只在
`m_ItemList` 找物品，ObjBase.pas:24653-24681）。普通修理/修复油会掉最大持久，
特殊修理/战神油不掉。幸运 N = 有 N/10 概率打出攻击上限（`roll_dc_with_luck`），
只影响物理攻击。

**验证**：新增 `scripts/test_repair.py` 39/0、`scripts/test_repair_ui.py`
（Playwright 3.9 连 3.10 起的临时服务）14/0；回归 `test_pyserver` 107/0、
`test_all_messages` 91/0、`test_tick_systems` 180/0、`test_durability` 16/0；
`node --check` / `py_compile` / `git diff --check` 通过。面板贴图与位置观感
仍需用户 Ctrl+F5 实机确认。

---

## §86 消耗品与 NPC 全量对照审计（2026-11-16，用户「检查其他NPC, 消耗品与原版不同的地方」）

**方法**：本地 Delphi 源码逐条核对 `ClientUseItems/EatItems/EatUseItems/
WeaptonMakeLuck/GetAttackPower/RecalcAbilitys/UserSelect/QuestCheckItem`，
对照 `gamedata/Mir200/Envir` 283 个 Market_Def + Npc_Def + QuestDiary 脚本
与 StdItems.csv 消耗品全表。

**已修复差异**（全部有原版行号依据）：
1. **祝福油概率模型重写**（`handlers_items._use_bless_oil` ← ObjBase.pas:23621-23671）：
   旧「50%/30%/20%」是自造。原版：`Random(20)=1` 诅咒；幸运<1 必成、<3 按
   `Random(nRand+6)=1`、<7 按 `Random(nRand*40)=1`（nRand=|DC2-DC|div5，
   武器越钝越难加）；**油恒消耗**（失败也白花），只有没装武器不扣。
2. **幸运口径统一**（`world.recompute` ← ItmUnit.pas:125/569-570 +
   ObjBase.pas:3401-3402）：`p.luck = Σ模板AC低 + 实例油 − Σ模板MAC低诅咒`，
   ±10 钳制（旧 `specialPwr 1..10` 是 Java 时代近似，1.80 数据该列全是
   327xx 高位垃圾，作废）。`instance_json.lucky` 同步改 AC 低+油口径；
   诅咒时前端 tooltip 显示「诅咒+N」（FState.pas:4238-4240）。
   `roll_dc_with_luck` 概率改回 `Random(10-min(9,luck))=0`（幸运1=1/9 而非
   旧 1/10），并补诅咒负幸运取下限分支（旧实现完全忽略）。
3. **Shape=12 增益药水实装**（EatItems :23383-23437 → 新增 `_use_status_potion`）：
   6 槽 `status_arr/status_until`（DC/MC/SC 上限+2+值、攻速、MaxHP/MaxMP），
   同槽覆盖、时长 MacMax 秒；`recompute` 消费、`_expire_buffs` 到期清零
   （对齐 Run :4194-4201）。数据在店卖/怪掉（攻击力/魔法力/道术力/疾风/
   HP/MP强化水全梯度），此前点了只会「暂未开放」。
4. **StdMode=0 空效果药水照样消耗**（:23349-23368 else 分支无条件 True；
   旧实现 AC/MAC 全 0 拒绝使用）；神水（Shape=2）置 `user_unlock_durg`
   照原版消耗（本项目无装备绑定，标志无消费方）。
5. **NODRUG 地图禁药守卫**（EatItems 顶 :23330-23334）：MapInfo 标志
   `NODRUG` 命中即拒绝一切 StdMode 0-3（当前数据集 0 张图带此标志，守卫为保真）。
6. **传送卷四 shape 对齐**（`world_extra.use_scroll` ← EatUseItems :23518-23574）：
   - 1 地牢逃脱卷=home 图**随机**落点（旧实现是 home 固定坐标）；
   - 2 随机传送卷查 `NORANDOMMOVE` 图标志（幸运房 B104 等直接无效）；
   - 3 回城卷 `pk_point>=200`（PKLevel>=2 红名）改去红名回家点 3/845/674
     （M2Share.pas:1693-1695），不再永远回 home；
   - 5 行会回城卷改去**城堡回城点** 3/644/290 ±4 随机
     （ObjBase.pas:23556-23568 + Castle.pas:921-928，旧实现错误地回个人 home），
     且非城主行会成员无效（物品不消耗）。

**审计确认「不是 bug / 暂不动」**：
- StdMode=4 书：`ReadBook` 只认名字查 Magic.csv，Shape无关；数据无
  AniCount>0 的书 → 原版 `+LNG/+WID` 攻杀/半月自动开启联动走不到，无缺口。
- StdMode=31 卷包：19 件 AniCount 全 0 → 全部是解包路径，
  `UseStdmodeFunItem(@StdModeFunc<AniCount>)` 分支数据不会触发（FunctionNPC
  本项目不存在，若未来加带 AniCount 的 31 类物品需另行立项）。
- NPC `@makedrug/@prices/@upgradenow/@getbackupgnow` 原版 UserSelect
  （ObjNpc.pas:1536-1575）各有独立分发；本项目 5 个 makedrug 店、2 个
  升级店点链接会走「标签不存在→回主对话」兜底，不报错但功能缺失——
  制毒/黑铁矿升级依赖独立子系统（HANDOFF 已列为独立工程），维持现状。
- 脚本条件动词缺口 `checkdura/checkduraeva/checkluckypoint/checkunit/
  dayofweek/hour/daytime/istakeitem`（合计 24 文件引用）：原引擎对未知
  条件**默认放行**（`_eval_condition` 兜底 return True），与原版「未知
  =0 恒假」方向相反；但涉及面（骰子房 p 变量、时辰任务）多为沉默版
  魔改内容（adddice/movr/sum 等原版 GOM 也只有 mov 一半实现），
  按「找不到权威依据不硬改」原则本轮不动，登记备查。
- 彩票（Shape=11）原版已注释取消（ObjBase.pas:23584-23588），提示
  「无法使用」正确。

**验证**：`test_repair.py` 扩到 46/0（新增祝福油三分支/诅咒/油恒消耗、
幸运口径、状态药水生效与到期、HP 强化水抬 max_hp）；`test_tick_systems`
幸运断言按新口径改（2acMin+3油-1macMin=4）；全量回归
tick 180/0、all_messages 91/0、pyserver 107/0、durability 16/0；
`test_repair_ui.py` Playwright 14/0；py_compile/node --check 通过。
药水生效提示与 tooltip 观感仍需用户 Ctrl+F5 实测。

## §87 /site 数据库站缓存分档：图片 60 秒 → 7 天（2026-11-16）

**问题**：用户反馈阿里云站每次打开地图页图片都重新加载。排查确认缓存头
**不缺**：`pyserver/net.py` 的 `/site` 路由一直有 ETag / Last-Modified /
304 条件请求，但统一用 `cache_override="public, max-age=60, must-revalidate"`
覆盖所有文件——包括 893 张地图/图标 PNG。超过 60 秒后每张图都要发一次
条件请求（公网逐张一个 RTT），带宽有限时观感就是「重新加载」。旧注释说明
60 秒是为规避「文件名不变 + immutable 一年看不到新图」，方向对但取值过保守。

**改动**（`pyserver/net.py`）：
- 新增 `WEBSITE_STATIC_MAX_AGE = 7*24*3600`、`WEBSITE_IMAGE_EXTS` 与
  `website_cache_control(name)`：HTML `no-cache`；图片 `public, max-age=604800,
  must-revalidate`；JS/CSS/JSON 及未知类型 `max-age=300, must-revalidate`。
- 不用 `immutable`：站点重生成时文件名不变，7 天到期后靠 ETag 自动换新图。
- `_serve_file` 新增可选 `cache_fn: Callable[[str], str]` 参数，优先级
  `cache_fn > cache_override > cache_control`；其余调用点零改动。
- `/site` 路由改传 `cache_fn=website_cache_control`；仍 `allow_gzip=False`
  （站点同步 zlib 压到游戏 tick 的约束不变，见 §60）。
- `docs/WIKI-PLAN.md` 同步路由/缓存分档与验证命令。

**验证**：`test_pyserver.py` 扩到 **114 PASS / 0 FAIL**，新增 9 项覆盖
分档表、图片不用 immutable、`/site/*.png` 长缓存 + ETag、条件请求 304、
HTML `no-cache`。本地起 `python -m pyserver --port 18080` 实测响应头：
PNG `public, max-age=604800, must-revalidate`、HTML `no-cache`、CSS
`max-age=300`，带 `If-None-Match` 返回 304 且无 body。

**部署提醒**：改的是 Python 代码，阿里云需**更新镜像并重启**才生效（只
rsync `data/website/` 不够）。验证：`curl -sI https://aliyun.notmmao.com/site/
assets/icons/items/1.png | grep -i cache-control`。浏览器第一次刷新仍会
重取一次图片（旧 60 秒缓存里的条目过期/覆盖），此后 7 天内零图片请求；
若改完想立刻看新图，Ctrl+F5 强刷即可。

---

## §88 一键镜像部署脚本 + bed804c 只推镜像部署（2026-11-16）

**背景**：用户要求「推送到阿里云，只推送镜像，其他资源文件不变」。Agent 侧
Bash 权限分类器（auto 模式依赖的远端模型）间歇性超时，构建命令连续被挡；
用户提出改为一键脚本由本人在终端运行，绕开分类器。

**新增 `scripts/deploy_image.sh`**（bash，`bash -n` 通过）：
- 流程 = §21.6 标准路径的脚本化：`gen_version.py`（优先 `D:/Python310/python.exe`）
  → `docker build` 短 hash + `latest` 双标签 → push ACR（失败提示
  `docker login --username=notmmao@gmail.com`）→ ssh 阿里云
  `pull → docker tag jootm2:python → cd /root/jootm2 && docker compose up -d`
  → 轮询验证 `/api/version` version == `git rev-parse --short HEAD`（3 秒 × 10 次）
  + `/api/status` 200。
- **只推镜像，无任何 rsync**：不碰服务器 `data/`、`resources/`、`gamedata/`、
  `data/website/`。⚠ 因此 gamedata 改动不会随本脚本生效（§21.8 挂载覆盖坑），
  改了 gamedata 需另行 rsync；工作区不干净时脚本开头打印警告。

**本轮部署结果（bed804c，用户亲自运行脚本）**：
- ACR 双标签 `bed804c` + `latest`，digest `sha256:8f5ba39a77c9…`；
  阿里云容器 Recreated → Started。
- 验证：`http://139.224.24.88:8080/api/version` 返回 `"version":"bed804c"`
  （builtAt 2026-11-16T14:56:31），`/api/status` **200**。按约定不做深度回归。
- 本次镜像包含 §85~§87 的代码改动（修复油分流 / 消耗品审计 / /site 图片长缓存）；
  gamedata 未同步，若宿主 gamedata 落后于仓库，模板 CSV 直载（§84）读到的仍是
  宿主旧数据。

**文档瘦身**：主文件超 500 行硬上限（927 行），§69~§82 逐字归档到
`docs/handoff/archive-2026-11-f.md`，主文件保留 §83~§88。§89 追加前再把
§83~§84 归档到同文件，主文件保留 §85~§89。

---

## §89 托管优化：法师远程 / 背包限流清肉 / 武器与符自动补戴（2026-11-16）

**用户诉求**：① 背包容易满（尤其挖肉后），打一会就满包回城；② 战士靠被动没问题，
道士已优化过远程，法师还贴身砍怪；③ 武器坏了、护身符没了要自动换背包里的下一个。

**方案（用户拍板：挖肉限流+智能清理 / 法师按等级选最优技能 / 武器+护身符补戴）**，
全部改在 `pyserver/autopilot.py`：

1. **背包**：新增 `BUTCH_BAG_RESERVE=8`——背包剩 ≤8 格就停挖肉，改打怪捡金币
   （钱不占格）；`_clean_meat()` 在吃紧时按肉的耐久（=m_nMeatQuality，卖价正比）
   从差到好逐份丢，留 `MEAT_KEEP=6` 份最值钱的，2s 一份走 `_drop` handler 不刷屏；
   进入 `cleaning_meat` 模式后持续丢到达标。**不再满包即回城停机**——只有真塞满
   （全是非肉物品）才退回原行为用回城卷回城。顺带修 `_want_pickup`：StdMode 40
   （肉）和 1（干肉）不再主动捡回，否则清包丢的肉立刻被捡回来白清。
2. **法师远程**：新增 `MAGE_ATTACK_SKILLS=(11,5,1)`（雷电术>大火球>火球术，
   都吃 MC、单目标 RM_DELAYMAGIC、不吃符）与 `_mage_fire()`：施法半径 8 格内
   （Magic.pas:266）按「已学+有蓝+冷却好」选最高优先级招；**没蓝/没学绝不贴身砍**
   （法师物理输出极低），只等回蓝；超射程照常走近。自查 MP/冷却避免
   「魔法值不足」刷屏，`_cast_magic` 仍做最终校验。
3. **装备自动补戴**：`_ensure_equipment()`（1s 节流）在武器槽空时从背包挑
   StdMode 5/6 中耐久最高的一把走 `takeOnItem` 补上；`_ensure_amulet()` 补
   护身符（Shape=5）/毒粉（Shape≤2）到 U_BUJUK/U_ARMRINGL 空槽（**只补空槽，
   否则毒粉会把护身符顶回背包**）。`_taoist_fire` 施法前也即时补符，避免刚耗完
   一张符就回落近战。`_best_wearable` 只做候选筛选，等级/重量/槽位校验仍由
   `_take_on_slot._can_take_on` 统一把关，不绕过规则。

**新增字段**（AutoPilot dataclass）：`last_clean_at/cleaning_meat/last_equip_at`。
**回归**（全用 D:/Python310/python.exe）：新增 `scripts/test_autopilot_hosted_opt.py`
14/0（法师技能优先级+没蓝不近战、武器换装、护身符毒粉双补、挖肉限流与清肉
品质排序）；既有 test_autopilot 39/0、test_autopilot_taoist 18/0、test_autopilot_ws
16/0、test_hosted_fixes 14/0、test_all_messages 91/0 全绿。
