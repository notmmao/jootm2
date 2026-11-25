"""离线修炼（Offline AFK）代理：docs/OFFLINE-AFK-PLAN.md 的阶段 0+1 实现。

架构要点：

* **复用在线托管内核**：决策循环直接继承 ``AutoPilot``（决策层与 session 无关，
  所有动作经 ``srv._xxx(p.session, ...)`` 调用）。离线代理把 ``p.session`` 换成
  ``NullSession``（send_* 全部吞掉），整条 handler 链路原样可用——广播照常发给
  其他在线玩家，只是没人收。
* **生命周期**：在线配置 → 下线瞬间 ``promote()`` 接管（角色留在世界继续打怪）→
  登录 ``takeover()`` 结算交还 → ``tick()`` 按分钟记账、时长耗尽/连死熔断停机结算。
* **落库**（不碰 Java 共用的 characters 表结构）：
  - ``character_afk``：时长余额 + 每日限领记账（玛法领路人令牌）；
  - ``afk_sessions``：运行中会话，每 60s 刷新 remaining_minutes，
    服务端重启按它**自动续挂**并补偿 ``afk.restartBonusMinutes``；
  - ``afk_reports``：结算报告，玩家下次登录下发。
* **资源保护**（§3.3 全部走 server.properties）：等级门槛 / 地图黑名单 /
  每图并发 / 全服上限 / 每日时长 / 经验系数（world_extra.add_exp 认
  ``afk_offline`` 旗标）/ 拾取视野减半（OfflinePilot.adjust_cfg）。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .afk_log import AfkSessionLog
from .autopilot import (DEFAULT_PRESET, PRESETS, AutoPilot, _find_town_scroll,
                       npc_really_repairs,
                        attack_interval_ms, normalize_preset)
from .util import RED_NAME_PK_POINT, SLOT_NAMES, U_DRESS, U_WEAPON
from .world import BAG_SIZE, Monster, Player, now_ms


def today() -> str:
    return time.strftime("%Y-%m-%d")


# ---- 补给闭环（docs/OFFLINE-AFK-PLAN.md §3.4 v2）------------------------
# 状态机：IDLE（空串）→ GOTO_TOWN → SELL → BUY → GOTO_ANCHOR → IDLE。
# 全程走既有 handler 与走/跑节拍（540/360ms），不加速不绕校验。
SUPPLY_GOTO_TOWN = "goto_town"
SUPPLY_SELL = "sell"
SUPPLY_BUY = "buy"
SUPPLY_GOTO_ANCHOR = "goto_anchor"
SUPPLY_CHECK_MS = 10_000        # 触发判定节流（不每 tick 重算）
SUPPLY_ACT_MS = 600             # 卖/买动作节奏（对齐客户端操作间隔）
SUPPLY_LEG_RETRY = 4            # 同一段门连续寻路受阻次数上限
SUPPLY_RETRY_MS = 60_000        # 一次补给失败后的重试冷却（按失败次数翻倍）
SUPPLY_LONG_RETRY_MS = 30 * 60_000  # 连续失败达上限后的长冷却（不直接停挂）
SUPPLY_FAIL_RESET_MS = 15 * 60_000  # 稳定升级一段时间后清零失败计数
# 补给完成后的冷却：本地商人在3格内时，卖完买完回头1格就到锚点，
# 若背包仍 ≥触发线会每 10 秒重新补给一次，实测 20 分钟内 BEGIN/FINISH
# 循环上百次、补给占时 90%。冷却期照常打怪（满包靠 bagFullStop=0 继续
# 打怪不拾取），给背包消耗（喝药）留出时间；濒死紧急补给走 _supply_begin
# 直连，不受此冷却影响。
SUPPLY_FINISH_COOLDOWN_MS = 3 * 60_000
SUPPLY_STATE_TIMEOUT_MS = 15 * 60_000  # 单状态超时：1000×800 大图一路跑到门点
                                       # 就要 4~6 分钟，8 分钟会把「赶路中」误判成超时
SUPPLY_SELL_MAX_PRICE = 100     # 「杂物」卖价阈值：低于它的装备顺手清掉
# 卖货时只找这么近的商人：1000×800 大图上某些杂物只有几十格外的 NPC 收，
# 为几十金币跑 200 格纯属浪费挂机时间（实测补给占时 74%）。太远就不卖，
# 满包靠 bagFullStop=0 继续打怪不拾取，经验照赚。
SUPPLY_SELL_MAX_DIST = 40
# 「专门进城补给」时的距离预算：整图级。
# 与上面那 40 格语义完全不同——40 格是「就地随手卖，别为小钱绕远」；
# 而我们是**专程**从挂机图跨图跑到补给城的，比奇省落点 (42,109) 到最近的
# 商人聚落 (300,250) 就有 260 格。若这里还按 40 格判「没人收」，后果是
# 全背包物品一次性进 unsellable 黑名单 + `_supply_repair_npc` 返回 None，
# 于是背包永远 46/46 满、身上装备永远脱不下来、武器一路磨到 0 也不修
# （2026-11-24 实测：40 分钟挂机 0 次修理，武器 2200→1169）。
# 进城后走几百格是必须付的成本，不是浪费。
SUPPLY_TOWN_MAX_DIST = 1200
# 跨图补给的门段数上限。超过就不专程去补给了（继续打怪等背包满/药尽）：
# 祖玛系列（D505/D5063）到比奇省要跨 11 段门，单程 10+ 分钟，往返把整段
# 挂机时间吃光——实测 D505 补给占时 91%、每杀净亏 95 金币（2026-11-24）。
SUPPLY_MAX_ROUTE_LEGS = 6
SUPPLY_BAG_TRIGGER = 4          # 背包剩余空位 ≤4 触发补给
SUPPLY_POTION_MIN_LEVEL = 10    # 药尽触发门槛
SUPPLY_POTIONS_PER_30MIN = 6    # 按剩余修炼时长粗估的买药量（每 30 分钟）
# 装备维护（用户 2026-11-23）：补给进城时顺手修背包里低持久装备 + 拆药水捆。
SUPPLY_REPAIR_DURA_PCT = 50     # 耐久低于上限 50% 才修（省金币）
SUPPLY_UNBIND_WHEN_LOW = True   # 药水（StdMode 0）少于目标量时拆捆补货
# 身上装备维护（用户 2026-11-24）：原版 ClientRepairItem 只认背包物品
# （ObjBase.pas:24653，身上不在 m_ItemList），所以必须先脱下再修、修完穿回。
# 只在补给城内做，且同一时刻只处理一个槽（避免半途换槽把装备丢在背包里）。
SUPPLY_EQUIP_SLOTS = (U_WEAPON, U_DRESS)   # 只维护主力消耗品：武器 + 衣服
# 「装备耐久过低」单独触发补给的门槛（比修理阈值 50% 更低）。
# 修理阈值决定「进城后修不修」，这里决定「值不值得为一趟修理专门进城」：
# 只有磨到 25% 才值得跑一趟，否则等背包满/药尽顺路修，避免补给占时上升。
SUPPLY_EQUIP_TRIGGER_PCT = 25
# 为「腾 1 格修身上装备」而卖高价杂物的兜底通道（用户 2026-11-24）。
# 背景：``takeOffItem`` 满包时拒绝卸下（handlers_items「背包已满，无法卸下」），
# 而身上装备必须先脱下来才能修（原版 ClientRepairItem 只认背包物品）。只要背包
# 常年 46/46，武器就永远修不上——实测 3 小时挂机磨到 44 点也一次没修。
# 这里允许「满包 + 身上确有该修装备」时，卖掉**最便宜的那件非药品**换 1 格；
# 只卖一件、只卖最便宜的，避免为修一把武器把整包战利品清空。
SUPPLY_SELL_FOR_ROOM = True


def supply_gate_route(gates_by_src: dict, src_map: str,
                      dst_map: str) -> list[tuple[str, int, int, str]] | None:
    """跨图门序列求解：对 ``world.gates_by_src`` 做图级 BFS。

    返回 [(src_map, 门x, 门y, dst_map), ...]——逐段用图内 ``bfs_path`` 走到
    门坐标，踩门由 move handler 自动换图；``src == dst`` 返回 []；
    图链路不通返回 None（调用方熔断停机，报告写明「无可达补给点」）。
    门是单向边（MapInfo.txt 的 src → dst），原版地图都有回程门，BFS 按
    有向图处理即可还原真实路线。
    """
    if src_map == dst_map:
        return []
    prev: dict[str, tuple[str, int, int] | None] = {src_map: None}
    queue = deque([src_map])
    while queue:
        cur = queue.popleft()
        for g in gates_by_src.get(cur, ()):
            nxt = g.dst_map
            if nxt in prev:
                continue
            prev[nxt] = (cur, g.src_x, g.src_y)
            if nxt == dst_map:
                legs: list[tuple[str, int, int, str]] = []
                node = nxt
                while prev[node] is not None:
                    pm, gx, gy = prev[node]
                    legs.append((pm, gx, gy, node))
                    node = pm
                legs.reverse()
                return legs
            queue.append(nxt)
    return None


def count_potions(w, p) -> tuple[int, int]:
    """背包里的血药 / 蓝药数量（StdMode 0 且 AC/MAC > 0）。"""
    hp = mp = 0
    for it in p.bag:
        t = w.item_templates.get(int(it.get("index", 0)))
        if t is None or t.stdMode != 0:
            continue
        if t.acMin > 0:
            hp += 1
        if t.macMin > 0:
            mp += 1
    return hp, mp


def claim_gift(world, p) -> tuple[bool, str]:
    """玛法领路人令牌领取（docs/OFFLINE-AFK-PLAN.md §3.6，已拍板机制）。

    规则全部走 server.properties 配置：每日限领 ``afk.claimPerDay`` 次、
    每次 +``afk.claimMinutes`` 分钟、余额上限 ``afk.poolCapMinutes``。
    记账走 ``character_afk`` 表——NPC 脚本的 [n] 标记重登归零（db.py 已知坑），
    绝不能用于限领。
    """
    cfg = world.config
    db = world.db
    afk = db.get_afk(p.character_id)
    t = today()
    claim_count = afk["claim_count"] if afk["claim_date"] == t else 0
    if claim_count >= max(1, cfg.afk_claim_per_day):
        return False, f"今天已领过 {claim_count} 次修炼补助，明天再来。"
    if afk["balance"] >= cfg.afk_pool_cap_minutes:
        return False, f"修炼时长余额已满（{afk['balance']} 分钟），先用掉一些。"
    balance = min(cfg.afk_pool_cap_minutes, afk["balance"] + cfg.afk_claim_minutes)
    db.save_afk(p.character_id, balance, t, claim_count + 1,
                afk["used_date"], afk["used_minutes"])
    return True, (f"领取 {cfg.afk_claim_minutes} 分钟修炼时长，"
                  f"当前余额 {balance} 分钟（每日可领 {cfg.afk_claim_per_day} 次）。")


def claim_gold(world, p) -> tuple[bool, str]:
    """玛法领路人每日金币补助（用户 2026-11-18：金币紧张，新增）。

    每日 1 次、金额 ``afk.goldPerDay``（默认 5000，设 0 关闭）；记账与修炼
    时长同表 ``character_afk``（gold_claim_date 列）——NPC 脚本的 [n] 标记
    重登归零（db.py 已知坑），绝不能用于限领。"""
    cfg = world.config
    amount = max(0, int(cfg.afk_gold_per_day))
    if amount <= 0:
        return False, "金币补助已关闭。"
    db = world.db
    t = today()
    afk = db.get_afk(p.character_id)
    if afk["gold_claim_date"] == t:
        return False, "今天的金币补助已经领过了，明天再来。"
    db.save_afk(p.character_id, afk["balance"], afk["claim_date"],
                afk["claim_count"], afk["used_date"], afk["used_minutes"], t)
    p.gold += amount
    world.send_stats(p)
    return True, f"领取 {amount} 金币补助（每日 1 次）。"


class NullSession:
    """离线代理的假会话：吞掉所有下行消息，其余属性对齐 net.py 的 WsSession。

    handler 链路用 ``find_by_session``（按对象身份查玩家）和
    ``session.send_json/send_text``（广播）——身份匹配对 NullSession 照常生效，
    下行消息进黑洞，其他在线玩家看到的广播不受影响。
    """

    def __init__(self, account_id: int = 0, account_name: str = ""):
        self.account_id = int(account_id)
        self.account_name = str(account_name)
        self.player_name = ""

    def send_json(self, msg: Any) -> None:      # noqa: ARG002 - 有意吞包
        pass

    def send_text(self, text: str) -> None:    # noqa: ARG002
        pass

    async def close(self) -> None:
        pass


@dataclass(slots=True)
class OfflinePilot(AutoPilot):
    """离线代理：决策内核与在线托管完全一致，只调整离线专属参数。

    注意 slots dataclass 继承的 3.10 坑：方法内**禁止零参数 super()**
    （__class__ cell 不重建），一律显式 ``AutoPilot.xxx(self, ...)``。
    """

    bag_full_stop_override: int = 1   # 补给关闭的 v1 回退固定满包停机

    exp_start: int = 0            # 开局本级经验（结算算经验增量用）
    level_start: int = 0
    max_exp_start: int = 0
    gold_start: int = 0
    restart_bonus: int = 0        # 重启续挂补偿（分钟，写进报告通知）
    deaths: int = 0
    remaining: int = 0            # 剩余修炼分钟（每 60s 递减，= afk_sessions 记账）
    # ---- 运行日志采样快照（pyserver/afk_log.py）----
    log: Any = None               # AfkSessionLog 或 None（日志关闭/打开失败）
    log_at: int = 0               # 上次采样时刻（0=还没采过）
    log_exp: int = 0              # 上次采样经验（算区间增量）
    log_kills: int = 0
    log_gold: int = 0
    log_supplies: int = 0         # 已开始的补给轮数（统计补给频率）
    log_errors: int = 0           # 异常计数（熔断阈值观察用）
    # ---- 补给闭环状态机 ----
    supply_state: str = ""        # ""=挂机中，其余见 SUPPLY_* 常量
    supply_reason: str = ""       # 本轮补给的原因（背包满/药水耗尽）
    supply_since: int = 0         # 当前状态进入时刻（超时熔断用）
    supply_fails: int = 0         # 连续补给失败次数（达上限转为长冷却，不停挂）
    supply_last_fail_at: int = 0  # 最近一次补给失败时刻（稳定升级后清零计数）
    supply_retry_until: int = 0   # 失败后的重试冷却截止
    last_supply_check_at: int = 0 # 触发判定节流
    supply_last_act_at: int = 0   # 卖/买动作节拍
    supply_leg_try: int = 0       # 当前门段/锚点段连续寻路受阻计数
    supply_npc_id: int = 0        # 当前交易 NPC（调试观察用）
    supply_return_tries: int = 0  # 补给失败后「先回锚点」的连续尝试次数
    supply_defend_since: int = 0  # 赶路清挡路怪的起始时刻（>15s 放弃，防卡死）
    supply_route: list = field(default_factory=list)   # 剩余门段 [(src,gx,gy,dst)]
    supply_unsellable: set = field(default_factory=set) # 无商人收购的 makeIndex
    supply_repaired: set = field(default_factory=set)   # 本轮已修的 makeIndex（防重复）
    supply_unbound: set = field(default_factory=set)    # 本轮已拆的卷包 makeIndex
    # ---- 身上装备「脱下→修理→穿回」（原版只认背包物品，见 _supply_repair_equipped）----
    maint_slot: int = -1          # 正在脱/穿的装备槽（-1=没有进行中的流程）
    maint_mi: int = 0             # 该装备的 makeIndex
    maint_stage: str = ""         # ""=空闲，"off"=已脱下待修，"on"=修好待穿回
    gear_no_npc_maps: set = field(default_factory=set)  # 没修理商的地图（别为修理反复跑）
    gear_retry_until: int = 0     # 「装备耐久低」触发补给的长冷却（防空转）
    anchor_map: str = ""          # 挂机锚点（补给出发前记忆）
    anchor_x: int = 0
    anchor_y: int = 0

    def adjust_cfg(self, cfg: dict) -> dict:
        # 拾取视野减半：给在线玩家留捡漏空间（§3.3，可配 lootSightCut）。
        cut = float(getattr(self.manager.service.config, "afk_loot_sight_cut", 0.5))
        adjusted = dict(cfg)
        adjusted["loot_sight"] = max(1, int(cfg.get("loot_sight", 8) * cut))
        return adjusted

    # ---- 补给钩子覆写（在线托管行为保持不变） ----------------------------

    def _supply_enabled(self, srv) -> bool:
        return bool(int(getattr(srv.config, "afk_supply_enabled", 1)))

    def _supply_town_map(self, srv, w=None, p=None) -> str:
        """补给目标地图：本地能补给就留在当前图，否则回配置的补给城。

        原版私服习惯回比奇补给，但我们的 1000×800 大矿区（图3）自带药店老板
        （卖药）与屠夫/客栈老板（收肉收杂物），跨 3 张图来回一趟要 8~10 分钟，
        实测占掉挂机 88% 的时间、2 小时只打 55 只怪。只要当前图能「买药 + 卖货」
        就地完成，就没必要折腾回城——这也更贴合托管「少走多看」的期望。
        地图切换/标定由 ``afk.supplyTownMap`` 保留为兜底（本地不满足时生效）。
        """
        fallback = str(getattr(srv.config, "afk_supply_town_map", "0") or "0")
        if w is not None and p is not None and p.map is not None:
            if self._can_supply_locally(srv, w, p):
                return p.map.name
        return fallback

    @staticmethod
    def _npc_reachable(w, p, npc) -> bool:
        """这个商人**真的走得到**吗（同图 + 同连通块）。

        为什么必须校验：祖玛阁（D5063）是原版「十二个独立小房间靠传送门
        串联」的迷宫地图，房间之间**没有任何步行走廊**（实测：挂机区所在
        房间 77 格，药品小贩所在房间另 77 格，两者 BFS 互不可达）。图里
        「有药商」不等于「买得到药」——只按 npcs_by_map 判断，代理会认为
        本地能补给，于是每轮都去撞墙，走到药尽、血低，最后停机。
        判定用 PathPlanner（和真正走路同一套 A*），单次预算很小：
        8 邻域搜索下 77 格房间几十毫秒就能搜完。
        """
        if p.map is None or npc.map is not p.map:
            return False
        if max(abs(p.x - npc.x), abs(p.y - npc.y)) <= 1:
            return True
        from .autopilot import PathPlanner
        plan = PathPlanner(p.map, p.x, p.y, npc.x, npc.y, False)
        state = plan.advance(budget_ms=12, max_nodes=4000)
        return state == "done" and bool(plan.build())

    def _can_supply_locally(self, srv, w, p) -> bool:
        """当前地图是否可就地补给：有**走得到**的药商，且背包腾得出格子。

        旧判据是「至少一件背包杂物能本地卖掉」——这个判据把「卖不掉也
        不需要卖」的情况误杀了：背包里只有一根卖价 10 的蜡烛（stdMode 30）时，
        本地没有商人收蜡烛 → 判「不能本地补给」→ 跨图跑去比奇省。
        而实际需求只是买药，蜡烛占 1 格不影响（背包 14/46 远没满）。
        2026-11-24 实测后果：D5063 本图有「药品小贩」（3 种血药，最猛 +90），
        代理却跨图赶路，撞上不可达的门 D5064 连续失败 → 停在原地被怪打死 /
        血低无药停机，整组「零金币、最低 HP 31%」。

        现在的判据按**真实需求**分两类：
        * 药不够 + 背包有空位 → 就地买药，不必卖任何东西；
        * 药不够 + 背包已满   → 必须能卖掉至少一件腾格子。
        另外所有商人候选都要过连通性校验（见 ``_npc_reachable``）。
        """
        want_mp = p.job == 1
        offers = self._supply_potion_offers(srv, w, p, want_mp)
        if not offers:
            return False
        hp_ok = any(o[2].acMin > 0 and self._npc_reachable(w, p, o[0])
                    for o in offers)
        mp_ok = (not want_mp) or any(o[2].macMin > 0 and self._npc_reachable(w, p, o[0])
                                    for o in offers)
        if not (hp_ok and mp_ok):
            return False
        # 背包还有空位 → 只要有走得到的药商就能就地补给
        if len(p.bag) < BAG_SIZE:
            return True
        # 背包已满：必须有走得到的商人肯收某件非药品，否则腾不出格子买药
        for it in p.bag:
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None or t.stdMode == 0:
                continue
            for npc in w.npcs_by_map.get(p.map.name, ()):
                if npc.merchant and npc.map is p.map \
                        and srv._sell_price(npc, t, it) > 0 \
                        and self._npc_reachable(w, p, npc):
                    return True
        return False

    def _on_danger_no_potion(self, srv, w, p, now: int) -> None:
        if self.supply_state:
            # 补给赶路途中血线危险：继续赶路（城里安全区），不停机。
            self.set_status("补给途中 · 血线危险", now)
            return
        if self._supply_enabled(srv) and p.map is not None:
            # 离线代理有补给闭环：回城卷直接传送 + 进入补给状态买药，
            # 买完会回原锚点继续修炼；不卷则沿用旧行为（停机）。
            scroll = _find_town_scroll(w, p)
            if scroll is not None:
                self._supply_begin(srv, w, p, now, "血量过低")   # 先记原图锚点
                srv._use_item(p.session, {"type": "useItem",
                                          "makeIndex": int(scroll.get("makeIndex", 0))})
                return
        AutoPilot._on_danger_no_potion(self, srv, w, p, now)

    def _on_bag_full(self, srv, w, p, now: int) -> None:
        if self.supply_state:
            return          # 补给状态机正在处理背包问题（卖货腾格子）
        if self._supply_enabled(srv):
            # ⚠️ 必须尊重补给冷却。这里曾被漏掉：满包时 _survive 每 tick 都会
            # 调到本方法，而「卖无可卖 + 药已买满 + 商人就在身边」的一轮补给
            # 只需 800ms 就能走完一圈（GOTO_TOWN 已在本地 → SELL 空转 → BUY
            # 已满 → GOTO_ANCHOR 1 格 → FINISH），于是每 tick 重起一轮。
            # 2026-11-24 日志实测：90 分钟刷出 2885 轮补给、73% 时间耗在补给
            # 流程里、63% 分钟零经验——诊断手段直接抓出了一个老 bug。
            if now < self.supply_retry_until:
                self.set_status("背包已满 · 补给冷却中，继续打怪不拾取", now)
                return
            self._supply_begin(srv, w, p, now, "背包已满")
            return
        AutoPilot._on_bag_full(self, srv, w, p, now)      # v1：回城卷/停机

    def _clean_meat(self, srv, w, p, now: int) -> None:
        # 补给途中不丢肉：肉正是要拿去卖钱的货，丢掉等于烧金币。
        if self.supply_state:
            return
        AutoPilot._clean_meat(self, srv, w, p, now)

    # ---- 主循环 ---------------------------------------------------------

    def tick(self, now: int) -> None:
        mgr = self.manager
        srv = mgr.service
        w = srv.world
        p = w.actor(self.player_id)
        if p is None or not isinstance(p, Player) or p.session is None:
            mgr.discard(self.player_id)
            return
        if not self.active:
            return
        if getattr(p, "dead", False) or not p.alive:
            mgr.handle_death(p, self)
            return
        cfg = self.adjust_cfg(PRESETS[self.preset])
        if self.supply_state:
            self._supply_tick_frame(srv, w, p, cfg, now)
            return
        # 补给赶路期间不做零进展熔断（卖/买/赶路本来就没经验），
        # 由状态超时与失败熔断兜底。
        if self._tick_progress(p, now):
            return
        if self._supply_check_trigger(srv, w, p, now):
            return
        AutoPilot._tick_combat(self, srv, w, p, cfg, now)

    # ---- 补给触发 -------------------------------------------------------

    def _supply_check_trigger(self, srv, w, p, now: int) -> bool:
        """按节流周期判定是否需要补给。返回 True = 已进入补给或已停机。"""
        if now - self.last_supply_check_at < SUPPLY_CHECK_MS:
            return False
        self.last_supply_check_at = now
        if self.supply_fails and now - self.supply_last_fail_at >= SUPPLY_FAIL_RESET_MS:
            # 稳定打了一段时间（有经验/掉落进展）→ 上次的失败不该一直记账
            self.supply_fails = 0
        if not self._supply_enabled(srv) or now < self.supply_retry_until:
            return False
        why = ""
        if len(p.bag) >= BAG_SIZE - SUPPLY_BAG_TRIGGER:
            why = "背包将满"
        elif p.level >= SUPPLY_POTION_MIN_LEVEL:
            hp, mp = count_potions(w, p)
            if p.job in (1, 2):
                # 法师/道士都靠蓝吃饭（道士治愈+火符，用户 2026-11-26）
                if hp == 0 or mp == 0:
                    why = "药水耗尽"
            elif hp == 0:
                why = "药水耗尽"
        if not why:
            # 身上的武器/衣服磨到阈值以下也该补给一次（否则只有「背包满/药尽」
            # 才会进城，武器可能一路磨到归零才被人发现）。用更低的 25% 门槛
            # 触发，避免和背包满的常规补给抢节奏。
            #
            # ⚠️ 必须带长冷却：``_supply_maintain`` 做完会 clear ``supply_repaired``，
            # 若补给点没有修理商（如 D011 这类纯猎场），装备永远修不上、
            # ``_gear_needs_supply`` 永远为 True，就会每 10 秒触发一次补给，
            # 退化回 2885 轮空转的老坑。所以失败/做不成都要记一张长冷却。
            if (self._supply_enabled(srv) and now >= self.gear_retry_until
                    and getattr(srv.config, "afk_supply_repair", 1)
                    and self._gear_needs_supply(w, p)):
                why = "装备耐久过低"
        if not why:
            return False
        # 预检可达性：图链路不通是确定性的，别每 10 秒重算一次门序列；
        # 也不直接停机——打怪升级本身还在产出，只是这趟补给不做了。
        town = self._supply_town_map(srv, w, p)
        if p.map is not None and p.map.name != town:
            route = supply_gate_route(w.gates_by_src, p.map.name, town)
            if route is None:
                self.supply_retry_until = now + SUPPLY_LONG_RETRY_MS
                self.set_status(f"当前地图无可达补给点（{why}），继续打怪",
                                now, force=True)
                return True
            if len(route) > SUPPLY_MAX_ROUTE_LEGS:
                # 链路太长（祖玛系列要跨 11 段门才到比奇省）：单程就要
                # 10+ 分钟，来回把整段挂机时间吃光（实测 D505 补给占时 91%、
                # 每杀金币 -95 的净亏损）。这种图宁可「不补给、只打怪」，
                # 等背包满/药尽再说；装备磨损靠 25% 门槛那趟顺路修。
                self.supply_retry_until = now + SUPPLY_LONG_RETRY_MS
                self.set_status(
                    f"补给路线过长（{len(route)} 段门 / {why}），先继续打怪",
                    now, force=True)
                return True
        self._supply_begin(srv, w, p, now, why)
        return True

    def _equipment_needs_repair(self, w, p) -> bool:
        """身上武器/衣服是否低于**修理阈值**（50%，而非进城的 25%）。

        与 ``_gear_needs_supply`` 的区别：那个决定「要不要专门跑一趟补给」，
        用更低的 25% 门槛；这个决定「要不要为腾格子卖一件杂物」，用与
        背包修理一致的 50% 阈值——只要进城后该修，
        就值得卖一件腾位置。
        """
        for slot in SUPPLY_EQUIP_SLOTS:
            if slot >= len(p.equipment):
                continue
            it = p.equipment[slot]
            if it is None:
                continue
            mi = int(it.get("makeIndex", 0))
            if mi in self.supply_repaired:
                continue
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None:
                continue
            dura = int(it.get("durability", 0) or 0)
            dmax = int(it.get("durabilityMax", 0) or 0)
            if dmax > 0 and dura * 100 < dmax * SUPPLY_REPAIR_DURA_PCT:
                return True
        return False

    def _gear_needs_supply(self, w, p) -> bool:
        """身上武器/衣服是否已磨到需要专门跑一趟补给的程度。

        与背包修理（50% 阈值）不同：这里只看身上
        两个主力槽，且用更低的 ``SUPPLY_EQUIP_TRIGGER_PCT``——进城是笔开销
        （跨图来回 3~8 分钟），只有磨得够狠才值得专门跑。同一个 makeIndex
        修过就跳过（``supply_repaired``），避免修完立刻又触发。
        """
        for slot in SUPPLY_EQUIP_SLOTS:
            if slot >= len(p.equipment):
                continue
            it = p.equipment[slot]
            if it is None:
                continue
            mi = int(it.get("makeIndex", 0))
            if mi in self.supply_repaired:
                continue
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None:
                continue
            dura = int(it.get("durability", 0) or 0)
            dmax = int(it.get("durabilityMax", 0) or 0)
            if dmax > 0 and dura * 100 < dmax * SUPPLY_EQUIP_TRIGGER_PCT:
                return True
        return False

    def _safe_anchor(self, w, p, x: int, y: int) -> tuple[int, int]:
        """锚点避开门格邻域：回锚点时若落脚贴着双向门，A* 最短路会穿过门格
        被传送到别的图，又被回程门弹回来——实测图3(666,282) 贴 3↔0155
        双向门，2 小时里 90% 时间在图3↔0155 横跳。这里就近找一个离所有
        传送门 ≥3 格的可走空格作为真实落脚点。
        """
        gm = p.map
        if gm is None:
            return x, y
        gates = getattr(w, "gates_by_src", {}).get(gm.name, ())
        gate_cells = [(g.src_x, g.src_y) for g in gates]
        if not gate_cells:
            return x, y

        def dist_to_gate(ax: int, ay: int) -> int:
            return min(max(abs(ax - gx), abs(ay - gy)) for gx, gy in gate_cells)

        if dist_to_gate(x, y) >= 3:
            return x, y
        # 半径 2~5 环内找离门 ≥3 的可走空格，优先近的
        for radius in range(2, 6):
            best, best_d = None, 1 << 30
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    nx, ny = x + dx, y + dy
                    if not gm.inside(nx, ny) or not gm.can_walk(nx, ny) \
                            or gm.occupied(nx, ny):
                        continue
                    gd = dist_to_gate(nx, ny)
                    if gd < 3:
                        continue
                    d = max(abs(dx), abs(dy))
                    if d < best_d:
                        best, best_d = (nx, ny), d
            if best is not None:
                return best
        return x, y      # 实在没有安全格，保留原位

    def _supply_begin(self, srv, w, p, now: int, why: str) -> None:
        self.supply_reason = why
        self.supply_state = SUPPLY_GOTO_TOWN
        self.supply_since = now
        self.supply_route = []
        self.supply_leg_try = 0
        self.supply_defend_since = 0
        # 卖不掉的物品（太远/没人收）在同图跨补给周期保留黑名单，别每轮重试；
        # 换图后商人变了才清空。
        new_map = p.map.name if p.map else ""
        if new_map != getattr(self, "anchor_map", ""):
            self.supply_unsellable.clear()
        self.supply_npc_id = 0
        self.supply_return_tries = 0
        self.anchor_map = new_map
        self.anchor_x, self.anchor_y = self._safe_anchor(w, p, p.x, p.y)
        self.log_supplies += 1
        self.manager._log_event(self, p, "supply_begin", why=why,
                                seq=self.log_supplies,
                                map=self.anchor_map,
                                anchor=[self.anchor_x, self.anchor_y],
                                gold=p.gold, bag=len(p.bag),
                                hp=p.hp, maxHp=p.max_hp,
                                fails=self.supply_fails,
                                remaining=self.remaining)
        self.set_status(f"补给开始 · {why} · 记忆锚点 {self.anchor_map}", now, force=True)

    # ---- 补给状态机骨架 -------------------------------------------------

    def _supply_tick_frame(self, srv, w, p, cfg, now: int) -> None:
        """补给期间仍做装备维护与生存（喝药/道士治愈），战斗循环由状态机接管。"""
        try:
            self._ensure_equipment(srv, w, p, now)
            self._survive(srv, w, p, cfg, now)
            if self.active:
                self._supply_tick(srv, w, p, now)
        except Exception as e:
            print(f"[afk] {p.name} 补给异常: {type(e).__name__}: {e}")
            self.manager.disable(p, "补给状态机内部错误，离线修炼已停止")
            return
        if self.active:
            self.set_status(self.status, now)

    def _supply_tick(self, srv, w, p, now: int) -> None:
        if now - self.supply_since > SUPPLY_STATE_TIMEOUT_MS:
            self._supply_fail(srv, w, p, now, f"状态 {self.supply_state} 超时")
            return
        # 赶路被围：仅在跨图赶路（goto_town/goto_anchor）时清挡路怪。
        # 绝不能在 sell/buy 阶段用——那是城内找商人，若寻路一时受阻，
        # _defend_blocker 会盯住一只打不死的怪每 tick return，实测卡死 20 分钟。
        if (self.supply_state in (SUPPLY_GOTO_TOWN, SUPPLY_GOTO_ANCHOR)
                and self.supply_leg_try > 0
                and (now - self.supply_defend_since) < 15_000
                and self._defend_blocker(srv, w, p, now)):
            if not self.supply_defend_since:
                self.supply_defend_since = now
            return
        if self.supply_leg_try == 0:
            self.supply_defend_since = 0     # 寻路恢复：重置清怪窗口
        town = self._supply_town_map(srv, w, p)
        if self.supply_state == SUPPLY_GOTO_TOWN:
            if self._supply_cross_map(srv, w, p, now, town):
                self.supply_state = SUPPLY_SELL
                self.supply_since = now
                # 不清 unsellable：卖不掉的东西同图跨周期保持黑名单，
                # 否则每轮补给都对同一批杂物重复跑远路找商人。
                self.set_status("补给 · 已到补给城，清理背包", now, force=True)
        elif self.supply_state == SUPPLY_SELL:
            self._supply_sell(srv, w, p, now)
        elif self.supply_state == SUPPLY_BUY:
            self._supply_buy(srv, w, p, now)
        elif self.supply_state == SUPPLY_GOTO_ANCHOR:
            if self._supply_cross_map(srv, w, p, now, self.anchor_map):
                if max(abs(p.x - self.anchor_x), abs(p.y - self.anchor_y)) <= 1:
                    self._supply_finish(srv, w, p, now)
                    return
                if self._step_to(srv, w, p, self.anchor_x, self.anchor_y,
                                 ("anchor", 0), "补给 · 返回锚点", now):
                    self.supply_leg_try = 0
                else:
                    self.supply_leg_try += 1
                    self.unreachable.pop(0, None)
                    if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                        self._supply_fail(srv, w, p, now,
                                          f"锚点 ({self.anchor_x},{self.anchor_y}) 无法返回")

    def _defend_blocker(self, srv, w, p, now: int) -> bool:
        """补给赶路被怪贴身堵住时先清掉一只，返回 True = 本 tick 已占用。

        背景：补给状态不主动打怪，玩家站着不动会被怪围成一圈——A* 的八个
        邻格全被 ``occupied`` 占住时连部分路径都算不出来，于是
        ``_step_to`` 立刻返回 False，四拍就计一次「门无法抵达」，两次补给
        失败直接停机（模拟里 20~30 分钟必现）。这里只在「寻路已经失败过」
        时出手，不把赶路变成清怪，节奏仍然快。
        """
        if p.map is None:
            return False
        blocker, best_d = None, 9
        for a in w.nearby(p.map, p.x, p.y, 2):
            if not isinstance(a, Monster) or not a.alive or a.master_id >= 0:
                continue
            d = p.distance_to(a)
            if (d <= 1 or a.target_id == p.id) and d < best_d:
                blocker, best_d = a, d
        if blocker is None:
            return False
        if self._action_busy(p, now) or self._step_busy(now):
            return True
        if p.job == 1 and self._mage_fire(srv, w, p, blocker, now):
            return True
        if p.job == 2 and self._taoist_fire(srv, w, p, blocker, now):
            return True
        if best_d <= 1 and now - self.last_attack_at >= attack_interval_ms(p.level):
            self.last_attack_at = now
            exp_before = p.exp
            srv._attack(p.session, {"type": "attack", "targetId": blocker.id})
            self.set_status(f"补给 · 清理挡路 {blocker.name}", now)
            if p.exp != exp_before:
                self.kills += 1
                self.last_exp = p.exp
                self.last_progress_at = now
            return True
        return False

    # ---- 跨图导航 -------------------------------------------------------
    def _supply_cross_map(self, srv, w, p, now: int, dest_map: str) -> bool:
        """沿门链路逐段走到 dest_map。返回 True = 已在目标图；
        False = 仍在途中（致命失败已在内部熔断/计失败）。"""
        if p.map is None:
            self._supply_fail(srv, w, p, now, "角色无地图状态")
            return False
        if p.map.name == dest_map:
            self.supply_route = []
            self.supply_leg_try = 0
            return True
        if not self.supply_route:
            route = supply_gate_route(w.gates_by_src, p.map.name, dest_map)
            if route is None:
                self._supply_fail(srv, w, p, now,
                                  f"{p.map.name} → {dest_map} 无门链路")
                return False
            self.supply_route = route
            self.supply_leg_try = 0
            self.set_status(f"补给 · 规划路线 {p.map.name}→{dest_map}"
                            f"（{len(route)} 段门）", now, force=True)
        while self.supply_route:
            leg = self.supply_route[0]
            if p.map.name == leg[0]:
                break
            if p.map.name == leg[3]:
                self.supply_route.pop(0)      # 已踩门换图：这一段完成
                self.supply_leg_try = 0
                self.supply_since = now       # 超时按「每段门」算，不是整趟补给
                continue
            # 位置漂移（死亡复活等）：丢弃旧路线，下一 tick 从当前位置重算
            self.supply_route = []
            return False
        if not self.supply_route:
            return True
        leg = self.supply_route[0]
        gx, gy = leg[1], leg[2]
        if (p.x, p.y) == (gx, gy):
            return False                      # 已踩上门，等换图短锁落地
        if self._step_to(srv, w, p, gx, gy, ("supply", id(leg)),
                         "补给 · 前往门点", now, exact=True):
            self.supply_leg_try = 0
        else:
            # 门坐标固定，黑名单没意义：清掉允许重试（可能只是怪挡路）
            self.supply_leg_try += 1
            self.unreachable.pop(id(leg), None)
            if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                self._supply_fail(srv, w, p, now,
                                  f"补给门 {leg[0]}({gx},{gy}) 无法抵达")
        return False

    def _ensure_equipment(self, srv, w, p, now: int) -> None:
        """覆盖父类：修理流程占用该槽时不要「自动补装」把它塞回背包去。

        父类 ``AutoPilot._ensure_equipment``（autopilot.py:584）见到空武器槽
        就无条件从背包挑一把穿上。而「脱下→修→穿回」流程中该槽**必然**是空的
        （装备正躺在背包里等着修），于是父类每 1 秒就把待修的武器穿回去，
        修理流程被反复打断 —— 实测（2026-11-24）一次脱穿后 repair 从未执行，
        装备持久照样单调下降。这里把该槽让给修理流程。
        """
        if self.maint_stage:
            slot = self.maint_slot
            if 0 <= slot < len(p.equipment) and p.equipment[slot] is None:
                # 该槽归修理流程所有：不做自动补装，也不重复扣检查节流
                return
        # ⚠️ 必须显式写 AutoPilot._ensure_equipment：OfflinePilot 是 slots
        # dataclass 子类，Python 3.10~3.12 不修正零参数 super() 的 __class__
        # cell，会抛「super(type, obj): obj must be an instance or subtype of type」
        AutoPilot._ensure_equipment(self, srv, w, p, now)

    def _supply_sell(self, srv, w, p, now: int) -> None:
        """逐个卖掉肉（StdMode 40）与低于阈值的杂物；卖空转买药。"""
        # 已经脱下的装备必须**优先**修完穿回：这时它躺在背包里，若继续走卖货
        # 循环，最坏会被当杂物卖掉（备用武器 price 可能 ≤ 阈值）。所以流程
        # 一旦开始就独占，直到 _maint_reset。
        if self.maint_stage:
            self._supply_maintain_equipped(srv, w, p, now)
            return
        cand = cand_t = None
        for it in p.bag:
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None:
                continue
            mi = int(it.get("makeIndex", 0))
            if mi in self.supply_unsellable:
                continue
            if t.stdMode == 40 or (t.stdMode not in (0, 3, 25)
                                   and t.price <= SUPPLY_SELL_MAX_PRICE):
                cand, cand_t = it, t
                break
        if cand is None and SUPPLY_SELL_FOR_ROOM \
                and len(p.bag) >= BAG_SIZE \
                and self._equipment_needs_repair(w, p):
            # 满包 + 身上确有该修的装备 → 卖掉最便宜的非药品，换 1 格出来。
            # 只挑一件、且要求该行商人也收（_supply_sell_npc 会二次校验价格）。
            # ⚠️ 优先「就近」而非「最便宜」：为修装备腾格子只是一次性动作，
            # 走 200 格去找一个多出 3 金币的买家会把补给占时拉爆（实测补给
            # 阶段本来就占 30~50% 时间）。只在 SUPPLY_SELL_MAX_DIST 内挑。
            best, best_t, best_p = None, None, 1 << 60
            for it in p.bag:
                t = w.item_templates.get(int(it.get("index", 0)))
                if t is None or t.stdMode in (0, 3, 25):
                    continue          # 药品/卷轴/符不能卖（是补给物资）
                mi = int(it.get("makeIndex", 0))
                if mi in self.supply_unsellable:
                    continue
                npc_ok = self._supply_sell_npc(srv, w, p, t, it)
                if npc_ok is None:
                    continue
                d = max(abs(p.x - npc_ok.x), abs(p.y - npc_ok.y))
                price = srv._sell_price(npc_ok, t, it)
                # 就近卖：距离上限取「就地 20 格 / 专程进城半张图」。
                # 腾格子卖价不重要（本来就是要清掉），关键是别为了 3 金币
                # 多跑半个地图——所以按距离挑，不按卖价挑。
                room_d = max(20, self._supply_npc_max_dist(p) // 2)
                if price > 0 and d <= room_d and d < best_p:
                    best, best_t, best_p = it, t, d
            if best is not None:
                cand, cand_t = best, best_t
                # 只卖一件：下 tick 买卖完之后 `_supply_maintain_equipped` 才有空格可用
                self.set_status("补给 · 背包已满，卖掉一件杂物腾出修理空间", now,
                                force=True)
        if cand is None:
            # 装备维护（用户 2026-11-23）：卖空后顺手修低持久装备 + 拆药水捆，
            # 全部做完再进入买药阶段。复用现有 handler，进城补给点即可完成。
            # 身上装备（用户 2026-11-24）：先处理「脱下→修→穿回」，再管背包。
            if self._supply_maintain_equipped(srv, w, p, now):
                return
            if self._supply_maintain(srv, w, p, now):
                return
            self.supply_state = SUPPLY_BUY
            self.supply_since = now
            self.supply_npc_id = 0
            self.supply_leg_try = 0
            self.set_status("补给 · 背包清理完成，采购药水", now, force=True)
            return
        npc = self._supply_sell_npc(srv, w, p, cand_t, cand)
        if npc is None:
            self.supply_unsellable.add(int(cand.get("makeIndex", 0)))
            return
        self.supply_npc_id = npc.id
        if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
            if self._step_to(srv, w, p, npc.x, npc.y, ("supply_npc", npc.id),
                             f"补给 · 前往{npc.name}", now):
                self.supply_leg_try = 0
            else:
                self.supply_leg_try += 1
                self.unreachable.pop(npc.id, None)
                if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                    self._supply_fail(srv, w, p, now, f"无法走近商人 {npc.name}")
            return
        if now - self.supply_last_act_at < SUPPLY_ACT_MS:
            return
        self.supply_last_act_at = now
        srv._sell_item(p.session, {"type": "sellItem", "npcId": npc.id,
                                   "makeIndex": int(cand.get("makeIndex", 0))})

    def _supply_npc_max_dist(self, p) -> int:
        """找商人时的距离预算：挂机图上就近卖（40 格），专程进补给城走全图。

        判据是「当前图和挂机锚点是不是同一张」——不在锚点说明这趟是
        为了补给专门跑出来的，几百格也得走完；在锚点则只是顺手卖，
        绝不为了几十金币绕远（那会把补给占时拉爆）。
        """
        if p.map is None:
            return SUPPLY_SELL_MAX_DIST
        if self.anchor_map and p.map.name != self.anchor_map:
            return SUPPLY_TOWN_MAX_DIST
        return SUPPLY_SELL_MAX_DIST

    def _supply_sell_npc(self, srv, w, p, t, it):
        """找当前地图上肯收购这件东西的最近商人（价格校验走 _sell_price）。"""
        best, best_d = None, 10 ** 9
        for npc in w.npcs_by_map.get(p.map.name if p.map else "", ()):
            if not npc.merchant or npc.map is not p.map:
                continue
            if srv._sell_price(npc, t, it) <= 0:
                continue
            d = max(abs(p.x - npc.x), abs(p.y - npc.y))
            if d < best_d:
                best, best_d = npc, d
        if best_d > self._supply_npc_max_dist(p):
            return None          # 太远：这件杂物不值得专门跑一趟
        return best

    # ---- 装备维护：修理 + 药水解捆（用户 2026-11-23）---------------------

    @staticmethod
    def _npc_really_repairs(npc) -> bool:
        """薄封装 → 模块级 npc_really_repairs（在线托管复用同一判定）。"""
        return npc_really_repairs(npc)

    def _supply_repair_npc(self, srv, w, p, item=None, item_tpl=None):
        """找当前地图上**能修这件东西**的最近商人。

        两段式筛选：
        1. 只认「真声明了修理能力」的商人（``_npc_really_repairs``）；
        2. 而且要能真的修**这一件**——原版 ``_get_item_price``
           （handlers_npc.py:162）先看商人经不经销该类货（脚本头 ``+N``
           即 ``item_types``），不经销就返回 -1 → 修理价 -1 → 原版回
           「这个物品无法修理」。
           实际踩坑：比奇省「高家店老板」脚本有 ``[@repair]`` 但
           ``item_types=[10,11,15]`` 是服装店，修不了武器（stdMode 5），
           代理却当它是最近修理商 → 跑了 12 格过去、修理被拒、白耗一轮
           （2026-11-24 实测「修理未生效 ... 高家店老板 距离 12」）。
        整张图都没有严格匹配的商人时，退回 ``srv._repair_allowed`` 的宽松
        判定（对 1.80 老脚本兼容），但**仍要求对该件修理价 ≥0**——否则
        走过去也修不成，不如直接放弃。
        """
        strict_npcs = [n for n in w.npcs_by_map.get(p.map.name if p.map else "", ())
                       if n.merchant and n.map is p.map
                       and self._npc_really_repairs(n)]
        if not strict_npcs:
            strict_npcs = [n for n in w.npcs_by_map.get(p.map.name if p.map else "", ())
                           if n.merchant and n.map is p.map
                           and srv._repair_allowed(n, "repair")]
        best, best_d = None, 10 ** 9
        for npc in strict_npcs:
            if item is not None and item_tpl is not None:
                try:
                    if srv._repair_price(npc, item_tpl, item, "repair") < 0:
                        continue      # 这家店不经营这类货：修不了
                except AttributeError:
                    pass              # 单测桩里没有该函数：按可修处理
            d = max(abs(p.x - npc.x), abs(p.y - npc.y))
            if d < best_d:
                best, best_d = npc, d
        if best is None:
            return None
        if best_d > self._supply_npc_max_dist(p):
            return None
        return best

    def _supply_unbind_candidate(self, w, p):
        """背包里能拆出药水的卷包（StdMode 31 + UnbindList 目标是 StdMode 0）。"""
        for it in p.bag:
            mi = int(it.get("makeIndex", 0))
            if mi in self.supply_unbound:
                continue
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None or t.stdMode != 31:
                continue
            target_name = w.unbind_item_name(t.shape)
            if not target_name:
                continue
            target_t = w.item_template_by_name(target_name)
            if target_t is not None and target_t.stdMode == 0:
                return it
        return None

    def _supply_maintain_equipped(self, srv, w, p, now: int) -> bool:
        """维护**身上**的武器/衣服：脱下 → 修理 → 穿回。返回 True=本 tick 已占用。

        为什么必须脱下来：原版 ``ClientRepairItem``（ObjBase.pas:24653-24681）
        只在 ``m_ItemList``（背包）里按 MakeIndex 找 UserItem，身上的 ``m_UseItems``
        不在其中，所以玩家在原版里修身上装备也得先卸下。离线代理此前只遍历
        ``p.bag``（``_supply_maintain``），于是**身上那件从不被修**——
        矩阵实测武器 4000 持久每分钟磨 25~38，约 105~158 分钟就归零，而
        ``do_damage_weapon`` 归零时直接把槽位置 None（不进背包、不落地），
        等于一轮 2 小时挂机就永久报废一把武器。

        流程约束（每一条都是为了避免「把玩家的装备搞丢」）：
        * **只在补给城内做**：调用方保证 ``supply_state == SUPPLY_SELL``，
          人已在城门口/商人旁，脱装期间只挨安全区的打（甚至没怪）。
        * **同时只处理一个槽**：``maint_stage`` 非空时只推进当前流程，
          绝不中途换槽——换槽会让前一件停在背包里，而补给结束的
          ``_supply_finish`` 不负责穿回。
        * **背包必须有空格**：``_take_off_item`` 在满包时会拒绝卸下
          （「背包已满，无法卸下」），所以先确认 ``len(p.bag) < BAG_SIZE``；
          这也正好是补给阶段刚卖完的状态。
        * **穿回用 takeOnItem**：走原版 ``_take_on_slot`` 全量校验（等级/重量/
          男女装），不绕过任何规则；万一穿不回（例如中途升级把负重算法变了），
          装备仍在背包里，最坏情况是「裸装打怪」而不是丢失。
        * 复制品不参与：只按 ``makeIndex`` 精确定位，避免同名装备串号。
        """
        cfg = getattr(srv, "config", None)
        if not getattr(cfg, "afk_supply_enabled", 1):
            return False
        if not getattr(cfg, "afk_supply_repair", 1):
            return False

        # ---- 已有未完成的流程：只推进它，绝不换槽 ----
        if self.maint_stage:
            return self._supply_maint_step(srv, w, p, now)

        # ---- 挑一件需要修的身上装备 ----
        cand_slot, cand_it, cand_t = -1, None, None
        for slot in SUPPLY_EQUIP_SLOTS:
            if slot >= len(p.equipment):
                continue
            it = p.equipment[slot]
            if it is None:
                continue
            mi = int(it.get("makeIndex", 0))
            if mi in self.supply_repaired:
                continue
            t = w.item_templates.get(int(it.get("index", 0)))
            if t is None:
                continue
            dura = int(it.get("durability", 0) or 0)
            dmax = int(it.get("durabilityMax", 0) or 0)
            if dmax > 0 and dura * 100 < dmax * SUPPLY_REPAIR_DURA_PCT:
                cand_slot, cand_it, cand_t = slot, it, t
                break
        if cand_it is None:
            return False        # 没有要修的：交给背包那套逻辑

        mi = int(cand_it.get("makeIndex", 0))
        # 没有**能修这件**的商人在本图 → 记入黑名单（本轮不再反复尝试），
        # 交给背包逻辑。候选要传进去：服装店有 [@repair] 但修不了武器。
        npc = self._supply_repair_npc(srv, w, p, cand_it, cand_t)
        if npc is None:
            self.supply_repaired.add(mi)
            return False
        # 背包满 → 卸不下来。标记跳过（卖货阶段会先腾格子，下轮再来）
        if len(p.bag) >= BAG_SIZE:
            return False

        self.maint_slot, self.maint_mi = cand_slot, mi
        self.supply_npc_id = npc.id
        # 离商人太远先走过去（复用补给赶路的节流与重试计数）
        if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
            self.maint_stage = "off"
            if self._step_to(srv, w, p, npc.x, npc.y, ("maint_npc", npc.id),
                             f"补给 · 前往{npc.name}修理{t.name}", now):
                self.supply_leg_try = 0
            else:
                self.supply_leg_try += 1
                if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                    self.maint_stage = ""
                    self.supply_repaired.add(mi)
            return True
        self.maint_stage = "off"
        return self._supply_maint_step(srv, w, p, now)

    def _supply_maint_step(self, srv, w, p, now: int) -> bool:
        """推进「脱下→修理→穿回」流程一步。"""
        slot, mi = self.maint_slot, self.maint_mi
        if slot < 0 or slot >= len(p.equipment):
            self._maint_reset()
            return False

        if self.maint_stage == "off":
            it = p.equipment[slot]
            if it is None or int(it.get("makeIndex", 0)) != mi:
                # 装备已经不在原位（死亡掉落/被换掉）→ 放弃，别穿错东西
                self._maint_reset()
                return False
            if len(p.bag) >= BAG_SIZE:
                self._maint_reset()      # 背包满：留给卖货腾格子，下轮再来
                return False
            # ⚠️ 距离校验必须在**脱下之前**：原版 takeOffItem 不校验距离，但
            # repairItem 走 `_npc_and_player` 要求 ≤15 格。若先脱下再赶路，
            # 中途一旦中断（寻路失败/补给超时）就得 reclaim 穿回，形成
            # 「脱了又穿」的活锁（2026-11-24 实测 12 分钟脱穿 8 次 нуль 修成）。
            # 先贴着商人再脱，这是唯一安全的顺序。
            npc = self._supply_repair_npc(srv, w, p, it, w.item_templates.get(
                int(it.get("index", 0))))
            if npc is None:
                self._maint_reclaim(srv, w, p)
                return False
            if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
                if self._step_to(srv, w, p, npc.x, npc.y, ("maint_npc", npc.id),
                                 f"补给 · 前往{npc.name}修理", now):
                    self.supply_leg_try = 0
                else:
                    self.supply_leg_try += 1
                    if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                        self._maint_reclaim(srv, w, p)
                return True
            if now - self.supply_last_act_at < SUPPLY_ACT_MS:
                return True
            self.supply_last_act_at = now
            srv._take_off_item(p.session, {"type": "takeOffItem",
                                           "slot": slot, "makeIndex": mi})
            self.maint_stage = "on"     # 下一步：就地修它
            self.set_status("补给 · 卸下装备准备修理", now, force=True)
            return True

        # ---- maint_stage == "on"：找背包里这件，修，然后穿回 ----
        it = p.bag_item(mi) if hasattr(p, "bag_item") else None
        if it is None:
            # 不在背包（卖掉了/丢了）→ 放弃
            self._maint_reset()
            return False
        npc = self._supply_repair_npc(srv, w, p, it, w.item_templates.get(
            int(it.get("index", 0))))
        if npc is None:
            # 本图没有能修这件的商人：装备必须**先穿回去**（不能留在背包里裸装）
            self._maint_reclaim(srv, w, p)
            return False
        # ⚠️ 原版 `_npc_and_player`（handlers_npc.py:34）要求
        # `p.distance_to(npc) <= 15`，超距直接把请求丢弃且**不报错**。
        # 之前这里没做距离校验：跨图进城后角色落在 (42,109)、铁匠在 (302,219)，
        # 请求被静默扔掉，`before == after`、金币不变，但代码照样把该件记进
        # `supply_repaired` 并「穿回」——于是每次补给都白跑，武器一路磨到 0
        # （2026-11-24 实修 5 次全失败，就是这条）。先走过去，贴近了再修。
        if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
            if self._step_to(srv, w, p, npc.x, npc.y, ("maint_npc", npc.id),
                             f"补给 · 前往{npc.name}修理", now):
                self.supply_leg_try = 0
            else:
                self.supply_leg_try += 1
                if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                    self._maint_reclaim(srv, w, p)      # 走不过去 → 穿回，放弃本件
                    self.supply_repaired.add(mi)
            return True
        if now - self.supply_last_act_at < SUPPLY_ACT_MS:
            return True
        self.supply_last_act_at = now
        dura_before = int(it.get("durability", 0) or 0)
        srv._repair_item(p.session, {"type": "repairItem", "npcId": npc.id,
                                     "makeIndex": mi, "mode": "repair"})
        # 修理是否真生效：耐久没涨就是失败（价格负数/金币不足/被 handler 拒），
        # 不能盲目记成「已修」——否则本轮不会重试，小事拖成整轮白跑。
        it2 = p.bag_item(mi)
        dura_after = int(it2.get("durability", 0) or 0) if it2 is not None else 0
        if dura_after <= dura_before:
            self.supply_repaired.add(mi)
            print(f"[afk] 修理未生效 {p.name} {getattr(it2, 'get', lambda k, d=None: d)('index', 0)}"
                  f" mi={mi} dura {dura_before}→{dura_after} "
                  f"(npc={npc.name} 距离 {max(abs(p.x - npc.x), abs(p.y - npc.y))})")
        else:
            self.supply_repaired.add(mi)
        self.set_status("补给 · 穿回修好的装备", now, force=True)
        # 修完立刻穿回（takeOnItem 会把它从背包挪回槽位）
        if hasattr(p, "bag_item") and p.bag_item(mi) is not None:
            srv._take_on_item(p.session, {"type": "takeOnItem",
                                          "slot": slot, "makeIndex": mi})
        self._maint_reset()
        return True

    def _maint_reset(self) -> None:
        self.maint_slot = -1
        self.maint_mi = 0
        self.maint_stage = ""

    def _maint_reclaim(self, srv, w, p) -> None:
        """兜底：中断（补给失败/结束/停机）时若装备还躺在背包，立刻穿回去。

        这是整套流程的最后一道保险。触发场景：修到一半补给失败转长冷却、
        状态超时熔断、停机结算——任何一种都可能让 ``maint_stage`` 停在 "on"
        而装备留在背包里。虽然背包里不会丢（死亡掉落只扫 ``equipment``），
        但「裸装去打怪」等于白挨打，所以这里尽量穿回；穿不回（等级/负重变了）
        就保持原样并清状态，至少装备还在玩家背包里。

        ⚠️ 放弃本件时要把它记进 ``supply_repaired``：否则同一件装备下一拍
        又被 ``_supply_maintain_equipped`` 选中 → 脱下 → reclaim 再穿回，
        在「能选到商人但走不过去」时形成每 tick 脱穿一次的活锁
        （2026-11-24 实测 12 分钟内脱穿 8 次、一次没修成）。
        """
        if not self.maint_stage:
            return
        slot, mi = self.maint_slot, self.maint_mi
        if 0 <= slot < len(p.equipment) and p.equipment[slot] is None \
                and hasattr(p, "bag_item") and p.bag_item(mi) is not None \
                and hasattr(srv, "_take_on_item"):
            try:
                srv._take_on_item(p.session, {"type": "takeOnItem",
                                              "slot": slot, "makeIndex": mi})
            except Exception as e:
                print(f"[afk] 穿回装备失败 {getattr(p, 'name', '?')}: "
                      f"{type(e).__name__}: {e}")
        if mi:
            self.supply_repaired.add(mi)    # 本轮不再反复脱穿这件
        self._maint_reset()

    def _supply_maintain(self, srv, w, p, now: int) -> bool:
        """做一件维护动作（修装备/拆药捆）。返回 True=本 tick 已处理，需下 tick 继续。"""
        srv_cfg = getattr(srv, "config", None)
        # 总开关沿用 afk.supplyEnabled；两项维护各自可关（默认开）。
        if not getattr(srv_cfg, "afk_supply_enabled", 1):
            return False
        do_repair = getattr(srv_cfg, "afk_supply_repair", 1)
        do_unbind = getattr(srv_cfg, "afk_supply_unbind", 1) and SUPPLY_UNBIND_WHEN_LOW

        cand = None
        mode = ""
        npc = None
        if do_repair:
            # 先按耐久筛出「想修的」，再逐件找**能修它**的商人（服装店有
            # [@repair] 但 item_types 只有服装，武器给它修不了）。
            for it in p.bag:
                mi0 = int(it.get("makeIndex", 0))
                if mi0 in self.supply_repaired or mi0 in self.supply_unsellable:
                    continue
                t0 = w.item_templates.get(int(it.get("index", 0)))
                if t0 is None or t0.stdMode in (0, 31):
                    continue
                dura0 = int(it.get("durability", 0) or 0)
                dmax0 = int(it.get("durabilityMax", 0) or 0)
                if dmax0 <= 0 or dura0 * 100 >= dmax0 * SUPPLY_REPAIR_DURA_PCT:
                    continue
                nn = self._supply_repair_npc(srv, w, p, it, t0)
                if nn is None:
                    self.supply_repaired.add(mi0)   # 没人能修这件，本轮跳过
                    continue
                cand, mode, npc = it, "repair", nn
                break
        if cand is None and do_unbind:
            found = self._supply_unbind_candidate(w, p)
            if found:
                cand, mode = found, "unbind"
        if cand is None:
            # 本轮做尽：清空维护集合（下轮补给重新评估）
            self.supply_repaired.clear()
            self.supply_unbound.clear()
            return False

        mi = int(cand.get("makeIndex", 0))
        if mode == "repair":
            # 修理必须靠近能修的 NPC（原版 ClientRepairItem 校验距离）。
            if npc is None:
                self.supply_repaired.add(mi)   # 没有修理商人：本轮跳过，避免每 tick 白找
                return True
            self.supply_npc_id = npc.id
            if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
                if self._step_to(srv, w, p, npc.x, npc.y, ("maint_npc", npc.id),
                                 f"补给 · 前往{npc.name}修理装备", now):
                    self.supply_leg_try = 0
                else:
                    self.supply_leg_try += 1
                    if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                        self.supply_repaired.add(mi)
                return True
        # 解捆是玩家双击物品，与 NPC 无关，就地进行（无需寻路）。
        if now - self.supply_last_act_at < SUPPLY_ACT_MS:
            return True
        self.supply_last_act_at = now
        if mode == "repair":
            dura_before = int(cand.get("durability", 0) or 0)
            srv._repair_item(p.session, {"type": "repairItem", "npcId": npc.id,
                                         "makeIndex": mi, "mode": "repair"})
            # 同样校验生效：耐久没涨说明被拒（价格 -1 / 金币不足 / 临时超距），
            # 别记成「已修」——否则同一件每轮都白试，还把金币账算错。
            after = p.bag_item(mi) if hasattr(p, "bag_item") else None
            if after is not None and int(after.get("durability", 0) or 0) > dura_before:
                self.supply_repaired.add(mi)
                self.set_status("补给 · 修理装备", now, force=True)
            else:
                self.supply_repaired.add(mi)   # 本轮不再重试，但日志留痕
                print(f"[afk] 背包修理未生效 {getattr(p, 'name', '?')} mi={mi} "
                      f"dura {dura_before}→{int(after.get('durability', 0) or 0) if after else '丢失'} "
                      f"(npc={npc.name})")
        else:
            srv._use_item(p.session, {"type": "useItem", "makeIndex": mi})
            self.supply_unbound.add(mi)
            self.set_status("补给 · 药水解捆", now, force=True)
        return True

    # ---- 买药 -----------------------------------------------------------

    def _supply_potion_target(self) -> int:
        """按剩余修炼分钟粗估买药数量：每 30 分钟 6 瓶，限 4~24。"""
        rem = max(0, int(self.remaining))
        return max(4, min(24, (rem + 29) // 30 * SUPPLY_POTIONS_PER_30MIN))

    def _supply_potion_offers(self, srv, w, p, want_mp: bool) -> list:
        """补给城内所有药水货源：[(npc, goods行, 模板)]，含血药（及蓝药）。"""
        offers = []
        for npc in w.npcs_by_map.get(p.map.name if p.map else "", ()):
            if not npc.merchant or npc.map is not p.map:
                continue
            for g in w.goods(npc):
                t = w.item_templates.get(int(g["index"]))
                if t is None or t.stdMode != 0:
                    continue
                if t.acMin > 0 or (want_mp and t.macMin > 0):
                    offers.append((npc, g, t))
        return offers

    def _supply_buy(self, srv, w, p, now: int) -> None:
        """按目标数量买血药（法师/道士另买蓝药），金币不够一瓶 → 熔断计失败。"""
        want_mp = p.job in (1, 2)
        offers = self._supply_potion_offers(srv, w, p, want_mp)
        hp_offers = [o for o in offers if o[2].acMin > 0]
        mp_offers = [o for o in offers if want_mp and o[2].macMin > 0]
        hp_have, mp_have = count_potions(w, p)
        target = self._supply_potion_target()
        if hp_have < target and hp_offers:
            offer = self._supply_pick_offer(w, p, hp_offers)
            if self._supply_buy_once(srv, w, p, now, offer, target - hp_have):
                return
        elif mp_have < target and mp_offers:
            offer = self._supply_pick_offer(w, p, mp_offers)
            if self._supply_buy_once(srv, w, p, now, offer, target - mp_have):
                return
        # 买满（或无可买）→ 出货判定后回锚点
        hp_have, mp_have = count_potions(w, p)
        if (hp_have > 0 or not hp_offers) and (mp_have > 0 or not mp_offers):
            self.supply_state = SUPPLY_GOTO_ANCHOR
            self.supply_since = now
            self.supply_route = []
            self.supply_npc_id = 0
            self.supply_leg_try = 0
            self.set_status(f"补给 · 采购完成（血药{hp_have} 蓝药{mp_have}），返回锚点",
                            now, force=True)
            return
        self._supply_fail(srv, w, p, now, "补给城没有可买的药水")

    def _supply_pick_offer(self, w, p, offers):
        """挑最便宜、其次最近的货源；锁定已选商人，避免玩家移动导致来回换目标。"""
        cached = w.npc(self.supply_npc_id)
        if cached is not None:
            for offer in offers:
                if offer[0] is cached:
                    return offer
        best = min(offers, key=lambda o: (o[1]["price"],
                                          max(abs(p.x - o[0].x), abs(p.y - o[0].y))))
        self.supply_npc_id = best[0].id
        return best

    def _supply_buy_once(self, srv, w, p, now: int, offer, need: int) -> bool:
        """向 offer 的商人买 need 瓶；返回 True = 已下单/已处理（本 tick 结束）。"""
        npc, g, t = offer
        self.supply_npc_id = npc.id
        if max(abs(p.x - npc.x), abs(p.y - npc.y)) > 12:
            if self._step_to(srv, w, p, npc.x, npc.y, ("supply_npc", npc.id),
                             f"补给 · 前往{npc.name}", now):
                self.supply_leg_try = 0
            else:
                self.supply_leg_try += 1
                self.unreachable.pop(npc.id, None)
                if self.supply_leg_try >= SUPPLY_LEG_RETRY:
                    self._supply_fail(srv, w, p, now, f"无法走近商人 {npc.name}")
            return True
        if now - self.supply_last_act_at < SUPPLY_ACT_MS:
            return True
        self.supply_last_act_at = now
        space = BAG_SIZE - len(p.bag)
        affordable = p.gold // max(1, int(g["price"]))
        n = max(0, min(need, space, affordable))
        if n <= 0:
            if space <= 0:
                self._supply_fail(srv, w, p, now, "背包已满，无法购买药水")
            else:
                self._supply_fail(srv, w, p, now,
                                  f"金币不足无法补给（需 {g['price']} 金币/瓶，"
                                  f"持有 {p.gold}）")
            return True
        srv._buy_item(p.session, {"type": "buyItem", "npcId": npc.id,
                                  "index": int(g["index"]), "count": n})
        return True

    # ---- 补给收尾 / 失败 -------------------------------------------------

    def _supply_finish(self, srv, w, p, now: int) -> None:
        # 中断兜底：若装备还躺在背包（修理流程没走完），先穿回去再收尾
        self._maint_reclaim(srv, w, p)
        self.supply_state = ""
        self.supply_fails = 0
        self.supply_return_tries = 0
        self.supply_route = []
        self.manager._log_event(self, p, "supply_end", seq=self.log_supplies,
                                ok=True, map=p.map.name if p.map else "",
                                gold=p.gold, bag=len(p.bag),
                                hp=p.hp, maxHp=p.max_hp,
                                potions=list(count_potions(w, p)),
                                remaining=self.remaining)
        # 回锚点了：刷新进展快照，避免被零进展熔断误杀
        self.last_exp, self.last_bag, self.last_gold = p.exp, len(p.bag), p.gold
        self.last_progress_at = now
        # 关键：3 分钟冷却，防止贴商人的锚点导致「卖完立刻又补给」死循环
        self.supply_retry_until = now + SUPPLY_FINISH_COOLDOWN_MS
        # 「装备耐久低」单独触发补给时，如果这趟没修成（补给城无修理商等），
        # 也要给长冷却，否则会每轮补给都因同一条理由重跑，退化成空转。
        if self._gear_needs_supply(w, p):
            self.gear_retry_until = now + SUPPLY_LONG_RETRY_MS
        self.set_status("补给完成 · 继续修炼", now, force=True)

    def _supply_fail(self, srv, w, p, now: int, why: str) -> None:
        """补给失败：退了再试，**不轻易停机**（长跑挂机最怕一次小故障全废）。

        失败计数达 ``afk.supplyMaxFails``（默认 5，0=不限）后转为 30 分钟长冷却，
        期间照常打怪；只有在「身上一瓶药都没有且血量见底」时才熔断，避免
        无药送死。稳定升级 15 分钟后失败计数自动清零（`_supply_check_trigger`）。

        另外：失败时如果人在**中转地图**（不是出发时的挂机锚点），先走回
        锚点再恢复打怪——中转图常常一只怪都没有，原地待命会被「长时间
        没有经验」熔断，这是模拟里 20 分钟就停机的主因之一。
        """
        self.supply_fails += 1
        self.supply_last_fail_at = now
        # 中断兜底：修理到一半就失败时，装备还躺在背包，先穿回去再说
        self._maint_reclaim(srv, w, p)
        self.manager._log_event(self, p, "supply_fail", why=why,
                                seq=self.log_supplies, fails=self.supply_fails,
                                state=self.supply_state,
                                map=p.map.name if p.map else "", x=p.x, y=p.y,
                                anchorMap=self.anchor_map,
                                gold=p.gold, bag=len(p.bag),
                                hp=p.hp, maxHp=p.max_hp,
                                potions=list(count_potions(w, p)))
        max_fails = max(0, int(getattr(srv.config, "afk_supply_max_fails", 5)))
        if max_fails and self.supply_fails >= max_fails:
            hp, _mp = count_potions(w, p)
            if hp == 0 and p.hp <= max(1, p.max_hp) * 0.3:
                self.manager.disable(p, f"连续 {self.supply_fails} 次补给失败"
                                        f"（{why}）且无药可喝，离线修炼已停止")
                return
            self.supply_fails = 0
            backoff = SUPPLY_LONG_RETRY_MS
            self.set_status(f"补给失败（{why}），继续打怪，30 分钟后再试",
                            now, force=True)
        else:
            backoff = SUPPLY_RETRY_MS * min(8, 2 ** max(0, self.supply_fails - 1))
            self.set_status(f"补给失败（{why}），{backoff // 60_000} 分钟后重试",
                            now, force=True)
        self.supply_retry_until = now + backoff
        on_anchor = (not self.anchor_map or p.map is None
                     or p.map.name == self.anchor_map)
        if not on_anchor and self.supply_return_tries < 4:
            self.supply_return_tries += 1
            self.supply_state = SUPPLY_GOTO_ANCHOR
            self.supply_since = now
            self.supply_route = []
            self.supply_leg_try = 0
            self.set_status(f"补给失败（{why}），先返回挂机点 {self.anchor_map}",
                            now, force=True)
            return
        self.supply_return_tries = 0
        self.supply_state = ""
        self.supply_route = []
        self.last_exp, self.last_bag, self.last_gold = p.exp, len(p.bag), p.gold
        self.last_progress_at = now


class OfflineAgentManager:
    """离线修炼代理管理器。接口面与 AutoPilotManager 对齐（AutoPilot.tick 会调
    ``manager.push/discard/disable/handle_death``），由 ``service.tick`` 驱动。"""

    def __init__(self, service: Any):
        self.service = service
        self._pilots: dict[int, OfflinePilot] = {}
        self._watchers: dict[int, set[int]] = {}   # 代理 id → 观战 GM 的 player_id
        self._last_charge_at = 0      # 分钟记账节拍
        self._last_persist_at = 0     # 周期落库节拍（降低宕机损失）
        # 装备耐久耗尽「爆掉」→ 挂机日志（用户 2026-11-25：首饰掉太快，
        # 需要实证到底是哪件、什么时候爆的）。world 侧只在有回调时调用。
        service.world.on_item_break = self._on_item_break

    def _on_item_break(self, p, slot: int, it: dict) -> None:
        pilot = self._pilots.get(getattr(p, "id", -1))
        if pilot is None or pilot.log is None:
            return                      # 不在线挂机的玩家（在线托管/手动）不记
        w = self.service.world
        t = getattr(w, "item_templates", {}).get(int(it.get("index", 0) or 0))
        self._log_event(pilot, p, "item_broken",
                        slot=slot, slotName=SLOT_NAMES[slot] if slot < len(SLOT_NAMES) else str(slot),
                        item=t.name if t else it.get("name", "?"),
                        duraMax=int(it.get("durabilityMax", 0) or 0),
                        map=p.map.name if p.map else "", x=p.x, y=p.y,
                        deaths=pilot.deaths, supply=pilot.supply_state)

    # ---- 运行日志（pyserver/afk_log.py）--------------------------------

    def _log_config(self) -> tuple[bool, Any, int]:
        cfg = self.service.config
        enabled = bool(int(getattr(cfg, "afk_log_enabled", 1)))
        log_dir = getattr(cfg, "afk_log_dir", None)
        if log_dir is None:
            log_dir = self.service.config.root / "data" / "afk-logs"
        interval = max(0, int(getattr(cfg, "afk_log_sample_minutes", 1)))
        return enabled, log_dir, interval

    def _open_log(self, p):
        """为一次修炼会话开日志；关闭或打开失败返回 None（挂机照常跑）。"""
        enabled, log_dir, _ = self._log_config()
        if not enabled:
            return None
        try:
            return AfkSessionLog(log_dir,
                                 getattr(p, "name", "") or "",
                                 getattr(p, "character_id", 0) or 0)
        except Exception as e:
            print(f"[afk] 日志打开失败 {getattr(p, 'name', '?')}: "
                  f"{type(e).__name__}: {e}")
            return None

    def _log_reject(self, p, stage: str, reason: str, cur_map: str = "",
                    plan: dict | None = None) -> None:
        """开启/续挂被拒时记一条 reject。

        ``stage`` 标明拒绝发生在哪个环节（request_start / promote / resume），
        日后可以按 reason 聚合出「哪道门槛最常挡住玩家」，作为调整
        ``afk.minLevel`` / ``excludedMaps`` / 每日额度的依据。
        被拒时没有存活会话，这里开一个一次性写入器、写完即关。
        """
        enabled, log_dir, _ = self._log_config()
        if not enabled:
            return
        try:
            log = AfkSessionLog(log_dir, getattr(p, "name", "") or "",
                                getattr(p, "character_id", 0) or 0)
            log.reject(now_ms(), stage, reason, map=cur_map or "",
                       level=getattr(p, "level", 0) or 0,
                       plan=dict(plan) if plan else None)
            log.close()
        except Exception:
            pass          # 日志问题绝不能反过来影响拒绝流程本身


    # ---- 接口面（AutoPilot 决策循环会调用） -----------------------------

    def get(self, p) -> OfflinePilot | None:
        return self._pilots.get(p.id) if p is not None else None

    def discard(self, player_id: int) -> None:
        """决策循环发现角色不在世界（已被移除）——收尾清 pilot。"""
        pilot = self._pilots.pop(player_id, None)
        if pilot is not None:
            pilot.active = False
            # 角色已不在世界，拿不到 p，只能直接关文件避免句柄泄漏
            self._log_close(pilot)

    def push(self, player_id: int, now: int, force: bool = False) -> None:
        """离线没有客户端可推，只同步 pushed_status 避免重复字符串比较；
        有 GM 观战时把状态文案实时转发过去（走 set_status 的节流，不会刷屏）。"""
        pilot = self._pilots.get(player_id)
        if pilot is not None:
            pilot.pushed_status = pilot.status
            pilot.last_state_at = now
            self._notify_watchers(player_id, pilot.status)

    # ---- GM 观战 --------------------------------------------------------

    def _notify_watchers(self, player_id: int, text: str) -> None:
        """把一条动态推给该代理的所有观战 GM；GM 已下线的顺手清掉。"""
        gids = self._watchers.get(player_id)
        if not gids:
            return
        w = self.service.world
        p = w.actor(player_id)
        prefix = f"[观战] {p.name if p is not None else '?'}: "
        for gid in list(gids):
            g = next((q for q in w.players() if q.id == gid), None)
            if g is None:
                gids.discard(gid)
            else:
                w.send_system(g, prefix + text)
        if not gids:
            self._watchers.pop(player_id, None)

    def watch_start(self, gm, name: str) -> str:
        """GM 开始实时跟踪一个离线代理的状态流。"""
        p = self.service.world.player(name)
        if p is None or p.id not in self._pilots:
            return f"角色 {name} 当前不在离线修炼中（@离线 list 查看）。"
        self._watchers.setdefault(p.id, set()).add(gm.id)
        return f"开始观战 {name}：{self._pilots[p.id].status}\n（再发一次 @离线 watch 停止）"

    def watch_stop(self, gm) -> str:
        stopped: list[str] = []
        for pid, gids in list(self._watchers.items()):
            if gm.id not in gids:
                continue
            gids.discard(gm.id)
            if not gids:
                self._watchers.pop(pid, None)
            a = self.service.world.actor(pid)
            stopped.append(a.name if a is not None else "?")
        if not stopped:
            return "当前没有观战中的离线代理。"
        return f"已停止观战：{'、'.join(stopped)}。"

    def push_state(self, p, preset: str | None = None, reason: str = "",
                   ok: bool = True) -> None:
        pass

    def disable(self, p, reason: str) -> None:
        """停机入口（决策循环熔断 / 玩家上线接管都汇到这里）。"""
        pilot = self._pilots.pop(p.id, None) if p is not None else None
        if pilot is None:
            return
        pilot.active = False
        pilot.reason = reason
        pilot.status = "已停止"
        # 停机兜底：装备若还躺在背包（修理流程被打断），结算前先穿回去，
        # 免得玩家下次登录看到自己「裸装」站着
        try:
            w2 = self.service.world
            # GameWorld.players 是**方法**（返回所有在线玩家列表），不是字典；
            # 要按 id 取就用 actor(id)。之前写成 w2.players.get(...) 会抛
            # AttributeError，被下面 except 吞掉 → 兜底穿回形同不存在。
            p2 = w2.actor(p.id) or p
            if p2 is not None:
                pilot._maint_reclaim(self.service, w2, p2)
        except Exception as e:
            print(f"[afk] 停机身装备兜底失败 id={getattr(p, 'id', '?')}: "
                  f"{type(e).__name__}: {e}")
        self._notify_watchers(p.id, f"已停止：{reason}")
        self._watchers.pop(p.id, None)
        pilot.on_disabled(reason)
        self._settle(p, pilot, reason)

    def handle_death(self, p, pilot: OfflinePilot) -> None:
        """死亡处理（已拍板：自动复活 + 连死熔断）。

        掉落已在 ``world.kill_player`` 里按原版结算；这里把角色按登录复活的
        同一规则（UsrEngn.pas:576-600：回出生点 ±2、HP=14）原地拉起继续挂。
        """
        cfg = self.service.config
        pilot.deaths += 1
        self._log_event(pilot, p, "death", deaths=pilot.deaths,
                        limit=max(1, int(cfg.afk_death_stop)),
                        level=p.level,
                        map=p.map.name if p.map else "", x=p.x, y=p.y,
                        hp=p.hp, maxHp=p.max_hp,
                        supply=pilot.supply_state, remaining=pilot.remaining)
        if pilot.deaths >= max(1, int(cfg.afk_death_stop)):
            self.disable(p, f"死亡 {pilot.deaths} 次，离线修炼已停止（防止药尽送死）")
            return
        w = self.service.world
        if p.pk_point >= RED_NAME_PK_POINT:
            home = w.get_or_load_map("3") or w.default_map
            hx, hy = 845, 674
        else:
            home = w.get_or_load_map(p.home_map) or w.default_map
            hx, hy = p.home_x, p.home_y
        x = max(0, min(home.width - 1, hx - 2 + (p.id % 5)))
        y = max(0, min(home.height - 1, hy - 2 + (p.id // 5 % 5)))
        p.dead = False
        p.alive = True
        p.poison_ticks = 0
        p.inc_health = p.inc_spell = 0
        p.target_id = -1
        if w.space_move(p, home.name, x, y) is not None:
            w.broadcast_entity_add(p)
        p.hp, p.mp = max(14, p.max_hp // 10), 0
        p.recompute(w)
        # 复活后重置熔断窗口：死亡结算本身不算「无进展」。
        pilot.last_exp = p.exp
        pilot.last_bag = len(p.bag)
        pilot.last_gold = p.gold
        pilot.last_progress_at = now_ms()
        pilot.path.clear()
        pilot.path_key = ()
        pilot.plan = None
        pilot.target_id = 0
        pilot.loot_id = 0
        pilot.loot_until = 0
        pilot.unreachable.clear()
        pilot.kite_until = 0
        pilot.kite_target_id = 0
        # 补给被死亡打断：路线作废，从复活点重算。卖/买中断回退到赶路状态
        # （复活多半已在城内，GOTO_TOWN 立即到达，接着卖/买）；赶路状态保持，
        # 状态机本来就会按当前位置重新规划门序列。
        if pilot.supply_state:
            pilot.supply_route = []
            pilot.supply_leg_try = 0
            pilot.supply_npc_id = 0
            if pilot.supply_state in (SUPPLY_SELL, SUPPLY_BUY):
                pilot.supply_state = SUPPLY_GOTO_TOWN
                pilot.supply_since = now_ms()
        pilot.set_status(f"死亡复活 · 剩余 {pilot.deaths}/{cfg.afk_death_stop} 次", now_ms(), force=True)

    # ---- 在线配置入口（WS `afk` 消息） ---------------------------------

    def request_start(self, p, minutes: int, preset: str) -> tuple[bool, str, dict]:
        """在线时配置离线修炼计划；下线瞬间生效。"""
        cfg = self.service.config
        info = self.state_info(p)
        ok, why = self._validate_start(p, info, minutes)
        if not ok:
            self._log_reject(p, "request_start", why,
                             p.map.name if p.map is not None else "")
            return False, why, info
        minutes = int(minutes)
        key = normalize_preset(preset)
        clipped = min(minutes, info["balance"], info["dailyRemaining"])
        p.afk_plan = {"minutes": clipped, "preset": key}
        info = self.state_info(p)
        note = ""
        if clipped < minutes:
            note = f"（原计划 {minutes} 分钟，受余额/今日额度限制缩减）"
        return True, (f"已设定离线修炼计划：下线后自动修炼 {clipped} 分钟"
                      f"（{PRESETS[key]['name']}档）{note}。"), info

    def _validate_start(self, p, info: dict, minutes: int) -> tuple[bool, str]:
        """在线配置阶段的全部门禁；返回 (ok, 拒绝原因)。

        抽成独立方法是为了让 ``request_start`` 只在返回原因处写一次日志，
        不用在六个 return 分支上各贴一行 ``_log_reject``。
        """
        cfg = self.service.config
        cur_map = p.map.name if p.map is not None else ""
        if p.map is None or cur_map in cfg.afk_excluded_maps:
            return False, (f"当前地图（{cur_map or '未知'}）禁止离线修炼，"
                           f"请换到允许的地图。")
        if p.level < cfg.afk_min_level:
            return False, (f"等级不足：当前 {p.level} 级，"
                           f"离线修炼需要 {cfg.afk_min_level} 级以上。")
        if getattr(p, "dead", False) or not p.alive:
            return False, "角色已死亡，无法开启离线修炼。"
        if int(minutes) <= 0:
            return False, "修炼时长无效（计划时长必须大于 0）。"
        balance = info["balance"]
        if balance < 10:
            return False, (f"修炼时长余额不足：仅剩 {balance} 分钟（至少 10 分钟），"
                           f"请找比奇城的玛法领路人领取。")
        if info["dailyRemaining"] < 10:
            return False, (f"今日修炼额度已用完：已用 {info['usedToday']}/"
                           f"{info['dailyLimit']} 分钟，明天再来。")
        if min(int(minutes), balance, info["dailyRemaining"]) < 10:
            # 计划值本身够 10 分钟，这里只可能是余额/额度把它压下来了
            return False, (f"可用时长不足 10 分钟：余额 {balance} 分钟、"
                           f"今日剩 {info['dailyRemaining']} 分钟。")
        return True, ""

    def request_stop(self, p) -> tuple[bool, str, dict]:
        if getattr(p, "afk_plan", None) is None:
            return False, "没有待生效的离线修炼计划。", self.state_info(p)
        p.afk_plan = None
        return True, "已取消离线修炼计划。", self.state_info(p)

    def state_info(self, p) -> dict:
        cfg = self.service.config
        db = self.service.db
        afk = db.get_afk(p.character_id)
        used_today = afk["used_minutes"] if afk["used_date"] == today() else 0
        balance = afk["balance"]
        claimable = (afk["claim_count"] < cfg.afk_claim_per_day
                     or afk["claim_date"] != today())
        plan = getattr(p, "afk_plan", None)
        pilot = self._pilots.get(p.id)
        return {
            "balance": balance,
            "claimToday": afk["claim_count"] if afk["claim_date"] == today() else 0,
            "claimPerDay": cfg.afk_claim_per_day,
            "claimable": claimable,
            "claimMinutes": cfg.afk_claim_minutes,
            "poolCap": cfg.afk_pool_cap_minutes,
            "usedToday": used_today,
            "dailyLimit": cfg.afk_daily_minutes,
            "dailyRemaining": max(0, cfg.afk_daily_minutes - used_today),
            "minLevel": cfg.afk_min_level,
            "excludedMaps": list(cfg.afk_excluded_maps),
            "maxAgents": cfg.afk_max_agents,
            "perMapLimit": cfg.afk_per_map_limit,
            "running": len(self._pilots),
            "plan": dict(plan) if plan else None,
            "active": bool(pilot),
        }

    # ---- 下线接管 / 登录交还 -------------------------------------------

    def promote(self, p) -> bool:
        """下线瞬间接管：配置了计划的角色留在世界继续打怪。

        返回 True 表示已接管（调用方跳过 persist/remove_player）。
        门禁在下线时复检：玩家可能断线前换到了黑名单图 / 等级被重置。
        """
        plan = getattr(p, "afk_plan", None)
        if not plan:
            return False
        p.afk_plan = None
        cfg = self.service.config
        reason = ""
        # 每条拒绝都要写清「卡在哪、差多少、上限多少」，玩家看报告才知道怎么改。
        # 旧文案只有「等级低于门槛」「名额已满」这类结论，玩家无法判断要升多少级、
        # 是哪个名额满了，实测反馈「拒绝原因没写好」。
        cur_map = p.map.name if p.map is not None else ""
        if p.map is None or cur_map in cfg.afk_excluded_maps:
            reason = (f"下线时所在地图（{cur_map or '未知'}）禁止离线修炼，"
                      f"请换到允许的地图再下线")
        elif p.level < cfg.afk_min_level:
            reason = (f"等级不足：当前 {p.level} 级，离线修炼要求 "
                      f"{cfg.afk_min_level} 级")
        elif len(self._pilots) >= cfg.afk_max_agents:
            reason = (f"全服离线修炼名额已满：{len(self._pilots)}/"
                      f"{cfg.afk_max_agents}，稍后再试")
        elif sum(1 for q in self._pilots.values()
                 if q and self._pilot_map(q) == cur_map) >= cfg.afk_per_map_limit:
            reason = (f"当前地图（{cur_map}）名额已满："
                      f"{cfg.afk_per_map_limit} 个上限，换张地图或稍后再试")
        minutes = int(plan.get("minutes", 0))
        afk = self.service.db.get_afk(p.character_id)
        used_today = afk["used_minutes"] if afk["used_date"] == today() else 0
        daily_left = max(0, cfg.afk_daily_minutes - used_today)
        minutes = min(minutes, afk["balance"], daily_left)
        if not reason and minutes < 10:
            # 三个上限到底撞了哪个：余额 / 每日额度 / 计划值。旧实现统一报
            # 「可用修炼时长不足 10 分钟」，玩家分不清是该领取还是等明天。
            if afk["balance"] < 10:
                reason = (f"修炼时长余额不足：仅剩 {afk['balance']} 分钟，"
                          f"请找比奇城的玛法领路人领取")
            elif daily_left < 10:
                reason = (f"今日修炼额度已用完：已用 {used_today}/"
                          f"{cfg.afk_daily_minutes} 分钟，明天再来")
            else:
                reason = (f"可用时长不足 10 分钟：余额 {afk['balance']}、"
                          f"今日剩 {daily_left}、计划 {int(plan.get('minutes', 0))}")
        if reason:
            self._report(p.character_id, now_ms(), now_ms(), 0, 0, 0, 0, 0,
                         f"离线修炼未能开启：{reason}",
                         notice="未开始的计划不扣除时长，可重新设置")
            # 拒绝原因入日志：日后可统计「哪类拒绝最多」（等级/地图/名额/时长），
            # 作为调整门槛的依据。
            self._log_reject(p, "promote", reason, cur_map, plan)
            return False
        # 预扣余额 → 会话落库 → 假会话顶替真会话 → 代理开跑
        db = self.service.db
        db.save_afk(p.character_id, afk["balance"] - minutes,
                    afk["claim_date"], afk["claim_count"], afk["used_date"],
                    afk["used_minutes"])
        db.start_afk_session(p.character_id, p.map.name, p.x, p.y,
                             plan.get("preset", DEFAULT_PRESET), minutes)
        p.session = NullSession(p.account_id, "")
        p.session.player_name = p.name
        p.afk_offline = True
        pilot = OfflinePilot(self, p.id, preset=plan.get("preset", DEFAULT_PRESET),
                             started_at=now_ms(),
                             exp_start=p.exp, level_start=p.level,
                             max_exp_start=p.max_exp, gold_start=p.gold,
                             last_exp=p.exp, last_bag=len(p.bag),
                             last_gold=p.gold, last_progress_at=now_ms(),
                             remaining=minutes)
        pilot.log = self._open_log(p)
        pilot.log_at = pilot.log_exp = pilot.log_kills = pilot.log_gold = 0
        self._log_event(pilot, p, "session_start",
                        preset=pilot.preset, plan=minutes,
                        level=p.level, exp=p.exp, gold=p.gold,
                        hp=p.hp, maxHp=p.max_hp,
                        map=p.map.name if p.map else "", x=p.x, y=p.y,
                        bag=len(p.bag))
        self._pilots[p.id] = pilot
        return True

    def takeover(self, name: str) -> bool:
        """玩家上线接管：结算正在运行的离线会话并移除离线角色。

        返回 True = 原本在线（离线代理），已清理，调用方可正常进游戏。
        """
        p = self.service.world.player(name)
        if p is None or not getattr(p, "afk_offline", False):
            return False
        pilot = self._pilots.get(p.id)
        # 玩家主动上线是**正常收工**，不是失败。reason 写「结束原因」供报告
        # 显示；旧文案「玩家上线，离线修炼结束」读起来像被拒绝，也导致
        # 结算报告里 minutes>0 的正常记录被误认为是异常。
        reason = "角色上线，本次修炼正常结束"
        if pilot is not None:
            self._pilots.pop(p.id, None)
            pilot.active = False
            pilot.reason = reason
            self._settle(p, pilot, reason)
        else:
            self._settle_row_only(p)
        return True

    # ---- 登录下发报告 ---------------------------------------------------

    def on_login(self, p) -> None:
        db = self.service.db
        reports = db.pending_afk_reports(p.character_id)
        if not reports:
            return
        db.mark_afk_reports_delivered(p.character_id)
        for rep in reports:
            minutes = int(rep["minutes"])
            if minutes <= 0:
                text = f"[离线修炼] {rep['notice'] or rep['reason']}"
            else:
                text = (f"[离线修炼] 本次修炼 {minutes} 分钟："
                        f"经验 +{rep['exp_gained']}，击杀 {rep['kills']}，"
                        f"金币 {'+' if rep['gold_delta'] >= 0 else ''}{rep['gold_delta']}，"
                        f"死亡 {rep['deaths']} 次。{rep['notice'] or rep['reason']}")
            self.service.world.send_system(p, text)
        p.session.send_json({"type": "afkReport",
                             "reports": [{k: rep[k] for k in
                                          ("started_at", "ended_at", "minutes",
                                           "exp_gained", "kills", "gold_delta",
                                           "deaths", "reason", "notice")}
                                         for rep in reports]})

    # ---- tick：分钟记账 / 周期落库 -------------------------------------

    def tick(self, now: int) -> None:
        if not self._pilots:
            return
        for pilot in list(self._pilots.values()):
            try:
                pilot.tick(now)
            except Exception as e:
                print(f"[afk] tick 异常 id={pilot.player_id}: {type(e).__name__}: {e}")
                self._log_event(pilot, None, "error",
                                where="tick", type=type(e).__name__,
                                msg=str(e), status=pilot.status,
                                supply=pilot.supply_state,
                                remaining=pilot.remaining)
                self.discard(pilot.player_id)
        if now - self._last_charge_at < 60_000:
            return
        self._last_charge_at = now
        db = self.service.db
        for cid, pilot in list(self._pilots.items()):
            # 退回时间已记账在 settle；这里只递减剩余分钟（服务端停机期间不扣）。
            p = self.service.world.actor(pilot.player_id)
            if p is None:
                continue
            remaining = pilot.remaining - 1
            pilot.remaining = remaining
            db.update_afk_remaining(p.character_id, remaining, pilot.deaths,
                                    pilot.restart_bonus)
            # 运行日志采样：默认每分钟一条（afk.logSampleMinutes 可配，0=关）。
            _, _, sample_min = self._log_config()
            if sample_min and (not pilot.log_at
                               or now - pilot.log_at >= sample_min * 60_000):
                self._log_sample(pilot, p, self.service.world)
            if self._watchers.get(pilot.player_id):
                # 观战心跳：战斗平稳、状态不变时也能确认代理活着。
                self._notify_watchers(
                    pilot.player_id,
                    f"{p.map.name if p.map else '?'}({p.x},{p.y}) "
                    f"HP {p.hp}/{p.max_hp} 剩{remaining}分钟")
            if remaining <= 0:
                self.disable(p, "修炼时长已用完，离线修炼结束")
        if now - self._last_persist_at < 300_000:
            return
        self._last_persist_at = now
        for cid, pilot in list(self._pilots.items()):
            p = self.service.world.actor(pilot.player_id)
            if p is not None:
                self.service.persist(p)

    # ---- 重启续挂（含补偿） --------------------------------------------

    def resume_from_db(self) -> None:
        """服务端启动时拉起所有运行中的离线会话，并补偿重启损失。"""
        db = self.service.db
        bonus = int(self.service.config.afk_restart_bonus_minutes)
        for row in db.running_afk_sessions():
            rec = db.find_character_by_id(row["character_id"])
            if rec is None:
                db.settle_afk_session(row["character_id"], "orphan")
                continue
            # 真人已经在线（不可能，服务端刚启动）→ 跳过；保险起见查一下。
            if self.service.world.player(rec["name"]) is not None:
                continue
            remaining = int(row["remaining_minutes"]) + bonus
            ns = NullSession(rec["account_id"], "")
            try:
                self.service._do_enter(ns, rec["name"])
            except Exception as e:
                print(f"[afk] 续挂进图失败 {rec['name']}: {type(e).__name__}: {e}")
                db.settle_afk_session(row["character_id"], "resume_failed")
                continue
            p = self.service.world.player(rec["name"])
            if p is None:
                db.settle_afk_session(row["character_id"], "resume_failed")
                continue
            p.afk_offline = True
            pilot = OfflinePilot(self, p.id, preset=normalize_preset(row["preset"]),
                                 started_at=int(row["started_at"]),
                                 exp_start=p.exp, level_start=p.level,
                                 max_exp_start=p.max_exp, gold_start=p.gold,
                                 last_exp=p.exp, last_bag=len(p.bag),
                                 last_gold=p.gold, last_progress_at=now_ms(),
                                 restart_bonus=bonus, deaths=int(row["deaths"]))
            pilot.remaining = remaining
            db.update_afk_remaining(p.character_id, remaining, pilot.deaths, bonus)
            pilot.log = self._open_log(p)
            self._log_event(pilot, p, "session_start", preset=pilot.preset,
                            resumed=True, plan=remaining, bonus=bonus,
                            level=p.level, exp=p.exp, gold=p.gold,
                            hp=p.hp, maxHp=p.max_hp,
                            map=p.map.name if p.map else "", x=p.x, y=p.y,
                            bag=len(p.bag))
            self._pilots[p.id] = pilot
            notice = (f"服务器重启，离线修炼已自动续挂（剩余 {remaining} 分钟，"
                      f"含补偿 {bonus} 分钟）。") if bonus > 0 else \
                     f"服务器重启，离线修炼已自动续挂（剩余 {remaining} 分钟）。"
            db.insert_afk_report(p.character_id, int(row["started_at"]),
                                 now_ms(), 0, 0, 0, 0, 0, "服务器重启自动续挂", notice)
            print(f"[afk] 已续挂 {rec['name']}：剩余 {remaining} 分钟（补偿 {bonus}）")

    # ---- 内部 -----------------------------------------------------------

    def _pilot_map(self, pilot: OfflinePilot) -> str:
        p = self.service.world.actor(pilot.player_id)
        return p.map.name if p is not None and p.map else ""

    @staticmethod
    def _remaining_of(pilot: OfflinePilot) -> int:
        return int(pilot.remaining)

    def _exp_gained(self, w, pilot: OfflinePilot, p) -> int:
        """经验增量：本级经验 + 跨级的整级经验补齐。

        ``add_exp`` 升级时 ``Exp -= MaxExp``，本级经验不是累计值；跨级时把
        起始级剩余 + 途经整级 + 结束级已有，逐段用 ``exp_for_level`` 补齐。
        """
        from .world import exp_for_level
        if p.level == pilot.level_start:
            return max(0, p.exp - pilot.exp_start)
        if p.level < pilot.level_start:      # 不可能（经验不减级），防御
            return 0
        gained = max(0, pilot.max_exp_start - pilot.exp_start)
        for lv in range(pilot.level_start + 1, p.level):
            gained += exp_for_level(lv)
        gained += p.exp
        return gained

    def _settle(self, p, pilot: OfflinePilot, reason: str) -> None:
        """结算：退回余额、写报告、持久化角色、把角色移出世界。"""
        db = self.service.db
        afk = db.get_afk(p.character_id)
        remaining = max(0, self._remaining_of(pilot))
        planned = 0
        try:
            row = db.settle_afk_session(p.character_id)
            if row is not None:
                planned = int(row["planned_minutes"])
                remaining = min(remaining, planned)
        except Exception:
            remaining = 0
        consumed = max(0, planned - remaining)
        exp_gained = self._exp_gained(self.service.world, pilot, p)
        gold_delta = p.gold - pilot.gold_start
        # 结算收尾先记日志再动世界状态：remove_player 之后 p.map 可能已失效
        self._log_event(pilot, p, "session_end", reason=reason,
                        planned=planned, consumed=consumed,
                        remaining=remaining, expGained=exp_gained,
                        kills=pilot.kills, goldDelta=gold_delta,
                        deaths=pilot.deaths, level=p.level,
                        exp=p.exp, gold=p.gold,
                        map=p.map.name if p.map else "", x=p.x, y=p.y,
                        bag=len(p.bag), supplies=pilot.log_supplies,
                        errors=pilot.log_errors, restartBonus=pilot.restart_bonus)
        self._log_close(pilot)
        # 退回未用完的余额
        used_date = afk["used_date"] if afk["used_date"] == today() else today()
        used_minutes = (afk["used_minutes"] if afk["used_date"] == used_date else 0) + consumed
        db.save_afk(p.character_id, afk["balance"] + remaining,
                    afk["claim_date"], afk["claim_count"], used_date, used_minutes)
        self._report(p.character_id, pilot.started_at, now_ms(), consumed,
                     exp_gained, pilot.kills, gold_delta, pilot.deaths,
                     reason,
                     notice=(f"（含重启补偿 {pilot.restart_bonus} 分钟）"
                             if pilot.restart_bonus else ""))
        self.service.persist(p)
        self.service.world.remove_player(p)
        self.service.world.broadcast_around(
            p.map, {"type": "entityRemove", "id": p.id}, p.x, p.y,
            getattr(self.service.world, "SIGHT_RANGE", 12))
        p.afk_offline = False

    def _settle_row_only(self, p) -> None:
        """无 pilot（重启续挂后 pilot 丢失等边界）也要收掉会话行。"""
        try:
            self.service.db.settle_afk_session(p.character_id)
        except Exception:
            pass

    def _log_event(self, pilot: OfflinePilot, p, kind: str, **fields: Any) -> None:
        """安全地把一条事件写进会话日志；任何异常都吞掉（不影响挂机）。

        ``p`` 允许为 None（tick 异常那条路径角色对象可能已不可用）。
        """
        log = pilot.log
        if log is None:
            return
        try:
            log.event(kind, now_ms(), **fields)
        except Exception as e:
            pilot.log_errors += 1
            print(f"[afk] 日志写入失败 id={pilot.player_id}: "
                  f"{type(e).__name__}: {e}")

    def _log_close(self, pilot: OfflinePilot) -> None:
        log = pilot.log
        pilot.log = None
        if log is None:
            return
        try:
            log.close()
        except Exception:
            pass

    def _log_sample(self, pilot: OfflinePilot, p, w) -> None:
        """周期采样：经验/击杀/金币/等级/HP/地图/补给状态。

        字段设计目标是「事后能还原这轮挂机的运行曲线」：
        ``dExp``/``dKills``/``dGold`` 是**区间增量**（两次采样之间），
        直接聚合就能算每分钟收益；``supply``/``hpPct``/``bag`` 是瞬时状态。
        击杀明细（哪只怪、什么等级）不在这里记——每分钟一条足够定位
        「这轮为什么只打 55 只怪」，逐只记会让日志体积翻十倍。
        """
        if pilot.log is None:
            return
        exp_now = self._exp_gained(w, pilot, p)
        hp_pct = int(p.hp * 100 / max(1, p.max_hp))
        self._log_event(
            pilot, p, "sample",
            level=p.level, exp=exp_now,
            dExp=max(0, exp_now - pilot.log_exp),
            kills=pilot.kills, dKills=max(0, pilot.kills - pilot.log_kills),
            gold=p.gold, dGold=p.gold - pilot.log_gold,
            hp=p.hp, maxHp=p.max_hp, hpPct=hp_pct,
            mp=p.mp, maxMp=p.max_mp,
            map=p.map.name if p.map else "", x=p.x, y=p.y,
            bag=len(p.bag), bagFree=BAG_SIZE - len(p.bag),
            supply=pilot.supply_state, supplyFails=pilot.supply_fails,
            remaining=pilot.remaining, deaths=pilot.deaths,
            status=pilot.status,
        )
        pilot.log_at = now_ms()
        pilot.log_exp = exp_now
        pilot.log_kills = pilot.kills
        pilot.log_gold = p.gold

    def _report(self, character_id: int, started_at: int, ended_at: int,
                minutes: int, exp_gained: int, kills: int, gold_delta: int,
                deaths: int, reason: str, notice: str = "") -> None:
        self.service.db.insert_afk_report(character_id, started_at, ended_at,
                                          minutes, exp_gained, kills,
                                          gold_delta, deaths, reason, notice)

    # ---- REST 快照 ------------------------------------------------------

    def snapshot(self) -> list[dict]:
        w = self.service.world
        out = []
        for pilot in self._pilots.values():
            p = w.actor(pilot.player_id)
            if p is None:
                continue
            out.append({"name": p.name, "level": p.level, "preset": pilot.preset,
                        "status": pilot.status, "kills": pilot.kills,
                        "deaths": pilot.deaths,
                        "remaining": self._remaining_of(pilot),
                        "supply": pilot.supply_state,
                        "map": p.map.name if p.map else "",
                        "x": p.x, "y": p.y,
                        "startedAt": pilot.started_at,
                        "goldDelta": p.gold - pilot.gold_start,
                        "expGained": self._exp_gained(w, pilot, p)})
        return out

    # ---- 挂机后台（/api/dash）视图 --------------------------------------

    def dash_view(self, character_id: int, p=None) -> dict:
        """离线修炼面板数据：余额/今日用量 + 运行中实时收益 + 上次报告。

        ``p`` 是世界里找到的角色（可能是离线代理，也可能是刚下线的真人对象
        ——真人完全退场时为 None，此时余额/报告仍能从库里读，这正是本页面
        的核心场景：人不在，看挂机的账）。
        """
        cfg = self.service.config
        db = self.service.db
        afk = db.get_afk(character_id)
        used_today = afk["used_minutes"] if afk["used_date"] == today() else 0
        out = {
            "balance": afk["balance"],
            "usedToday": used_today,
            "dailyLimit": cfg.afk_daily_minutes,
            "dailyRemaining": max(0, cfg.afk_daily_minutes - used_today),
            "plan": None,
            "running": None,
            "lastReport": None,
        }
        if p is not None:
            plan = getattr(p, "afk_plan", None)
            if plan:
                out["plan"] = dict(plan)
            pilot = self._pilots.get(p.id)
            if pilot is not None:
                w = self.service.world
                out["running"] = {
                    "startedAt": pilot.started_at,
                    "preset": pilot.preset,
                    "remaining": self._remaining_of(pilot),
                    "elapsedMin": max(0, int((now_ms() - pilot.started_at) // 60_000)),
                    "kills": pilot.kills,
                    "deaths": pilot.deaths,
                    "goldDelta": p.gold - pilot.gold_start,
                    "expGained": self._exp_gained(w, pilot, p),
                    "level": p.level,
                    "status": pilot.status,
                    "supply": pilot.supply_state,
                    "map": p.map.name if p.map else "",
                    "hp": p.hp, "maxHp": p.max_hp,
                }
        rep = db.latest_afk_report(character_id)
        if rep is not None:
            out["lastReport"] = {k: rep[k] for k in
                                 ("started_at", "ended_at", "minutes", "exp_gained",
                                  "kills", "gold_delta", "deaths", "reason", "notice")}
        return out

    def dash_stop(self, p) -> tuple[bool, str]:
        """后台「中止/取消」：运行中 → 立即结算停机；仅有待生效计划 → 取消计划。"""
        if p is None:
            return False, "角色不在修炼中，也没有待生效计划。"
        if getattr(p, "afk_offline", False):
            if self._pilots.get(p.id) is not None:
                self.disable(p, "后台中止离线修炼")
                return True, "已中止本次修炼，未用完的时长已退回余额（报告下次登录可见）。"
        plan = getattr(p, "afk_plan", None)
        if plan is not None:
            p.afk_plan = None
            return True, "已取消待生效的离线修炼计划。"
        return False, "当前没有进行中的修炼，也没有待生效计划。"
