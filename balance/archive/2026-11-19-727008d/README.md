# 归档：2026-11-19 首次全量运行

- 代码版本：`727008d`（`balance/` 初版）
- 命令：`python balance/run.py report --compare 176`
- 数据：主树 `gamedata`（指纹 `6f203ec92e45`），对比树 `data/gamedata/176`（指纹 `18204feccf6d`）；`game_rates.json` 全 1.0，周末按平日
- `index.html`：报告（双击打开）；`snapshot.json`：报告内嵌的全部指标
- `cache/`：引擎 A（主树 / 176 各 225 组合 × 8 场）与引擎 B（主树 19 场）原始结果
- 已知缺口：引擎 B「南驽马石窟三层 Lv31 N=40」一场未跑（运行环境单次 3 分钟上限）
- 对应结论：`../../GM-ANALYSIS.md`

同一份缓存放回 `balance/out/cache/` 且代码与数据指纹不变时，`run.py report` 可直接复用、不重跑模拟。
