# Monster.DB「Race」字段参考（原版分发表 × 本项目实作状态）

> 原版依据：`M2Server/UsrEngn.pas:1831 TUserEngine.AddBaseObject` 的 `case nMonRace of`，
> 常量定义 `M2Server/M2Share.pas:143-171`；各类行为在 `ObjMon.pas / ObjMon2.pas / ObjAxeMon.pas`。
> 对照：本项目 `pyserver/util.py`（GUARD_RACES/RANGED_RACES）、`world.py/_monster_ai`、`world_extra.py`（ANIMAL_RACES 等）。
> 生成于第 21 轮（2026-11-13），race 96 复活僵尸实作后。

## 0. race 字段其实有**三处**含义（同名的坑）

| 位置 | 字段/参数 | 作用 |
|---|---|---|
| 服务端 | `Monster.DB 的 Race`（本项目 `Monster.csv` Race 列） | **类分发**：AddBaseObject 按 race 决定 new 哪个 TMonster 子类，决定 AI 行为 |
| 服务端 | `Monster.DB 的 RaceImg`（`m_btRaceImg`） | 走协议 feature 低位，客户端重建 actor 用 |
| 客户端 | `Actor.pas:848 GetRaceByPM(Race)` | 按 race 选**动作帧表**（MA9/MA10/…：stand/walk/attack 各段的 start/frame/ftime） |
| 客户端 | `PlayScn.pas:2046 RACEfeature(cfeature)` | 按 race 选 **TActor 子类**（0 玩家 / 9 足球 / 13 食人花 / 14 骷髅 / 15 掷斧骷髅…，影响绘制偏移与取帧方式） |

本项目前端对应：动作表 = `frontend/actor/actorData.mjs`；绘制 = `ActorSprite.mjs`（`MON_SEG` 段映射）。
注意：`Race` 与 `RaceImg` 通常相等但不必然；本项目 Monster 建立时 `race=t.race, race_img=t.raceImg` 分开传。

## 1. 服务端类分发总表（UsrEngn.pas:1831-1938）

标记：✅ 已实作 ｜ 🟡 近似实作（有行为差异）｜ ❌ 未实作 ｜ ➖ 本项目 CSV 无该 race 的怪，暂无影响。
「CSV」= `gamedata/mud2/DB/Monster.csv`（本项目唯一怪物数据源）。

| Race | 原版类 | 原版行为（依据） | 状态 | 备注 |
|---|---|---|---|---|
| 11 | TSuperGuard 超级卫士（大刀） | **主动攻击视野(7)内一切怪（race≥80）＋玩家召唤物＋PKLevel≥2 红名**（ObjGuard.pas:87-126，m_boAttackPet=True） | ✅ | TNormNpc 系；站桩不追击（Run 无 Walk）；CSV「卫士/卫士1」 |
| 12 | TPetSuperGuard 宠物卫士 | 同上但 `m_boAttackPet=False`：有主召唤物先打它才反击（ObjGuard.pas:130-136） | ✅ | ➖ CSV 无 race 12 怪 |
| 20 | TArcherPolice | 弓箭警察（远程守卫） | ✅ | GUARD+RANGED；➖ CSV 无 |
| 51 | TMonster＋boAnimal 鸡 | MeatQuality R(3500)+3000、Leathery 50（UsrEngn:1835） | ✅ | 挖肉/肉品质；CSV race 51 挂的是「卫士」 |
| 52 | 鹿：1/30 TChickenDeer，否则 TMonster | 见人就跑不攻击（ObjMon:531）；Leathery 150 | ✅ | chicken_deer 变种已实作（1/30、MeatQuality 10000+R(20000)） |
| 53 | TATMonster＋boAnimal 狼 | 主动攻击动物，Leathery 150 | ✅ | |
| 55 | TTrainer 练功师 | 练功木桩反馈 | ❌ | CSV 有 1 只「练功师」 |
| 80 | TMonster（被动基础怪） | 无主动索敌 | ✅ | handlers_combat:157：51/52/80 不 SearchTarget；CSV 无 80（稻草人在 83） |
| 81 | TATMonster 主动怪 | 主力近战 AI | ✅ | CSV 128 只（多钩猫/钉耙猫/尸王…） |
| 82 | TSpitSpider 吐毒蜘蛛 | 远程＋m_boUsePoison（ObjMon:660） | ✅ | RANGED_RACES＋poison(82/119) |
| 83 | TSlowATMonster 慢速主动怪 | 走速慢（ObjMon:634） | ✅ | 效果由 CSV WalkSpeed 体现，等价 |
| 84 | TScorpion 蝎子 | boAnimal（ObjMon:650） | ✅ | 可挖肉 |
| 85 | TStickMonster 食人花 | 埋地出土/潜回（ObjMon2:154-297） | ✅ | hidden＋digUp/digDown（第 20 轮前的旧实作） |
| 86 | TATMonster | 骷髅系主动怪 | ✅ | |
| 87 | TDualAxeMonster 掷斧骷髅 | 远程飞斧 2 连投（ObjAxeMon:65-112） | ✅ | flyAxe 特效；连投次数未还原 |
| 88 / 89 | TATMonster | 驽马系主动怪 | ✅ | |
| 90 | TGasAttackMonster 洞蛆 | boAnimal＋近身毒气（ObjMon:861） | 🟡 | 挖肉 ✅；近身攻击的毒效果未还原 |
| 91 | TMagCowMonster 火焰沃玛 | 魔法攻击（ObjMon:952） | 🟡 | 按普通近战近似 |
| 92 | TCowKingMonster 沃玛教主/统领 | Boss 魔法 AI（ObjMon:1023-1057） | 🟡 | 按普通近战近似 |
| 93 | TThornDarkMonster 暗黑战士 | 远程连投 3 次（ObjAxeMon:184，继承掷斧） | ✅ | RANGED_RACES；连投次数未还原 |
| 94 | TLightingZombi 闪电僵尸 | **直线穿透闪电** MagPassThroughMagic 9 格（ObjMon:1131-1189） | 🟡 | 近似普通远程；直线穿透伤害未还原；不埋地（与 95 区分） |
| 95 | TDigOutZombi 埋地僵尸 | m_boFixedHideMode，3×3 目标才 ComeOut（ObjMon:1193-1260） | ✅ | 第 20 轮：SM_DIGUP＋尘土＋叫声 |
| 96 | TZilKinZombi 复活僵尸 | 死亡原地复活 1~3 次，MaxHP/经验减半（ObjMon:1263-1313） | ✅ | 第 21 轮实作，见 §4 |
| 97 | TCowMonster 沃玛战士系 | 主动怪＋1/2 bo2BA（UsrEngn:1894-1898） | ✅ | bo2BA（不走非安全格）未还原，影响极小 |
| 100 | TWhiteSkeleton 变异骷髅 | 埋地，首次 Run 广播 RM_DIGUP 钻出（ObjMon:1317-1346） | ❌ | CSV 有「变异骷髅」（也是召唤兽模板「神兽10」） |
| 101 | TScultureMonster 祖玛雕像 | **石化模式** STATE_STONE_MODE，2 格内活敌 MeltStoneAll 群体活化＋RM_DIGUP（ObjMon:1357-1442） | ❌ | CSV 有 8 只雕像/卫士 |
| 102 | TScultureKingMonster 祖玛教主 | 特殊 Boss AI（ObjMon:1446-） | ❌ | |
| 103 | TBeeQueen 角蝇 | 产小蜂（ObjMon2:370-） | ❌ | |
| 104 | TArcherMonster 祖玛弓箭手 | 远程连投 6 次（ObjAxeMon:198） | ✅ | RANGED_RACES；连投次数未还原 |
| 105 | TGasMothMonster 楔蛾 | 毒气远程（ObjMon:1569） | 🟡 | 近似近战 |
| 106 | TGasDungMonster 粪虫 | 毒气（ObjMon:1612） | 🟡 | 近似近战 |
| 107 | TCentipedeKingMonster 触龙神 | 特殊（ObjMon2:442-） | ❌ | |
| 110 | TCastleDoor 沙巴克城门 | 攻城器械 | ❌ | 攻城玩法未做 |
| 111 | TWallStructure 沙巴克城墙 | 攻城器械 | ❌ | 同上 |
| 112 | TArcherGuard 弓箭守卫 | 远程守卫（ObjMon2:889-） | ✅ | GUARD＋RANGED |
| 113 | TElfMonster 神兽（近战形态） | 召唤兽变形 | ✅ | 变形/远程切换已实作（world.py:1658） |
| 114 | TElfWarriorMonster 神兽1（远程形态） | 同上 | ✅ | |
| 115 | TBigHeartMonster 赤月恶魔/千年树妖 | 特殊 Boss（ObjMon2:588-） | ❌ | |
| 116 | TSpiderHouseMonster 幻影蜘蛛 | 产爆裂蜘蛛（ObjMon2:648-） | ❌ | |
| 117 | TExplosionSpider 爆裂蜘蛛 | 自爆（ObjMon2:729-） | ❌ | |
| 118 | THighRiskSpider 天狼蜘蛛 | 主动怪（ObjMon:749） | ✅ | 近似主动近战 |
| 119 | TBigPoisionSpider 花吻蜘蛛 | m_boUsePoison 毒（ObjMon:846） | ✅ | poison(82/119) |
| 120 | TSoccerBall 飞火流星 | 足球：直线滚动反弹（ObjMon2:303-340） | ❌ | |
| 130-134 | TDoubleCritical / TRon / TSandMob / TMagicMon / TBoneKing | 第三方引擎扩展怪 | ➖ | CSV 无这些 race，未实作无影响 |
| 200-215 | TElectronicScolpion / TClone / TTele / TKhazard / TGreen / TRed / TFrostTiger / TFire / TFireBall | 私服扩展（ObjMon/ObjMon2 尾部） | ➖ | 同上 |

## 2. 机制层实作状态（横切，不按单只怪）

| 机制 | 原版依据 | 本项目 |
|---|---|---|
| 挖肉 boAnimal（51/52/53/82/84/85/90） | ObjBase.pas:17458 ClientGetButchItem | ✅ ANIMAL_RACES（world_extra.py:50） |
| 挖空变骷髅 race∈[50,80) | ObjBase.pas:17485 | ✅ SKELETON_RACE_MIN/MAX |
| 肉品质 m_nMeatQuality | UsrEngn:1835-1864 出生初始化 | ✅ init_meat_quality（鸡/鹿/狼按原版区间） |
| Leathery 磨损 50/150 | UsrEngn:1840/1849/1863 | ✅ ANIMAL_LEATHERY（第 17 轮按 race 修正） |
| 守卫 AI GUARD_RACES {11,12,20,112} | 守卫不追远、不拉仇恨 | ✅ |
| 大刀守卫主动打怪/召唤物/红名 | TSuperGuard.Run（ObjGuard.pas:87-126） | ✅（第 22 轮；守卫目标 pk_point≥200 = PKLevel≥2） |
| 杀怪经验等级惩罚 | CalcGetExp（ObjBase.pas:2250）：≥怪+10 级每超 1 级扣 1/15，保底 1 | ✅（第 22 轮；`boHighLevelKillMonFixExp` 默认 False=惩罚生效） |
| 组队经验 ×bonus[n] 按等级比例分 | GainExp（ObjBase.pas:15557-15603，2 人 1.3…11 人 2.2） | ✅（第 22 轮；12 格内同图存活成员） |
| 远程 RANGED_RACES {82,87,93,94,104,112,20} | 各远程类 | ✅（攻击距离/拉开距离/追击半径） |
| 用毒 {82,119} | m_boUsePoison | ✅ |
| 鹿 1/30 变种 TChickenDeer | UsrEngn:1844 | ✅ |
| 埋地/出土 85/95 | m_boFixedHideMode | ✅（85 双向潜回；95 出土不复钻） |
| **复活 96** | TZilKinZombi | ✅（第 21 轮） |
| 神兽变形 113/114 | TElfMonster/T ElfWarrior | ✅ |
| 石化活化 101 | TScultureMonster.MeltStone | ❌ |
| 空图休眠/5 分钟清怪 | （本项目性能优化，非原版行为） | ✅（第 20 轮） |

## 3. 本项目 Monster.csv 实际分布（273 只）

race: 数量 → 怪名（截取）：
11:2 卫士 ｜ 51:1（卫士——**原版该槽位是鸡**，卫士走挖肉/动物分支无实害但不符原版常量）
52:6 羊/护卫/鹿×4 ｜ 53:2 狼 ｜ 55:1 练功师 ｜ 81:128 ｜ 82:7 ｜ 83:10 ｜ 84:1 蝎子 ｜ 85:1 食人花
86:2 骷髅 ｜ 87:6 掷斧骷髅/半兽统领… ｜ 88:4 驽马战士/骷髅战士 ｜ 89:8 驽马系 ｜ 90:2 洞蛆
91:3 火焰沃玛 ｜ 92:10 沃玛教主/虹魔教主/统领 ｜ 93:7 暗黑战士/蟾怪 ｜ 94:2 僵尸1 ｜ 95:3 僵尸2/石墓尸王
96:6 僵尸3/30/4/40/5/50 ｜ 97:14 沃玛战士系 ｜ 100:2 变异骷髅/神兽10 ｜ 101:8 祖玛雕像/卫士
102:2 祖玛教主 ｜ 103:1 角蝇 ｜ 104:6 祖玛弓箭手/鹰卫 ｜ 105:2 楔蛾/月魔蜘蛛 ｜ 106:2 粪虫
107:1 触龙神 ｜ 110:1 沙巴克城门 ｜ 111:3 城墙 ｜ 112:4 弓箭手/护卫 ｜ 113:1 神兽 ｜ 114:1 神兽1
115:3 赤月恶魔/千年树妖 ｜ 116:1 幻影蜘蛛 ｜ 117:1 爆裂蜘蛛 ｜ 118:4 天狼蜘蛛/工蚁 ｜ 119:3 花吻蜘蛛 ｜ 120:1 飞火流星

## 4. race 96 复活僵尸（TZilKinZombi，第 21 轮实作）

原版（ObjMon.pas:1263-1313）：

```pascal
constructor TZilKinZombi.Create;
  nZilKillCount := 0;
  if Random(3) = 0 then nZilKillCount := Random(3) + 1;   // 1/3 概率可复活 1~3 次

procedure TZilKinZombi.Die;
  if nZilKillCount > 0 then begin
    dw558 := GetTickCount();
    dw560 := (Random(20) + 4) * 1000;                      // 死后 4~23 秒复活
  end;
  Dec(nZilKillCount);                                      // 无条件递减，-1 彻底死亡

procedure TZilKinZombi.Run;   // 复活条件（全部满足才触发）
  m_boDeath and not m_boGhost and nZilKillCount >= 0
  and m_wStatusTimeArr[POISON_STONE] = 0                   // 未被石化
  and m_VisibleActors.Count > 0                            // 附近有观察者
  and (GetTickCount - dw558) >= dw560
→ MaxHP := MaxHP shr 1; FightExp := FightExp div 2; HP := MaxHP; ReAlive();
  （ReAlive = 清死亡态 + 广播 RM_ALIVE(10153→SM_ALIVE=27)，ObjBase.pas:21199）
```

客户端：`SM_ALIVE` → 清 `m_boDeath/m_boSkeleton`（Actor.pas:1462），动作 = ActDeath 段按
方向正向重播一遍（AxeMon.pas:424，同出土动画）＋ 出场音 `m_nAppearSound`（Actor.pas:2401）。

本项目对应实作：

- `pyserver/world.py`：`Monster.zil_revives / zil_revive_at`；`_init_race_traits`（出生 1/3 概率
  1~3 次，spawn/respawn 共用）；`_tick_zilkin_revive`（tick 主循环挂接）。
- `pyserver/world_extra.py kill_monster`：race 96 死亡时按原版安排 `zil_revive_at` 并无条件
  递减；`respawn_at` 顺延到复活窗口之后——复活成功则取消重生（原版是同对象原地复活，
  不走 MonGen 补刷），复活不发生（附近无人）则重生照常补怪，数量守恒。
- 前端：`PlayScene.mjs case 'alive'`（复位 corpse/alive 状态＋'alive' 一次性动作）；
  `ActorSprite.mjs` `MON_SEG.alive='death'`（同 digup 段）；`SoundManager.mjs` alive→appear 音。
- 近似点：POISON_STONE 石化状态未实作（恒通过）；`m_VisibleActors.Count>0` 近似为
  SIGHT_RANGE 内有活玩家。

回归：`scripts/test_all_messages.py` 63 PASS / 0 FAIL（新增 12 条 race 96 断言）。
