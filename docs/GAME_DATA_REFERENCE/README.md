# 游戏数据参考资料索引

本目录收集**可用于游戏性调优参考**的外部资料：**一篇来源一个文件**，便于后续持续沉淀同类材料。

每篇文档统一结构：

1. **来源**（出处、口径、采集方式、可信度前提）
2. **重点摘要**（几句话讲清这份资料最有用的结论）
3. **细节**（原始数据表、机制说明、未测项）
4. **可信度与验证**（哪些是逐帧核对、哪些是语音转写、误识别修正）
5. **调优参考提示**（如何用于本项目，以及不能怎么用）

> ⚠️ **使用边界**：这里收录的多为私服 / 特定引擎的实测数据，**不是 Delphi 原版权威依据**。可用于定位差异、生成调优假设；改动项目数值前仍须按 `.workbuddy/skills/jootm2-original-debug/` 的流程找原版依据，找不到就先不要硬改。

## 已收录

| 来源 | 主题 | 文件 | 数据口径 | 采集日期 |
|---|---|---|---|---|
| B 站 BV1Dxxs6QE3e（丨Leng少丨） | 战士 8 个技能 0~3 级伤害与机制 | [BILIBILI-BV1Dxxs6QE3e-warrior-skills.md](./BILIBILI-BV1Dxxs6QE3e-warrior-skills.md) | 996 引擎，官方表单，技能数据零改动 | 2026-11-16 |
| B 站 BV1YFxv6nEsC（丨Leng少丨） | 道士主要技能：治疗/施毒/火符/噬血/防魔 | [BILIBILI-BV1YFxv6nEsC-taoist-skills.md](./BILIBILI-BV1YFxv6nEsC-taoist-skills.md) | 996 三端引擎；绿毒机制与官服不同 | 2026-11-16 |
| B 站 BV1xE4F65Efa（丨Leng少丨） | 法师主要技能：雷电/疾光/地狱雷光/火墙/灭天火/流星火雨/冰咆哮/魔法盾 | [BILIBILI-BV1xE4F65Efa-mage-skills.md](./BILIBILI-BV1xE4F65Efa-mage-skills.md) | 同系列推断 996；只测常用技能 | 2026-11-16 |

## 命名约定

- B 站：`BILIBILI-<BV号>-<英文主题>.md`，例如 `BILIBILI-BV1Dxxs6QE3e-warrior-skills.md`。
- 其他站点：`<SITE>-<视频ID>-<英文主题>.md`；文档资料：`<SOURCE>-<主题>.md`。
- 文件内首节固定为 `## 来源`，末节固定为 `## 调优参考提示`，方便检索工具统一解析。

## 采集方法

视频类材料使用用户级 skill `video-reference-extractor`；数据库对比类使用 `mir2-db-compare`。
新资料入库后，记得回到本文件登记索引。
