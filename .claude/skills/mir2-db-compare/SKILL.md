---
name: mir2-db-compare
description: 对比第三方传奇 Mir2 私服数据库（SQL dump / SQLite .db / Paradox .DB）与本项目 gamedata/mud2/DB 的 CSV，产出 docs/DB_COMPARE_*.md 报告。当用户提供另一套私服 DB 要求对比、评估能否借用内容、找数值差异/同源关系时使用。已完成的先例：applem2（Paradox）、GEEM2（SQL dump）。
---

# Mir2 第三方 DB 对比

## 目标

把用户给的第三方私服 DB 与本项目 `gamedata/mud2/DB/*.csv`（Monster/StdItems/Magic 三张表）做逐表对比，产出可留档的报告，回答三个问题：**对方是什么血统、哪些数值被改过、有什么可以借用**。

## 硬性约束

1. **材料落位**：导出物与中间结果放 `data/db_compare/<tag>/`（`data/` 已 gitignore，**不入库**）；报告放 `docs/DB_COMPARE_<TAG>.md`（入库）；一次性深挖脚本放 `data/db_compare/` 里，不进 `scripts/`。
2. **解释器用 `D:/Python310/python.exe -I`**（零第三方依赖的脚本不挑版本，但保持项目约定）。
3. **不动本项目 `gamedata/mud2/DB/*.csv`**——它是对比基准。
4. Windows 控制台与重定向默认 **GBK**：跑输出中文的脚本一律加 `PYTHONIOENCODING=utf-8`；重定向文件乱码时按 GBK 读回再转 UTF-8。

## 流程

### 第 1 步：识别对方格式并导出成 CSV

| 对方格式 | 工具 |
|---|---|
| SQL dump（SQLite 方言：`CREATE TABLE` + `INSERT INTO "T" VALUES …`） | `scripts/mir2_db_sql_export.py <dump.sql> -o data/db_compare/<tag>`（内存 sqlite3 灌入导出，UTF-8 读取） |
| Paradox `.DB`（原版 M2 格式） | `scripts/mir2_db_export.py <对方DB目录> -o data/db_compare/<tag>`（需 `pip install pypxlib`，表头取 DB 自身字段名） |
| 已是 CSV | 直接放入 `data/db_compare/<tag>/`，确认 UTF-8 编码 |

注意：同名 `.db` 不一定含全部表（GEEM2.db 只有 StdItems），以内容最全的文件为准。

### 第 2 步：跑通用对比

```bash
PYTHONIOENCODING=utf-8 D:/Python310/python.exe -I scripts/compare_mir2_db.py \
    --ours gamedata/mud2/DB --theirs data/db_compare/<tag> --label <TAG> \
    --out data/db_compare/<tag>_raw.md
```

字段名对不上时扩展 `compare_mir2_db.py` 的 `FIELD_ALIAS`（本项目 `Level` vs 对方 `Lvl`、`WalkSpeed` vs `WALK_SPD` 这类）。

### 第 3 步：深挖（写一次性脚本，输出落 data/db_compare/）

按先例值得算的指标：

- **同源判定**：Level/HP 一致率（两轮先例都是 HP ~65% 一致 → 同源）；共有技能 EffectType/Spell/Power/MaxPower/Job/Delay 是否零差异。
- **Exp 倍数分布**：`ours/theirs` 的 median 与区间直方图（用 `fractions.Fraction` 精确分桶，经验值看是否整倍数被批量调过）。
- **StdItems 拆分字段对照**：本项目是语义化列（`AcMin/AcMax/…`），对方多半是泛用槽（`Ac/Ac2/Dc/Dc2`），逐对 (Min,Max) vs (Lo,Hi) 比；schema **不能逐列对拷**。
- **种族/AI 语义字段**：race 重映射（对方可能拿 81 当占位）、CoolEye 可能被布尔化——逐值列出差异明细。
- **对方扩展列非零占用率**：很多引擎扩展列是空壳，全 0 就可以在报告里标注「可忽略」。

### 第 4 步：写报告 docs/DB_COMPARE_<TAG>.md

结构参照 `docs/DB_COMPARE_GEEM2.md` / `docs/DB_COMPARE_APPLEM2.md`：

1. 材料与工具（对方文件、格式、落位）
2. 对方血统判定（世代特征 + 同源指标）
3. 逐表对比（规模表 → 同源指标 → 被改过的地方，样例 ≤5 条）
4. 名称集合差异摘要（按类别归类，别整段罗列）
5. 结论：三句话——血统、改动重点、借内容时的坑
6. 复现命令块

## 报告里的数值语义参照

- 装备槽/StdMode 语义查 `pyserver/util.py` 的 `slot_for()`（5/6=武器、10/11=衣服、15=头盔、19/20/21=项链、22/23=戒指、24/26=手镯、40=肉、43=矿）。
- 与 applem2/GEEM2 两轮结论交叉引用时，只引用对方报告里**实际写了**的数字，不要替旧报告推算新口径（如 median）。
