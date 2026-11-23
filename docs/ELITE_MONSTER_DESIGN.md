# 精英怪设计说明

> 2026-11-23 落地（HANDOFF-PYSERVER §143 第 4 项）。**原版传奇（火炬源码）没有精英怪机制**，
> 这是用户主动要求新增的玩法，数值全部可配置，随时可以关掉回到纯原版。

## 1. 玩法规则

- 天然刷怪（MonGen 首刷/重生、NPC 脚本刷怪、GM @刷怪）时每只怪独立掷骰，
  命中即成为精英。
- 精英怪强化：**HP ×2、攻击(DC) ×1.5、经验 ×3**。
- 外观：名字前缀「精英·」+ 绿色（`#5ef08a`），两种名字风格（原版/当前改良）下都生效；
  精英绿色优先于 PK 关系色和普通色。
- 掉落不加倍（保持原版掉落表），只强化血量、伤害和经验——刻意做小，避免破坏经济。

## 2. 配置（`config/server.properties`，不写则用默认）

| 键 | 默认 | 说明 |
|---|---|---|
| `elite.rate` | `0.05` | 成为精英的概率，0 = 完全关闭该机制 |
| `elite.hpMult` | `2.0` | HP 倍率 |
| `elite.dcMult` | `1.5` | 攻击倍率 |
| `elite.expMult` | `3.0` | 经验倍率 |

读取链路：`config.py` DEFAULTS → dataclass 字段 `elite_rate / elite_hp_mult /
elite_dc_mult / elite_exp_mult` → `world.py::_spawn_monster`。

## 3. 实现要点

- **掷骰点**：`pyserver/world.py::_spawn_monster()`。`hp<=0` 或 `exp<=0` 的摆设怪
  （装饰类 NPC 怪）跳过掷骰，不产生"精英稻草桩"这种无意义实体。
- **HP**：直接改实例 `max_hp/hp`，模板不受污染。
- **DC**：不新增字段，复用 `slave_dc_bonus`（`roll_attack` 本来就会把它加进伤害），
  精英加成 `bonus = round(max(2, dc) × (dcMult − 1))`，攻击链路零改动。
- **经验**：直接把实例 `m.exp` 乘 `expMult`，`_grant_exp` 与复活僵尸的 ÷2 逻辑
  自然保持相对倍率。
- **下发**：`Monster.brief_json` 带 `elite` 布尔字段 → 前端
  `PlayScene` 快照透传 → `MapActor._drawHeadHud` 加前缀和绿色。
- **宝宝排除**：`_spawn_monster` 预留 `allow_elite=False` 参数给召唤兽等非野生路径
  （当前 `make_slave` 走独立构造，本就不会命中）。

## 4. 已知边界

- 精英标记**不落库**：服务端重启后重生怪重新掷骰，同一只怪不保证仍是精英。
- 精英不影响怪物 AI 与刷新节奏，只是数值/外观变体。
- Python `round(2.5)=2`（银行家舍入），DC 加成计算测试按此断言。

## 5. 验证

- `scripts/test_elite_monster.py` 18/18（概率、倍率、跳过条件、brief_json、DC 复用）。
- E2E（`scripts/e2e_session_20261123.py`）：临时配置 `elite.rate=1.0` 下 GM @刷怪
  稻草人×5 全部精英，前端绿色「精英·稻草人」+ 血条数字截图确认。
- 注意：GM `@刷怪` 已改为走 `_spawn_monster` 统一路径，精英掷骰对 GM 刷怪同样生效。

## 6. 后续可扩展方向（未实现）

- 精英专属掉落表（如必爆药/金币加成）——需先定经济影响。
- 精英名字颜色/前缀做成职业或地图差异化。
- 高级地图提高 `elite.rate`（按地图分档配置）。
