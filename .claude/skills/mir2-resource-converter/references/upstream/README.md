# 上游脚本快照（snapshot 2026-11-17，jootm2 @ 28c1477 后）

本目录是三个权威源文件的**逐字快照**，让本 skill 离开仓库也可查阅解码语义的第一手实现：

| 文件 | 权威内容 |
|---|---|
| `wil.mjs` | WIL/WIX 前端解码器：位深判定（字节数反推）、行序自底向上、行对齐反推、空图规则（偏移<1080 / h==1）、Range 加载与纹理缓存 |
| `palette.mjs` | 256 色 RGBA 调色板唯一数据源（index 0 = 透明）——`references/palette.json` 即由此提取 |
| `actorData.mjs` | 帧号契约唯一数据源：HA/MA 动作表、HUMANFRAME/MERCHANTFRAME、`getMonImgLib`/`getMonOffset`/`getNpcOffset`、WORDER 武器层级、NPC 占位外观表——`references/tables.json` 即由此提取 |

快照与提炼物的关系：`wil_pack.py` 运行时只消费 `palette.json` / `tables.json`（结构化、已测）；
本目录供人/Agent 逐字核对与边界确认。**在本 jootm2 仓库内使用时，仓库里的同名源文件是
最上位基准**——发现不一致，以仓库为准并同步更新本快照与两份提炼物（可用
`wil_pack.py --tables frontend/actor/actorData.mjs` 实时解析验证）。
