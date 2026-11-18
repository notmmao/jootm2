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

from .autopilot import (DEFAULT_PRESET, PRESETS, AutoPilot, normalize_preset)
from .util import RED_NAME_PK_POINT
from .world import BAG_SIZE, Player, now_ms


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
SUPPLY_RETRY_MS = 60_000        # 一次补给失败后的重试冷却
SUPPLY_STATE_TIMEOUT_MS = 8 * 60_000   # 单状态超时（大图赶路 1~2 分钟量级）
SUPPLY_SELL_MAX_PRICE = 100     # 「杂物」卖价阈值：低于它的装备顺手清掉
SUPPLY_BAG_TRIGGER = 4          # 背包剩余空位 ≤4 触发补给
SUPPLY_POTION_MIN_LEVEL = 10    # 药尽触发门槛
SUPPLY_POTIONS_PER_30MIN = 6    # 按剩余修炼时长粗估的买药量（每 30 分钟）


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

    exp_start: int = 0            # 开局本级经验（结算算经验增量用）
    level_start: int = 0
    max_exp_start: int = 0
    gold_start: int = 0
    restart_bonus: int = 0        # 重启续挂补偿（分钟，写进报告通知）
    deaths: int = 0
    remaining: int = 0            # 剩余修炼分钟（每 60s 递减，= afk_sessions 记账）
    # ---- 补给闭环状态机 ----
    supply_state: str = ""        # ""=挂机中，其余见 SUPPLY_* 常量
    supply_reason: str = ""       # 本轮补给的原因（背包满/药水耗尽）
    supply_since: int = 0         # 当前状态进入时刻（超时熔断用）
    supply_fails: int = 0         # 连续补给失败次数（≥2 熔断停机）
    supply_retry_until: int = 0   # 失败后的重试冷却截止
    last_supply_check_at: int = 0 # 触发判定节流
    supply_last_act_at: int = 0   # 卖/买动作节拍
    supply_leg_try: int = 0       # 当前门段/锚点段连续寻路受阻计数
    supply_npc_id: int = 0        # 当前交易 NPC（调试观察用）
    supply_route: list = field(default_factory=list)   # 剩余门段 [(src,gx,gy,dst)]
    supply_unsellable: set = field(default_factory=set) # 无商人收购的 makeIndex
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

    def _supply_town_map(self, srv) -> str:
        return str(getattr(srv.config, "afk_supply_town_map", "0") or "0")

    def _on_danger_no_potion(self, srv, w, p, now: int) -> None:
        if self.supply_state:
            # 补给赶路途中血线危险：继续赶路（城里安全区），不停机。
            self.set_status("补给途中 · 血线危险", now)
            return
        AutoPilot._on_danger_no_potion(self, srv, w, p, now)

    def _on_bag_full(self, srv, w, p, now: int) -> None:
        if self.supply_state:
            return          # 补给状态机正在处理背包问题（卖货腾格子）
        if self._supply_enabled(srv):
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
        if not self._supply_enabled(srv) or now < self.supply_retry_until:
            return False
        why = ""
        if len(p.bag) >= BAG_SIZE - SUPPLY_BAG_TRIGGER:
            why = "背包将满"
        elif p.level >= SUPPLY_POTION_MIN_LEVEL:
            hp, mp = count_potions(w, p)
            if p.job == 1:
                if hp == 0 or mp == 0:
                    why = "药水耗尽"
            elif hp == 0:
                why = "药水耗尽"
        if not why:
            return False
        # 预检可达性：图链路不通是确定性的，直接熔断（报告写明原因）。
        town = self._supply_town_map(srv)
        if p.map is not None and p.map.name != town:
            if supply_gate_route(w.gates_by_src, p.map.name, town) is None:
                mgr = self.manager
                mgr.disable(p, f"无可达补给点：地图 {p.map.name} → {town} 无门链路，"
                               f"离线修炼已停止（触发：{why}）")
                return True
        self._supply_begin(srv, w, p, now, why)
        return True

    def _supply_begin(self, srv, w, p, now: int, why: str) -> None:
        self.supply_reason = why
        self.supply_state = SUPPLY_GOTO_TOWN
        self.supply_since = now
        self.supply_route = []
        self.supply_leg_try = 0
        self.supply_unsellable.clear()
        self.supply_npc_id = 0
        self.anchor_map = p.map.name if p.map else ""
        self.anchor_x, self.anchor_y = p.x, p.y
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
        town = self._supply_town_map(srv)
        if self.supply_state == SUPPLY_GOTO_TOWN:
            if self._supply_cross_map(srv, w, p, now, town):
                self.supply_state = SUPPLY_SELL
                self.supply_since = now
                self.supply_unsellable.clear()
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
                self.manager.disable(p, f"无可达补给点：地图 {p.map.name} → {dest_map} "
                                        f"无门链路，离线修炼已停止")
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

    # ---- 卖货 -----------------------------------------------------------

    def _supply_sell(self, srv, w, p, now: int) -> None:
        """逐个卖掉肉（StdMode 40）与低于阈值的杂物；卖空转买药。"""
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
        if cand is None:
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
        return best

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
        """按目标数量买血药（法师另买蓝药），金币不够一瓶 → 熔断计失败。"""
        want_mp = p.job == 1
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
        self.supply_state = ""
        self.supply_fails = 0
        self.supply_route = []
        # 回锚点了：刷新进展快照，避免被零进展熔断误杀
        self.last_exp, self.last_bag, self.last_gold = p.exp, len(p.bag), p.gold
        self.last_progress_at = now
        self.set_status("补给完成 · 继续修炼", now, force=True)

    def _supply_fail(self, srv, w, p, now: int, why: str) -> None:
        self.supply_fails += 1
        if self.supply_fails >= 2:
            self.manager.disable(p, f"连续 {self.supply_fails} 次补给失败：{why}，"
                                    f"离线修炼已停止")
            return
        self.supply_state = ""
        self.supply_route = []
        self.supply_retry_until = now + SUPPLY_RETRY_MS
        self.last_exp, self.last_bag, self.last_gold = p.exp, len(p.bag), p.gold
        self.last_progress_at = now
        self.set_status(f"补给失败（{why}），稍后重试", now, force=True)


class OfflineAgentManager:
    """离线修炼代理管理器。接口面与 AutoPilotManager 对齐（AutoPilot.tick 会调
    ``manager.push/discard/disable/handle_death``），由 ``service.tick`` 驱动。"""

    def __init__(self, service: Any):
        self.service = service
        self._pilots: dict[int, OfflinePilot] = {}
        self._last_charge_at = 0      # 分钟记账节拍
        self._last_persist_at = 0     # 周期落库节拍（降低宕机损失）

    # ---- 接口面（AutoPilot 决策循环会调用） -----------------------------

    def get(self, p) -> OfflinePilot | None:
        return self._pilots.get(p.id) if p is not None else None

    def discard(self, player_id: int) -> None:
        """决策循环发现角色不在世界（已被移除）——收尾清 pilot。"""
        pilot = self._pilots.pop(player_id, None)
        if pilot is not None:
            pilot.active = False

    def push(self, player_id: int, now: int, force: bool = False) -> None:
        """离线没有客户端可推，只同步 pushed_status 避免重复字符串比较。"""
        pilot = self._pilots.get(player_id)
        if pilot is not None:
            pilot.pushed_status = pilot.status
            pilot.last_state_at = now

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
        pilot.on_disabled(reason)
        self._settle(p, pilot, reason)

    def handle_death(self, p, pilot: OfflinePilot) -> None:
        """死亡处理（已拍板：自动复活 + 连死熔断）。

        掉落已在 ``world.kill_player`` 里按原版结算；这里把角色按登录复活的
        同一规则（UsrEngn.pas:576-600：回出生点 ±2、HP=14）原地拉起继续挂。
        """
        cfg = self.service.config
        pilot.deaths += 1
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
        if p.map is None or p.map.name in cfg.afk_excluded_maps:
            return False, "当前地图禁止离线修炼。", info
        if p.level < cfg.afk_min_level:
            return False, f"离线修炼需要 {cfg.afk_min_level} 级以上。", info
        if getattr(p, "dead", False) or not p.alive:
            return False, "角色已死亡，无法开启离线修炼。", info
        minutes = int(minutes)
        if minutes <= 0:
            return False, "修炼时长无效。", info
        balance = info["balance"]
        if balance < 10:
            return False, "修炼时长余额不足，请找比奇城的玛法领路人领取。", info
        minutes = min(minutes, balance, info["dailyRemaining"])
        if minutes < 10:
            return False, "今日可用修炼时长不足 10 分钟。", info
        key = normalize_preset(preset)
        p.afk_plan = {"minutes": minutes, "preset": key}
        info = self.state_info(p)
        return True, f"已设定离线修炼计划：下线后自动修炼 {minutes} 分钟（{PRESETS[key]['name']}档）。", info

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
        if p.map is None or p.map.name in cfg.afk_excluded_maps:
            reason = "下线时所在地图已禁止离线修炼"
        elif p.level < cfg.afk_min_level:
            reason = "等级低于离线修炼门槛"
        elif len(self._pilots) >= cfg.afk_max_agents:
            reason = "全服离线修炼名额已满"
        elif sum(1 for q in self._pilots.values()
                 if q and self._pilot_map(q) == (p.map.name if p.map else "")) >= cfg.afk_per_map_limit:
            reason = "当前地图离线修炼名额已满"
        minutes = int(plan.get("minutes", 0))
        afk = self.service.db.get_afk(p.character_id)
        used_today = afk["used_minutes"] if afk["used_date"] == today() else 0
        minutes = min(minutes, afk["balance"],
                      max(0, cfg.afk_daily_minutes - used_today))
        if not reason and minutes < 10:
            reason = "可用修炼时长不足 10 分钟"
        if reason:
            self._report(p.character_id, now_ms(), now_ms(), 0, 0, 0, 0, 0,
                         f"离线修炼未能开启：{reason}")
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
        reason = "玩家上线，离线修炼结束"
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
        # 退回未用完的余额
        used_date = afk["used_date"] if afk["used_date"] == today() else today()
        used_minutes = (afk["used_minutes"] if afk["used_date"] == used_date else 0) + consumed
        db.save_afk(p.character_id, afk["balance"] + remaining,
                    afk["claim_date"], afk["claim_count"], used_date, used_minutes)
        self._report(p.character_id, pilot.started_at, now_ms(), consumed,
                     self._exp_gained(self.service.world, pilot, p),
                     pilot.kills, p.gold - pilot.gold_start, pilot.deaths,
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
                        "startedAt": pilot.started_at})
        return out
