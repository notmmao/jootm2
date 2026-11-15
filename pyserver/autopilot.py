"""在线托管 AutoPilot（一期）。

设计约束见 ``docs/AUTOPILOT-PLAN.md``：

* **绑真人会话**：实例只存在于 ``GameService`` 的内存字典里，绝不写
  ``characters`` 表；``on_disconnect`` 是唯一清理点，重登必须显式再点一次。
* **不加速**：走路 540ms / 跑 360ms、攻速 ``1400 - min(370, level*14)`` ms，
  与手动客户端完全一致；所有动作都走既有 handler（``_move`` / ``_attack`` /
  ``_pickup`` / ``_use_item`` / ``_cast_magic``），不绕过任何距离、时间锁与
  伤害校验。长距离移动按前端 ``RUN_INTERVAL`` 发 ``run: True`` 跑过去。
* **主动拾取**：掉落物不再只在路过时顺手捡，按价值/距离选目标主动走过去。
* **寻路**：8 方向 A*，与 ``world.try_move`` 用同一套阻挡判定；找不到路就
  把目标列入短时黑名单换目标，绝不退化成「朝墙直线撞」。
* **手动优先**：手动游戏消息在 ``GameService.on_message`` 处立即暂停托管。
* **单 loop 友好**：纯内存同步逻辑，无线程、无 IO、无阻塞；每 tick 最多
  一次扫描 + 一个动作。

本模块不注册 WS 消息类型（由 service.py 统一注册），也不落库。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

from .world import (BAG_SIZE, Monster, Player, direction_delta, now_ms)
from .handlers_combat import spell_cost, skill_level

# ---- 节拍（与前端 PlayScene.mjs 一致）------------------------------------
WALK_INTERVAL_MS = 540           # PlayScene.mjs:26 WALK_INTERVAL
RUN_INTERVAL_MS = 360            # PlayScene.mjs:27 RUN_INTERVAL（右键跑）
RUN_MIN_DIST = 3                 # 距离 >=3 格用跑，贴身逼近仍用走
PICKUP_INTERVAL_MS = 300
POTION_INTERVAL_MS = 1000
STATE_PUSH_MS = 1500
STUCK_MS = 180_000              # 零经验熔断：大图从城里走到野外可能要 1~2 分钟，
                                # 设太短会把「正在赶路」误判成卡死
PATH_MAX_NODES = 6000           # A* 单次扫描上限（100ms tick 内纯内存可承受）
LOOT_LOCK_MS = 8000             # 拾取目标锁定时长，避免多件掉落间来回抖动
BUTCH_LOCK_MS = 15_000          # 挖肉尸体锁定（一只动物要挖 3~10 次 × 600ms）
BUTCH_INTERVAL_MS = 700         # 挖肉动作节奏（服务端校验 600ms，留余量防无效连点）
UNREACHABLE_MS = 10_000         # 不可达目标黑名单时长（怪会移动，不能永久拉黑）
BLOCKED_LIMIT = 4               # 连续被挡几步就换目标，别原地磨墙

# ---- 默认策略档（管理员预置，玩家只选档）--------------------------------
PRESETS: dict[str, dict] = {
    "safe": {
        "name": "安全", "level_delta": 0, "sight": 9, "loot_sight": 6,
        "potion_hp": 0.70, "retreat_hp": 0.42, "max_target_dc": 12,
        "pickup_min_price": 0, "thrust": False, "butch": True,
        "desc": "只打不高于自身等级的怪，血线 70% 就喝药，顺手挖肉赚钱",
    },
    "balanced": {
        "name": "均衡", "level_delta": 3, "sight": 12, "loot_sight": 8,
        "potion_hp": 0.55, "retreat_hp": 0.28, "max_target_dc": 18,
        "pickup_min_price": 0, "thrust": True, "butch": True,
        "desc": "默认档，允许高 3 级的怪，血线 55% 喝药，顺手挖肉赚钱",
    },
    "efficient": {
        "name": "效率", "level_delta": 8, "sight": 12, "loot_sight": 10,
        "potion_hp": 0.45, "retreat_hp": 0.20, "max_target_dc": 26,
        "pickup_min_price": 50, "thrust": True, "butch": False,
        "desc": "接受高 8 级的怪、只捡值钱物品，不挖肉专注升级",
    },
}
DEFAULT_PRESET = "balanced"
PRESET_ALIASES = {"安全": "safe", "均衡": "balanced", "效率": "efficient",
                  "safe": "safe", "balanced": "balanced", "efficient": "efficient"}

# 按等级段的练级路线：只用于选怪加权与 HUD 建议，不做跨图自动导航
# （一期保持简单：托管只打当前地图，买卖/换图仍由玩家自己做）。
ROUTES: tuple[dict, ...] = (
    {"until": 7, "map": "0", "monsters": ("鸡", "鹿", "羊", "狼", "稻草人",
                                        "多钩猫", "钉耙猫"), "note": "比奇城外 · 鸡鹿稻草人"},
    {"until": 12, "map": "0", "monsters": ("稻草人", "多钩猫", "钉耙猫",
                                         "森林雪人", "半兽人"), "note": "比奇野外 · 猫与雪人"},
    {"until": 18, "map": "3", "monsters": ("僵尸", "洞蛆", "蜈蚣", "爬地僵尸"),
     "note": "矿区 · 僵尸洞蛆"},
    {"until": 26, "map": "3", "monsters": ("僵尸", "黑色恶蛆", "钳虫", "蝎蛇"),
     "note": "矿区深处 · 恶蛆钳虫"},
    {"until": 35, "map": "4", "monsters": ("红野猪", "黑野猪", "蝎蛇", "角蝇"),
     "note": "石墓 · 野猪蝎蛇"},
    {"until": 101, "map": "*", "monsters": (), "note": "按当前地图就近选怪"},
)

# 不打安全区守卫 / 练功师 / 明显的高级 BOSS，避免托管把自己送掉。
AVOID_NAMES = ("弓箭护卫", "虎卫", "鹰卫", "刀卫", "练功师", "卫士",
               "教主", "沃玛教主", "祖玛教主", "赤月恶魔", "触龙神")

MANUAL_TYPES = frozenset({
    "move", "attack", "dirAttack", "castMagic", "pickup", "useItem", "butch",
    "drop", "takeOnItem", "takeOffItem", "quickSlot", "moveItem",
    "npcTalk", "npcSelect", "buyItem", "sellItem", "querySellPrice",
    "storageOpen", "storageStore", "storageTake",
    "tradeRequest", "tradeAccept", "tradeCancel", "tradeAddItem",
    "tradeAddGold", "tradeConfirm",
})


def normalize_preset(value: Any) -> str:
    key = PRESET_ALIASES.get(str(value).strip()) if value is not None else None
    return key or DEFAULT_PRESET


def route_for(level: int) -> dict:
    for r in ROUTES:
        if level <= r["until"]:
            return r
    return ROUTES[-1]


def attack_interval_ms(level: int) -> int:
    """对齐前端 ``_canNextHit()``：1400 - min(370, level*14)。"""
    return max(0, 1400 - min(370, max(1, level) * 14))


def _walkable(game_map, x: int, y: int) -> bool:
    """必须与 ``world.try_move`` 完全一致：inside + can_walk + 无活物占位。

    旧实现给「can_walk=False 但 is_door=True」开了例外，可 try_move 开门后
    仍会因 can_walk=False 拒绝移动 —— 寻路以为有路、实际走不动，正是
    「隔着墙卡住」的一个来源。
    """
    return (game_map is not None and game_map.inside(x, y)
            and game_map.can_walk(x, y) and not game_map.occupied(x, y))


def bfs_path(game_map, sx: int, sy: int, tx: int, ty: int,
             exact: bool = False) -> list[int]:
    """8 方向 A*（切比雪夫启发），默认返回走到「目标格或其相邻格」的方向序列；
    ``exact=True`` 只认目标格本身（捡拾必须站在物品所在格，ObjBase.pas:1696）。

    只做本图寻路；找不到返回空列表，调用方据此**换目标**而不是直线撞墙。
    启发式让大图绕墙比朴素 BFS 少扫一个量级，节点上限保护 100ms tick。
    """
    if game_map is None or max(abs(sx - tx), abs(sy - ty)) <= (0 if exact else 1):
        return []
    start = (sx, sy)

    def heuristic(x: int, y: int) -> int:
        return max(abs(x - tx), abs(y - ty))

    best: dict[tuple[int, int], int] = {start: 0}
    prev: dict[tuple[int, int], tuple[int, tuple[int, int]]] = {}
    heap = [(heuristic(sx, sy), 0, start)]
    goal: tuple[int, int] | None = None
    scanned = 0
    while heap and scanned < PATH_MAX_NODES:
        _, g, (cx, cy) = heapq.heappop(heap)
        if g > best.get((cx, cy), 1 << 30):
            continue
        scanned += 1
        for d in range(8):
            dx, dy = direction_delta(d)
            nx, ny = cx + dx, cy + dy
            if not _walkable(game_map, nx, ny):
                continue
            ng = g + 1
            if ng >= best.get((nx, ny), 1 << 30):
                continue
            best[(nx, ny)] = ng
            prev[(nx, ny)] = (d, (cx, cy))
            if max(abs(nx - tx), abs(ny - ty)) <= (0 if exact else 1):
                goal = (nx, ny)
                heap.clear()
                break
            heapq.heappush(heap, (ng + heuristic(nx, ny), ng, (nx, ny)))
    if goal is None:
        return []
    out: list[int] = []
    cur = goal
    while cur != start:
        d, parent = prev[cur]
        out.append(d)
        cur = parent
    out.reverse()
    return out


# ---------------------------------------------------------------- 实例
@dataclass(slots=True)
class AutoPilot:
    manager: "AutoPilotManager"
    player_id: int
    preset: str = DEFAULT_PRESET
    active: bool = True
    reason: str = ""
    status: str = "托管已开启"
    started_at: int = 0
    kills: int = 0
    last_exp: int = 0
    last_progress_at: int = 0
    last_move_at: int = 0
    last_attack_at: int = 0
    last_pickup_at: int = 0
    last_potion_at: int = 0
    last_scan_at: int = 0
    last_state_at: int = 0
    pushed_status: str = ""
    target_id: int = 0
    loot_id: int = 0
    loot_until: int = 0
    butch_id: int = 0              # 正在挖的尸体 id
    butch_until: int = 0           # 挖肉目标锁过期时刻
    last_butch_at: int = 0
    last_bag: int = 0              # 背包数快照：挖肉/拾取得手也是「进展」信号
    last_gold: int = 0
    acted: bool = False            # 本 tick 已做过「主动动作」（治愈术），跳过攻击
    unreachable: dict[int, int] = field(default_factory=dict)  # 怪 id → 黑名单过期时刻
    path: list[int] = field(default_factory=list)
    path_key: tuple = ()
    blocked_steps: int = 0

    # ---- 状态回包 -------------------------------------------------------
    def set_status(self, text: str, now: int, force: bool = False) -> None:
        self.status = text
        if force or (text != self.pushed_status
                     and now - self.last_state_at >= STATE_PUSH_MS):
            self.manager.push(self.player_id, now, force=force)

    # ---- 主循环 ---------------------------------------------------------
    def tick(self, now: int) -> None:
        mgr = self.manager
        srv = mgr.service
        w = srv.world
        p = w.actor(self.player_id)
        if p is None or not isinstance(p, Player) or p.session is None:
            mgr.discard(self.player_id)      # 角色已离场：静默清场
            return
        if not self.active:
            return
        if getattr(p, "dead", False) or not p.alive:
            mgr.disable(p, "角色死亡，托管已停止")
            return

        cfg = PRESETS[self.preset]
        # 熔断：长时间一点进展都没有（经验/背包/金币都不变），说明卡住 /
        # 打不到怪 / 断在奇怪状态。挖肉不涨经验但会进肉、捡钱也只动金币，
        # 都要算进展，否则新手纯挖肉赚钱 3 分钟就被误停。
        progress = (p.exp != self.last_exp or len(p.bag) != self.last_bag
                    or p.gold != self.last_gold)
        if progress:
            self.last_exp = p.exp
            self.last_bag = len(p.bag)
            self.last_gold = p.gold
            self.last_progress_at = now
        elif now - self.last_progress_at >= STUCK_MS:
            mgr.disable(p, "长时间没有获得经验，托管已停止")
            return

        try:
            self.acted = False
            self._survive(srv, w, p, cfg, now)
            if not self.active:
                return
            self._pickup(srv, w, p, cfg, now)
            self._fight(srv, w, p, cfg, now)
        except Exception as e:  # 单个 pilot 出错不能拖垮 100ms tick
            print(f"[autopilot] {p.name} 异常: {type(e).__name__}: {e}")
            mgr.disable(p, "内部错误，托管已停止")
            return
        self.set_status(self.status, now)

    # ---- 生存：喝药 / 血线 / 背包满 ------------------------------------
    def _survive(self, srv, w, p, cfg, now: int) -> None:
        max_hp = max(1, p.max_hp)
        # stdMode 0 的普通药水是「回血池」，不是即时加血，必须算进有效血量，
        # 否则会在回血生效前连喝、把药一次耗光。
        pool = int(getattr(p, "inc_health", 0) or 0)
        ratio = (p.hp + pool) / max_hp
        potion = _find_potion(w, p)
        if ratio <= cfg["retreat_hp"] and potion is None:
            # 没药的道士还能靠治愈术自救（有蓝就不停机，治愈冷却中才熔断）
            if not self._taoist_heal(srv, w, p, now):
                self.manager.disable(p, "血量过低且没有药水，托管已停止")
            return
        if ratio <= cfg["potion_hp"]:
            # 道士优先治愈术回血（Magic.pas:301-317：不吃符、目标为空默认
            # 自己）；没学 / 没蓝 / 冷却中 → 喝药兜底（药效即时，保命优先）。
            if self._taoist_heal(srv, w, p, now):
                return
            if (potion is not None
                    and now - self.last_potion_at >= POTION_INTERVAL_MS
                    and now_ms() - getattr(p, "last_use_item_at", 0) >= 300):
                self.last_potion_at = now
                srv._use_item(p.session, {"type": "useItem",
                                          "makeIndex": int(potion.get("makeIndex", 0))})
                self.set_status("喝药中", now)
        if len(p.bag) >= BAG_SIZE - 2:
            scroll = _find_town_scroll(w, p)
            if scroll is not None:
                srv._use_item(p.session, {"type": "useItem",
                                          "makeIndex": int(scroll.get("makeIndex", 0))})
                self.manager.disable(p, "背包已满，已用回城卷回城；补给后再开启托管")
            else:
                self.manager.disable(p, "背包已满且没有回城卷，请清理背包")
            return

    # ---- 拾取：只捡值钱/有用的，垃圾留在地上 ---------------------------
    def _pickup(self, srv, w, p, cfg, now: int) -> None:
        if now - self.last_pickup_at < PICKUP_INTERVAL_MS:
            return
        for gi in list(w.ground_items.values()):
            # 原版 ClientPickUpItem（ObjBase.pas:1696）必须站在物品格上才能捡
            if gi.map is not p.map or (p.x, p.y) != (gi.x, gi.y):
                continue
            if not _want_pickup(w, gi, cfg):
                continue
            if not w._can_pick_owner(p, gi):
                continue        # 别人的掉落（2 分钟归属内），别反复尝试
            if not gi.is_gold() and len(p.bag) >= BAG_SIZE:
                continue
            self.last_pickup_at = now
            srv._pickup(p.session, {"type": "pickup", "itemId": gi.id})
            if gi.id == self.loot_id:      # 捡完立刻释放拾取目标锁
                self.loot_id = 0
                self.loot_until = 0
            self.set_status("拾取中", now)
            return

    def _taoist_heal(self, srv, w, p, now: int) -> bool:
        """道士治愈术回血（原版 SKILL_HEALLING，Magic.pas:301-317）：
        目标为空默认施给自己、**不吃护身符**。返回 False = 当前不可施
        （非道士 / 没学 / 没蓝 / 冷却中），调用方回落喝药。
        MP 不足提示会刷屏，必须先自查（冷却拦截 :986 是静默的）。"""
        if p.job != 2 or 2 not in p.skills:
            return False
        t = w.magic_templates.get(2)
        if t is None:
            return False
        if now_ms() - p.skill_cooldown.get(2, 0) < t.delay:
            return False
        if p.mp < spell_cost(t, skill_level(p, 2)):
            return False
        exp_before = p.exp
        srv._cast_magic(p.session, {"type": "castMagic", "magicId": 2,
                                    "targetId": 0, "targetX": p.x,
                                    "targetY": p.y})
        self.acted = True            # 一个 tick 一个主动动作（原版节拍）
        self.set_status("治愈术中", now)
        if p.exp != exp_before:
            self.last_exp = p.exp
            self.last_progress_at = now
        return True

    def _taoist_fire(self, srv, w, p, target, now: int) -> bool:
        """道士远程灵魂火符（原版 SKILL_FIRECHARM？13，每次 1 张符）。

        返回 True = 本 tick 已被远程攻击占用（已施放 / 冷却中保持站位）；
        False = 不可用（非道士 / 没学 / 没蓝 / 没符），调用方回落近战。
        冷却自查按真实时钟与 ``_cast_magic`` 首行拦截（:986，静默）同源。"""
        if p.job != 2 or 13 not in p.skills:
            return False
        t = w.magic_templates.get(13)
        if t is None:
            return False
        if now_ms() - p.skill_cooldown.get(13, 0) < t.delay:
            self.set_status(f"打怪中 · {target.name}", now)
            return True
        if p.mp < spell_cost(t, skill_level(p, 13)):
            return False
        if srv._find_amulet(p, 1, 1) is None:
            return False        # 符耗尽不自动补戴（CheckAmulet 只查装备槽）
        exp_before = p.exp
        srv._cast_magic(p.session, {"type": "castMagic", "magicId": 13,
                                    "targetId": target.id,
                                    "targetX": target.x, "targetY": target.y})
        self.last_attack_at = now
        self.set_status(f"火符 · {target.name}", now)
        if p.exp != exp_before:
            self.kills += 1
            self.last_exp = p.exp
            self.last_progress_at = now
        return True

    # ---- 目标决策：掉落物 → 选怪 → 寻路 → 攻击 --------------------------
    def _fight(self, srv, w, p, cfg, now: int) -> None:
        if self.acted:               # 治愈术等生存动作已占用本 tick
            return
        # 过期黑名单清理：怪会走动，不可达不能永久拉黑。
        for tid, expire in list(self.unreachable.items()):
            if expire <= now:
                del self.unreachable[tid]

        # 贴身威胁优先：被怪追着打的时候别跑去捡东西送死。
        loot = None if self._nearby_threat(w, p) else self._choose_loot(w, p, cfg, now)
        if loot is not None:
            d = max(abs(p.x - loot.x), abs(p.y - loot.y))
            if d == 0:                       # 同格才能捡（原版 GetItem 自身坐标）
                self.set_status("拾取中", now)   # _pickup 本 tick 已跑过
                return
            label = "金币" if loot.is_gold() else "物品"
            self._step_to(srv, w, p, loot.x, loot.y,
                          ("loot", loot.id), f"捡{label}", now, exact=True)

        route = route_for(p.level)
        target = self._choose_target(w, p, cfg, route, now)
        if target is None:
            # 视野内没有可打的怪 → 空档期挖肉（新角色金币主要来源：鸡/鹿/羊/
            # 狼都是可挖动物，肉卖给商店换钱）。原版 Alt+左键 CM_BUTCH，
            # 距离 ≤2 就能挖（ObjBase.pas:17470），不必站上尸体格。
            if self._butch_corpses(srv, w, p, cfg, now):
                return
            far = self._nearest_monster(w, p, cfg, now)
            if far is None:
                self.set_status("附近没有可打的怪 · 等待刷新", now)
                return
            target = far
        self.target_id = target.id
        dist = p.distance_to(target)
        # 道士远程：施法半径 8 格内（Magic.pas:266）优先灵魂火符，
        # 没符/没蓝/没学 → 近战兜底；超射程先走近，进入射程即转远程。
        if dist <= 8 and self._taoist_fire(srv, w, p, target, now):
            return
        if dist <= 1:
            if now - self.last_attack_at >= attack_interval_ms(p.level):
                self.last_attack_at = now
                exp_before = p.exp
                srv._attack(p.session, {"type": "attack", "targetId": target.id})
                self.set_status(f"打怪中 · {target.name}", now)
                if p.exp != exp_before:
                    self.kills += 1
                    self.last_exp = p.exp
                    self.last_progress_at = now
            elif now - self.last_attack_at < 200:
                self.set_status(f"打怪中 · {target.name}", now)
        else:
            self._step_to(srv, w, p, target.x, target.y,
                          ("monster", target.id), f"前往 {target.name}", now)

    def _nearby_threat(self, w, p) -> bool:
        """两格内有活怪贴身 / 正在锁定我就先战斗（召唤物不算威胁——
        宝宝贴着主人是常态，敌对玩家的宝宝也不会主动锁玩家）。"""
        for a in w.nearby(p.map, p.x, p.y, 2):
            if (isinstance(a, Monster) and a.alive and a.master_id < 0
                    and (a.target_id == p.id or p.distance_to(a) <= 1)):
                return True
        return False

    def _butch_corpses(self, srv, w, p, cfg, now: int) -> bool:
        """空档期挖肉。返回 True = 本 tick 已被挖肉占用（走位或出刀）。

        原版 CM_BUTCH 链路（ObjBase.pas:17458-17504）：
        * 要求 ``m_boDeath and not m_boSkeleton and m_boAnimal``——尸体还在
          （corpse_until 未过）、没变骷髅、是可挖动物；
        * 距离 ≤2，两次出刀间隔 dwTurnIntervalTime（600ms 默认）；
        * 挖空一次就 ``TakeBagItems`` 一次性拿走全部产出（bag_looted）。
        只在视野里没有活怪可打时进行——有怪先清怪，挖肉不打断战斗节奏。
        """
        if not cfg.get("butch"):
            return False
        sight = cfg.get("loot_sight", 8)
        corpse = self._choose_butch(w, p, cfg, now, sight)
        if corpse is None:
            self.butch_id = 0
            return False
        d = max(abs(p.x - corpse.x), abs(p.y - corpse.y))
        if d > 2:
            self._step_to(srv, w, p, corpse.x, corpse.y,
                          ("butch", corpse.id), f"挖肉 · {corpse.name}", now)
            return True
        if now - self.last_butch_at < BUTCH_INTERVAL_MS:
            self.set_status(f"挖肉 · {corpse.name}", now)
            return True
        self.last_butch_at = now
        srv._butch(p.session, {"type": "butch", "actorId": corpse.id,
                               "x": corpse.x, "y": corpse.y})
        # 挖空（TakeBagItems 已给产出）或尸体被挖成骷髅/到期消失 → 释放目标
        if corpse.bag_looted or corpse.skeleton or corpse.corpse_until <= now:
            self.butch_id = 0
            self.butch_until = 0
        self.set_status(f"挖肉 · {corpse.name}", now)
        return True

    def _choose_butch(self, w, p, cfg, now: int, sight: int):
        """选挖肉尸体：视野内最近的可挖尸体，锁定 15s（一只要挖 3~10 次）。"""
        if self.butch_until > now:
            m = w.actor(self.butch_id)
            if (isinstance(m, Monster) and not m.alive and m.map is p.map
                    and not m.skeleton and not m.bag_looted
                    and m.corpse_until > now
                    and max(abs(p.x - m.x), abs(p.y - m.y)) <= sight + 2):
                return m
        best, best_d = None, 10 ** 9
        gm = p.map
        if gm is None:
            return None
        for a in gm.actors.values():
            # 注意：w.nearby 只返回活物，尸体必须直接扫地图注册表
            if (not isinstance(a, Monster) or a.alive or a.skeleton
                    or a.bag_looted or a.corpse_until <= now
                    or not w.butchable(a)):
                continue
            if max(abs(a.x - p.x), abs(a.y - p.y)) > sight:
                continue
            d = p.distance_to(a)
            if d < best_d:
                best, best_d = a, d
        if best is not None:
            self.butch_id = best.id
            self.butch_until = now + BUTCH_LOCK_MS
        return best

    def _choose_loot(self, w, p, cfg, now: int):
        """主动拾取目标：按价值/距离评分，锁定 8s 避免多件掉落间来回抖动。

        目标格被别的活物占着（怪站在金币上）时**直接跳过不追**：原版必须
        站上物品格才能捡（ObjBase.pas:1696），被占的格永远进不去，追过去
        只会「走不到→拉黑→换目标→又拉黑」来回掉头震荡。
        """
        sight = cfg.get("loot_sight", 8)
        if self.loot_until > now:
            gi = w.ground_item(self.loot_id)
            if (gi is not None and gi.map is p.map
                    and self.unreachable.get(gi.id, 0) <= now
                    and max(abs(p.x - gi.x), abs(p.y - gi.y)) <= sight + 2
                    and _want_pickup(w, gi, cfg)
                    and w._can_pick_owner(p, gi)
                    and not _tile_blocked_by_other(p, gi.x, gi.y)):
                return gi
        best, best_score = None, -1.0
        for gi in w.ground_items.values():
            if gi.map is not p.map or not _want_pickup(w, gi, cfg):
                continue
            if not w._can_pick_owner(p, gi):
                continue        # 2 分钟归属期内的别人掉落，跳过不追
            if self.unreachable.get(gi.id, 0) > now:
                continue
            d = max(abs(p.x - gi.x), abs(p.y - gi.y))
            if d > sight:
                continue
            if not gi.is_gold() and len(p.bag) >= BAG_SIZE:
                continue
            if d > 0 and _tile_blocked_by_other(p, gi.x, gi.y):
                continue        # 落点被占：等怪走开，别来回掉头
            t = None if gi.is_gold() else _template(w, gi.item)
            value = 80 if gi.is_gold() else max(1, t.price if t else 1)
            score = value / (1.0 + d * 0.7)
            if gi.id == self.loot_id:
                score *= 1.3
            if score > best_score:
                best, best_score = gi, score
        self.loot_id = best.id if best is not None else 0
        self.loot_until = now + LOOT_LOCK_MS if best is not None else 0
        return best

    def _choose_target(self, w, p, cfg, route, now: int):
        cached = w.actor(self.target_id)
        if (isinstance(cached, Monster) and cached.alive and cached.master_id < 0
                and cached.map is p.map
                and p.distance_to(cached) <= cfg["sight"] + 2
                and self.unreachable.get(cached.id, 0) <= now
                and now - self.last_scan_at < 600):
            return cached
        self.last_scan_at = now
        best, best_score = None, -1.0
        for a in w.nearby(p.map, p.x, p.y, cfg["sight"]):
            if not isinstance(a, Monster) or not a.alive:
                continue
            if a.master_id >= 0:
                continue        # 召唤物（含自己的宝宝）不是打怪目标——打了
                                # 就是「道士攻击自己的召唤物」，还记仇到主人头上
            if self.unreachable.get(a.id, 0) > now:
                continue
            t = a.template
            if t is None or any(k in a.name for k in AVOID_NAMES):
                continue
            lv = max(1, t.level)
            # 怪物模板 level 主要用于经验惩罚，并不代表强度（1 级新手打
            # 的就是 10 级稻草人）。危险度按攻击力/血量判，等级差只做降权，
            # 不能硬过滤——否则新手开托管会一只怪都选不到，直到熔断停机。
            if t.dc > cfg["max_target_dc"]:
                continue
            if t.hp > max(120, p.max_hp * 5) and lv > p.level + 10:
                continue
            d = p.distance_to(a)
            score = max(1, t.exp) / (1.0 + d * 0.8)
            if route["monsters"] and any(k in a.name for k in route["monsters"]):
                score *= 1.5
            if lv > p.level + cfg["level_delta"]:
                score *= 0.5
            if lv > p.level:
                score *= 0.8
            if a.target_id == p.id:
                score *= 1.2
            if score > best_score:
                best, best_score = a, score
        return best

    def _nearest_monster(self, w, p, cfg, now: int):
        best, best_d = None, 10 ** 9
        gm = p.map
        if gm is None:
            return None
        for a in gm.actors.values():
            if not isinstance(a, Monster) or not a.alive or a.template is None:
                continue
            if a.master_id >= 0:
                continue        # 召唤物不追（见 _choose_target）
            if self.unreachable.get(a.id, 0) > now:
                continue
            if any(k in a.name for k in AVOID_NAMES):
                continue
            t = a.template
            if t.dc > cfg["max_target_dc"]:
                continue
            if t.hp > max(120, p.max_hp * 5) and max(1, t.level) > p.level + 10:
                continue
            d = p.distance_to(a)
            if d < best_d:
                best, best_d = a, d
        return best

    def _step_to(self, srv, w, p, tx: int, ty: int, key: tuple,
                 label: str, now: int, exact: bool = False) -> bool:
        """走到目标点（默认相邻即可，``exact=True`` 要求踏上目标格——
        捡拾原版必须站在物品所在格上，ObjBase.pas:1696）；返回 False =
        目标不可达、已换目标。

        距离 >=3 格按前端 ``RUN_INTERVAL`` 发 ``run: True`` 跑过去；逼近时
        仍用走。路径 A* 与 ``try_move`` 共用阻挡判定，找不到路**绝不退化成
        direction_toward 朝墙直线撞**，而是短时拉黑目标重新选。
        """
        dist = max(abs(p.x - tx), abs(p.y - ty))
        if dist <= (0 if exact else 1):
            return True
        running = dist >= RUN_MIN_DIST or len(self.path) >= 2
        interval = RUN_INTERVAL_MS if running else WALK_INTERVAL_MS
        if now - self.last_move_at < interval:
            return True
        if now_ms() - getattr(p, "map_change_at", 0) < 350:
            return True                      # 与手动共用换图短锁
        # 缓存只绑「目标 + 坐标 + 所在图」，不绑玩家坐标：路径是一串方向，
        # 走一步 pop 一步；目标移动 / 换图 / 被挡才重算。
        path_key = key + (tx, ty, p.map.name if p.map else "", exact)
        if not self.path or self.path_key != path_key:
            self.path = bfs_path(p.map, p.x, p.y, tx, ty, exact)
            self.path_key = path_key
            if not self.path:
                self._give_up_target(key, now)
                self.path_key = ()
                self.set_status("目标被墙挡住 · 换目标", now)
                return False
        d = self.path.pop(0)
        before = (p.x, p.y, p.map.name if p.map else "")
        self.last_move_at = now
        srv._move(p.session, {"type": "move", "dir": d, "run": running})
        after = (p.x, p.y, p.map.name if p.map else "")
        self.set_status(f"{label} · {dist}格{' · 跑' if running else ''}", now)
        if before[2] != after[2]:
            self.path.clear()            # 踩门换图：旧路径失效，重新选怪
            self.path_key = ()
            self.target_id = 0
            self.loot_id = 0
        elif before[:2] == after[:2]:
            self.path.clear()            # 被占格 / 墙挡住，下个节拍重算
            self.blocked_steps += 1
            if self.blocked_steps >= BLOCKED_LIMIT:
                self.path_key = ()
                self._give_up_target(key, now)
                self.set_status("寻路受阻 · 换目标", now)
                return False
        else:
            self.blocked_steps = 0
        return True

    def _give_up_target(self, key: tuple, now: int) -> None:
        """不可达目标短时拉黑（id 由 world.new_id() 全局唯一，怪/物品共用）。"""
        if not key:
            return
        self.unreachable[key[1]] = now + UNREACHABLE_MS
        if key[0] == "monster":
            self.target_id = 0
        else:
            self.loot_id = 0
            self.loot_until = 0


# ---------------------------------------------------------------- 物品筛选
def _tile_blocked_by_other(p, x: int, y: int) -> bool:
    """目标格是否有**别的**活物占位（自己站在上面不算——那正是捡拾位）。

    ``GameMap.occupied`` 把玩家自己也算进去，捡拾判定必须排除自身。
    """
    gm = p.map
    if gm is None:
        return False
    lst = gm.by_pos.get((x, y))
    return any(a.alive and a is not p for a in (lst or ()))


def _template(w, it: dict | None):
    if not it:
        return None
    return w.item_templates.get(int(it.get("index", 0)))


def _find_potion(w, p):
    best, best_key = None, None
    for it in p.bag:
        t = _template(w, it)
        if t is None or t.stdMode != 0 or t.acMin <= 0:
            continue
        # 疗伤药(shape=1)立即回血优先；其余按回复量从小到大，避免浪费大药
        key = (0 if t.shape == 1 else 1, t.acMin)
        if best_key is None or key < best_key:
            best, best_key = it, key
    return best


def _find_town_scroll(w, p):
    for it in p.bag:
        t = _template(w, it)
        if t is not None and t.stdMode == 3 and t.shape in (1, 2, 3, 5):
            return it
    return None


def _want_pickup(w, gi, cfg: dict) -> bool:
    if gi.is_gold():
        return True
    t = _template(w, gi.item)
    if t is None:
        return True
    if t.stdMode in (1, 2):              # 肉 / 毒药：占位垃圾
        return False
    if t.stdMode == 0 and t.acMin <= 0 and t.macMin <= 0:
        return False
    return t.price >= cfg["pickup_min_price"]


# ---------------------------------------------------------------- 管理器
class AutoPilotManager:
    """只存在于服务进程内存；角色登出/断线时由 GameService 显式清理。"""

    def __init__(self, service: Any):
        self.service = service
        self._pilots: dict[int, AutoPilot] = {}

    def get(self, p) -> AutoPilot | None:
        return self._pilots.get(p.id) if p is not None else None

    def discard(self, player_id: int) -> None:
        self._pilots.pop(player_id, None)

    def on_disconnect(self, p) -> None:
        """断线即停：直接丢弃实例，不落库、不推消息（会话已不可用）。"""
        if p is not None:
            self._pilots.pop(p.id, None)

    def enable(self, p, preset=None, now: int | None = None) -> tuple[bool, str]:
        if p is None or p.session is None:
            return False, "角色不在线"
        if getattr(p, "dead", False) or not p.alive:
            return False, "角色已死亡，请重新登录"
        stamp = now_ms() if now is None else now
        key = normalize_preset(preset)
        pilot = self._pilots.get(p.id)
        if pilot is None:
            pilot = AutoPilot(self, p.id, preset=key, started_at=stamp,
                              last_exp=p.exp, last_progress_at=stamp)
            self._pilots[p.id] = pilot
        pilot.preset = key
        pilot.active = True
        pilot.reason = ""
        pilot.path.clear()
        pilot.path_key = ()
        pilot.target_id = 0
        pilot.loot_id = 0
        pilot.loot_until = 0
        pilot.unreachable.clear()
        pilot.blocked_steps = 0
        pilot.last_progress_at = stamp
        pilot.last_exp = p.exp
        pilot.last_move_at = 0
        pilot.last_attack_at = 0
        self._ensure_thrust(p, key)
        pilot.set_status("托管已开启", stamp, force=True)
        self.service.world.send_system(
            p, f"[托管] 已开启 · {PRESETS[key]['name']}档；进行手动操作会自动接管。")
        return True, ""

    def _ensure_thrust(self, p, preset: str) -> None:
        """学会刺杀(12)且策略开启时，代打开关（走原版 toggle handler）。"""
        if not PRESETS[preset]["thrust"] or 12 not in p.skills or p.long_hit_on:
            return
        try:
            self.service._cast_magic(p.session, {
                "type": "castMagic", "magicId": 12,
                "targetX": p.x, "targetY": p.y, "targetId": -1})
        except Exception as e:  # 开关失败不影响托管主流程
            print(f"[autopilot] 刺杀开关失败: {type(e).__name__}: {e}")

    def pause(self, p, reason: str = "手动操作，已接管") -> None:
        pilot = self.get(p)
        if pilot is None or not pilot.active:
            return
        pilot.active = False
        pilot.reason = reason
        pilot.path.clear()
        pilot.set_status("已暂停 · 手动接管", now_ms(), force=True)

    def disable(self, p, reason: str) -> None:
        pilot = self._pilots.pop(p.id, None) if p is not None else None
        if pilot is None:
            return
        pilot.active = False
        pilot.reason = reason
        pilot.status = "已停止"
        if p is not None and p.session is not None:
            p.session.send_json(self.state_dict(p, None, pilot.preset,
                                                reason=reason, now=now_ms()))
            self.service.world.send_system(p, f"[托管] {reason}。")

    def tick(self, now: int) -> None:
        if not self._pilots:
            return
        for pilot in list(self._pilots.values()):
            try:
                pilot.tick(now)
            except Exception as e:  # 双保险：pilot.tick 内部已兜一层
                print(f"[autopilot] tick 异常 id={pilot.player_id}: "
                      f"{type(e).__name__}: {e}")
                self.discard(pilot.player_id)

    # ---- 状态视图（WS 回包 + REST 共用）--------------------------------
    def state_dict(self, p, pilot: AutoPilot | None, preset: str,
                   reason: str = "", ok: bool = True, now: int | None = None) -> dict:
        stamp = now_ms() if now is None else now
        key = normalize_preset(preset)
        active = bool(pilot is not None and pilot.active)
        return {
            "type": "autopilotState", "ok": ok,
            "active": active,
            "paused": bool(pilot is not None and not pilot.active),
            "preset": pilot.preset if pilot else key,
            "presetName": PRESETS[pilot.preset if pilot else key]["name"],
            "status": pilot.status if pilot else ("已暂停" if reason else "未开启"),
            "reason": reason or (pilot.reason if pilot else ""),
            "kills": pilot.kills if pilot else 0,
            "elapsedMs": max(0, stamp - pilot.started_at) if pilot else 0,
            "level": p.level if p is not None else 0,
            "route": route_for(p.level if p is not None else 1)["note"],
            "presets": [{"id": k, "name": v["name"], "desc": v["desc"]}
                        for k, v in PRESETS.items()],
        }

    def push(self, player_id: int, now: int, force: bool = False) -> None:
        w = self.service.world
        p = w.actor(player_id)
        pilot = self._pilots.get(player_id)
        if p is None or p.session is None:
            return
        if pilot is None:
            return
        if not force and now - pilot.last_state_at < STATE_PUSH_MS:
            return
        pilot.last_state_at = now
        pilot.pushed_status = pilot.status
        p.session.send_json(self.state_dict(p, pilot, pilot.preset, now=now))

    def push_state(self, p, preset: str | None = None,
                   reason: str = "", ok: bool = True) -> None:
        if p is None or p.session is None:
            return
        pilot = self.get(p)
        p.session.send_json(self.state_dict(p, pilot,
                                            preset or (pilot.preset if pilot else DEFAULT_PRESET),
                                            reason=reason, ok=ok))

    def snapshot(self) -> list[dict]:
        w = self.service.world
        out = []
        for pilot in self._pilots.values():
            p = w.actor(pilot.player_id)
            if p is None:
                continue
            out.append({"name": p.name, "level": p.level, "active": pilot.active,
                        "preset": pilot.preset, "status": pilot.status,
                        "kills": pilot.kills, "exp": p.exp,
                        "map": p.map.name if p.map else "",
                        "x": p.x, "y": p.y, "hp": p.hp, "maxHp": p.max_hp,
                        "elapsedMs": max(0, now_ms() - pilot.started_at)})
        return out
