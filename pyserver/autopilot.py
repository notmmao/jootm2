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
from .util import U_ARMRINGL, U_BUJUK, U_WEAPON, slot_accepts

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
BUTCH_BAG_RESERVE = 8           # 挖肉至少留 8 格空位；填太满就停挖，别把托管逼回城
CLEAN_INTERVAL_MS = 2_000       # 背包整理（丢最差品质的肉）检查间隔
MEAT_KEEP = 6                   # 背包常留多少份肉卖钱，超出部分按需丢最差的
EQUIP_CHECK_INTERVAL_MS = 1_000 # 武器/护身符自动补戴检查间隔

# 原版客户端施法输入节拍（MirClient/ClMain.pas:2040/2421-2428）：
# 500ms(ClientConf.wSpellTime，MShare.pas:415 默认 500) + 200ms(施法硬直)
# + 技能自身 Delay。托管直接调服务端 _cast_magic，绕过了客户端这层节流，
# 会比真人快一截。注意项目 MagicTemplate.delay 已把 DB 的 Delay ×10 转成
# 毫秒（templates.py:162，火球 60→600ms），而原版公式用的是 DB 原值，
# 所以还原成 t.delay / 10；实际间隔取「服务端技能 CD」与「客户端节拍」
# 的较大值。2026-11-18 用户反馈施法仍偏快，基础节拍 700→1100：
# 火球 max(600,1100+6)=1106≈1.2s；雷电 max(1000,1100+10)=1110≈1.2s。
SPELL_INPUT_BASE_MS = 1100

# 攻击/施法后的动作锁：原版 SendActMsg/SendSpellMsg 发出即置
# ActionLock=TRUE（ClMain.pas:3581/3592），CanNextAction 要求 IsIdle
# （:3975），动作未落地前不能移动。托管把「出刀/施法时刻」记进
# last_attack_at / last_cast_at，_step_to 在动作未完成时拒绝移动。
# 施法看 last_cast_at，近战看 last_attack_at，取各自原版节拍（毫秒）。

# 法师单体输出技能优先级：雷电术(11) > 大火球(5) > 火球术(1)。
# 三招都是 ``_resolve_magic`` 里对单目标生效的 RM_DELAYMAGIC（Magic.pas:279/407），
# 均不吃护身符；按「已学 + 有蓝 + 不在冷却」从高到低挑第一招。
MAGE_ATTACK_SKILLS = (11, 5, 1)

# 法师放风筝：被「强怪」贴身时不站着硬扛，先拉开到约 4 格再施法。
# 这是托管策略（原版无托管，玩家手动走位），不是原版对齐项，数值为可调经验值。
KITE_TRIGGER_DIST = 2          # 怪贴到 2 格内才考虑走位（切比雪夫）
KITE_SAFE_DIST = 4             # 拉开到 4 格就停手重新施法（仍在 8 格射程内）
KITE_STEP_MS = WALK_INTERVAL_MS  # 后撤走一步沿用走/跑节拍，不额外加速
# 强怪判定（满足任一）：攻击上限够疼 或 血量够厚，避免对鸡/稻草人无脑后退
KITE_STRONG_MAXDC = 12
KITE_STRONG_HP = 200

# ---- 默认策略档（管理员预置，玩家只选档）--------------------------------
PRESETS: dict[str, dict] = {
    "safe": {
        "name": "安全", "level_delta": 0, "sight": 9, "loot_sight": 6,
        "potion_hp": 0.70, "retreat_hp": 0.42,
        "max_target_dc": 12,
        "pickup_min_price": 0, "thrust": False, "butch": True,
        "desc": "只打不高于自身等级的怪，血线 70% 喝药、法师蓝不够放技能才喝，顺手挖肉赚钱",
    },
    "balanced": {
        "name": "均衡", "level_delta": 3, "sight": 12, "loot_sight": 8,
        "potion_hp": 0.55, "retreat_hp": 0.28,
        "max_target_dc": 18,
        "pickup_min_price": 0, "thrust": True, "butch": True,
        "desc": "默认档，允许高 3 级的怪，血线 55% 喝药、法师蓝不够放技能才喝，顺手挖肉赚钱",
    },
    "efficient": {
        "name": "效率", "level_delta": 8, "sight": 12, "loot_sight": 10,
        "potion_hp": 0.45, "retreat_hp": 0.20,
        "max_target_dc": 26,
        "pickup_min_price": 50, "thrust": True, "butch": False,
        "desc": "接受高 8 级的怪、只捡值钱物品，法师蓝不够放技能才喝，不挖肉专注升级",
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
    no_butch: bool = False          # 用户独立开关：勾选后任何档位都不挖肉
    active: bool = True
    reason: str = ""
    status: str = "托管已开启"
    started_at: int = 0
    kills: int = 0
    last_exp: int = 0
    last_progress_at: int = 0
    last_move_at: int = 0
    last_attack_at: int = 0
    last_cast_at: int = 0           # 上次施法：原版客户端 500+200+Delay 节拍
    last_cast_beat: int = 0         # 上次施法对应的动作锁时长（ms）
    kite_until: int = 0             # 放风筝走位目标锁过期时刻（避免来回抖动）
    kite_target_id: int = 0         # 正在放风筝拉开的那只怪
    last_pickup_at: int = 0
    last_potion_at: int = 0
    last_mp_potion_at: int = 0
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
    last_clean_at: int = 0         # 上次背包整理（丢低品质肉）时刻
    cleaning_meat: bool = False    # 已进入清肉模式：降到 MEAT_KEEP 份才退出
    last_equip_at: int = 0         # 上次自动补戴检查时刻
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
            self._ensure_equipment(srv, w, p, now)   # 武器/符损毁后自动补戴
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
        # 法师没有治愈术，蓝见底就只能站桩挨打。喝蓝改为「空蓝才喝」：
        # 当前蓝量（含 inc_spell 回蓝池）连最便宜的一招都放不出来时才补，
        # 不再按固定蓝线 35% 一掉就灌（用户 2026-11-18 反馈太激进）。
        if p.job == 1:
            need = _min_mage_spell_cost(w, p)
            if need is not None:
                available = p.mp + int(getattr(p, "inc_spell", 0) or 0)
                if available < need:
                    mana = _find_mana_potion(w, p)
                    if (mana is not None
                            and now - self.last_mp_potion_at >= POTION_INTERVAL_MS
                            and now_ms() - getattr(p, "last_use_item_at", 0) >= 300):
                        self.last_mp_potion_at = now
                        srv._use_item(p.session, {"type": "useItem",
                                                  "makeIndex": int(mana.get("makeIndex", 0))})
                        self.set_status("补充魔法值", now)
        # 背包压力：满包不再直接回城停机。先停挖肉（肉占格、金币不占），
        # 再按品质从差到好丢弃多余的肉，保住「挖肉赚钱」又避免打一会就回城。
        # 实在塞满（全是装备/药水等非肉物品）才退回原行为：回城并停机。
        if len(p.bag) >= BAG_SIZE - BUTCH_BAG_RESERVE or self.cleaning_meat:
            self._clean_meat(srv, w, p, now)
        if len(p.bag) >= BAG_SIZE:
            scroll = _find_town_scroll(w, p)
            if scroll is not None:
                srv._use_item(p.session, {"type": "useItem",
                                          "makeIndex": int(scroll.get("makeIndex", 0))})
                self.manager.disable(p, "背包已满，已用回城卷回城；补给后再开启托管")
            else:
                self.manager.disable(p, "背包已满且没有回城卷，请清理背包")
            return

    # ---- 背包整理：挖肉限流 + 丢最差品质的肉 --------------------------
    def _clean_meat(self, srv, w, p, now: int) -> None:
        """背包吃紧时丢掉品质最低的肉，保住最值钱的 MEAT_KEEP 份。

        肉的耐久即 ``m_nMeatQuality``（service.py:_butch 写入，卖价正比于品质，
        handlers_npc._user_item_price 的 StdMode 40 分支），所以「丢最差的、
        留最好的」既不亏经济，又能腾出格子继续挖/捡。走既有 ``_drop``
        handler（和手动丢弃同一条协议，不绕过校验）；一次只丢一件、间隔
        2s，避免 100ms tick 内把背包瞬间清空或刷屏。
        """
        if not self._butch_enabled(p) or now - self.last_clean_at < CLEAN_INTERVAL_MS:
            return
        meats = []
        for it in p.bag:
            t = _template(w, it)
            if t is not None and t.stdMode == 40:       # StdMode 40 = 肉/鸡肉
                meats.append((int(it.get("durability", 0) or 0), it))
        if len(meats) <= MEAT_KEEP:
            self.cleaning_meat = False
            return
        self.cleaning_meat = True
        meats.sort(key=lambda pair: pair[0])             # 品质差的排前
        worst = meats[0][1]
        self.last_clean_at = now
        srv._drop(p.session, {"type": "drop",
                              "makeIndex": int(worst.get("makeIndex", 0))})
        self.set_status("整理背包 · 丢弃低品质肉", now)

    def _butch_enabled(self, p) -> bool:
        """挖肉总开关：用户「不挖肉」勾选优先，再看预设、背包余量。"""
        return (not self.no_butch and bool(PRESETS[self.preset].get("butch"))
                and len(p.bag) < BAG_SIZE)

    # ---- 装备维护：武器/护身符损毁后从背包自动补戴 --------------------
    def _ensure_equipment(self, srv, w, p, now: int) -> None:
        """耐久归零的槽位自动从背包找同类补上，走 ``takeOnItem`` handler。

        原版 ``do_damage_weapon`` / ``_use_amulet`` 把耐久耗尽直接清槽， Clearing
        后玩家得手动从背包穿。托管期间无人值守，这里按「可用 + 耐久最高」
        自动补戴：

        * 武器槽（U_WEAPON，StdMode 5/6）—— 坏一把换下一把；
        * 护身符/毒粉槽（U_BUJUK 与 U_ARMRINGL，StdMode 25）—— 施法前补符，
          避免灵魂火符因为「没符在身上」一直回落近战。
        ``_take_on_slot`` 内部仍会校验槽位/等级/重量/回收旧装备，这里只做
        候选筛选，不绕过任何规则。检查按秒节流，不占满 100ms tick。
        """
        if now - self.last_equip_at < EQUIP_CHECK_INTERVAL_MS:
            return
        self.last_equip_at = now
        if p.equipment[U_WEAPON] is None:
            cand = _best_wearable(w, p, U_WEAPON, lambda t: t.stdMode in (5, 6))
            if cand is not None:
                srv._take_on_item(p.session, {
                    "type": "takeOnItem", "slot": U_WEAPON,
                    "makeIndex": int(cand.get("makeIndex", 0))})
                self.set_status("武器损毁 · 自动换装", now)
        self._ensure_amulet(srv, w, p, now)

    def _ensure_amulet(self, srv, w, p, now: int) -> None:
        """护身符（kind=1，Shape=5）/ 毒粉（kind=2，Shape<=2）即时补戴。

        原版 CheckAmulet 只认 U_BUJUK / U_ARMRINGL 两个槽，故只往这两处放。
        ``_taoist_fire`` 施法前也会调它，避免刚用完一张符就回落近战。
        """
        for kind, accept in ((1, lambda t: t.shape == 5),
                             (2, lambda t: t.shape <= 2)):
            if srv._find_amulet(p, kind, 1) is not None:
                continue
            for slot in (U_BUJUK, U_ARMRINGL):
                # 只补空槽：已在戴的槽不能覆盖，否则毒粉会把护身符顶回背包
                # （两者都属 StdMode 25、两槽都能放），越补越乱。
                if p.equipment[slot] is not None:
                    continue
                cand = _best_wearable(w, p, slot,
                                      lambda t, a=accept: t.stdMode == 25 and a(t))
                if cand is not None:
                    srv._take_on_item(p.session, {
                        "type": "takeOnItem", "slot": slot,
                        "makeIndex": int(cand.get("makeIndex", 0))})
                    self.set_status("符药耗尽 · 自动补充", now)
                    break

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

    def _spell_ready(self, t, now: int) -> bool:
        """原版客户端施法输入节拍：500ms(ClientConf.wSpellTime) + 200ms 硬直
        + 技能自身 Delay（MirClient/ClMain.pas:2040/2421-2428）。

        服务端 ``_cast_magic`` 只查技能表 Delay（项目里已是毫秒），那是
        原版 M2 的口径；客户端那层 500+200 的输入节流原本由真人点击
        天然满足，托管代发必须自己补，间隔取两者较大值。"""
        client_beat = SPELL_INPUT_BASE_MS + t.delay / 10
        return now - self.last_cast_at >= max(t.delay, client_beat)

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
        self.last_cast_at = now      # 治愈术也占客户端施法节拍，别紧接火符
        self.last_cast_beat = max(t.delay, int(SPELL_INPUT_BASE_MS + t.delay / 10))
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
        if not self._spell_ready(t, now):
            self.set_status(f"施法间隔 · {target.name}", now)
            return True
        if p.mp < spell_cost(t, skill_level(p, 13)):
            return False
        if srv._find_amulet(p, 1, 1) is None:
            # 符用光了：先即时从背包补一张（同一 tick 内生效，避免空放一次
            # 火符后被判定「没符」白回落近战）；补不到才回落近战。
            self._ensure_amulet(srv, w, p, now)
            if srv._find_amulet(p, 1, 1) is None:
                return False
        exp_before = p.exp
        srv._cast_magic(p.session, {"type": "castMagic", "magicId": 13,
                                    "targetId": target.id,
                                    "targetX": target.x, "targetY": target.y})
        self.last_attack_at = now
        self.last_cast_at = now
        self.last_cast_beat = max(t.delay, int(SPELL_INPUT_BASE_MS + t.delay / 10))
        self.set_status(f"火符 · {target.name}", now)
        if p.exp != exp_before:
            self.kills += 1
            self.last_exp = p.exp
            self.last_progress_at = now
        return True

    def _mage_fire(self, srv, w, p, target, now: int) -> bool:
        """法师远程单体输出：已学 + 有蓝 + 冷却好的最高优先级技能。

        返回 True = 本 tick 已被远程施法占用（含冷却中保持站位）；
        False = 一招都放不出（没学 / 没蓝），调用方**不再贴身砍怪**——
        法师物理攻击极低，近战等于送死，改为等回蓝 / 走近至射程。

        ``_cast_magic`` 会再次校验 MP、冷却、8 格半径（Magic.pas:266），
        这里自查只是为了避免刷「魔法值不足」提示和空耗一个 tick。
        """
        if p.job != 1 or not target.alive:
            return False
        stamp = now_ms()
        for magic_id in MAGE_ATTACK_SKILLS:
            if magic_id not in p.skills:
                continue
            t = w.magic_templates.get(magic_id)
            if t is None:
                continue
            if stamp - p.skill_cooldown.get(magic_id, 0) < t.delay:
                self.set_status(f"打怪中 · {target.name}", now)
                return True             # 技能冷却中保持站位，不贴身
            if not self._spell_ready(t, now):
                self.set_status(f"施法间隔 · {target.name}", now)
                return True             # 客户端节拍未到：不连发，也别贴身
            if p.mp < spell_cost(t, skill_level(p, magic_id)):
                continue                # 这招没蓝，试下一招
            exp_before = p.exp
            srv._cast_magic(p.session, {"type": "castMagic",
                                        "magicId": magic_id, "targetId": target.id,
                                        "targetX": target.x, "targetY": target.y})
            self.last_attack_at = now
            self.last_cast_at = now
            self.last_cast_beat = max(t.delay, int(SPELL_INPUT_BASE_MS + t.delay / 10))
            self.set_status(f"{t.name} · {target.name}", now)
            if p.exp != exp_before:
                self.kills += 1
                self.last_exp = p.exp
                self.last_progress_at = now
            return True
        return False

    def _kite_worthy(self, w, target) -> bool:
        """是否值得为这只怪放风筝。弱鸡（鸡/鹿/稻草人）一发就秒，后退纯
        浪费输出；只有攻击够疼或血够厚的怪贴身才有走位价值。
        判定只读模板字段（maxDc/hp），不估算技能伤害，稳定可预期。"""
        t = target.template
        if t is None:
            return False
        return int(getattr(t, "maxDc", 0) or 0) >= KITE_STRONG_MAXDC \
            or int(getattr(t, "hp", 0) or 0) >= KITE_STRONG_HP

    def _kite_step(self, srv, w, p, target, now: int) -> bool:
        """被强怪贴身时后撤一步，拉开到 KITE_SAFE_DIST 再恢复施法。

        返回 True = 本 tick 已被走位占用（含「该走但还在硬直/走位节拍未到」
        的等待），调用方不要再施法；False = 已拉开到安全距离 / 无路可退，
        回落正常施法。方向从 8 邻格挑「可走且离怪更远」的最佳一格；若所有
        方向都拉不开（墙角被堵），放弃走位硬扛，绝不原地抖动。
        """
        dist = p.distance_to(target)
        if dist >= KITE_SAFE_DIST:
            self.kite_until = 0
            self.kite_target_id = 0
            return False
        # 走位目标锁：期限内持续为这只怪拉开，避免怪晃一下就换目标来回横跳。
        if self.kite_target_id != target.id and now < self.kite_until:
            return False
        # 施法/出刀硬直期不能移动（对齐原版 ActionLock）；但保留走位意图，
        # 下一 tick 硬直落地再撤。设置状态方便玩家看懂「为何Stand住」。
        if self._action_busy(p, now):
            self.set_status("放风筝 · 等待动作落地", now)
            return True
        if now - self.last_move_at < KITE_STEP_MS:
            return True                      # 走位节拍未到，保持占位不施法
        gm = p.map
        if gm is None or now_ms() - getattr(p, "map_change_at", 0) < 350:
            return True
        # 挑离怪最远的可走邻格；同距离时偏好不超出射程、且与上次方向一致。
        best_dir, best_dist = -1, dist
        for d in range(8):
            dx, dy = direction_delta(d)
            nx, ny = p.x + dx, p.y + dy
            if not _walkable(gm, nx, ny):
                continue
            nd = max(abs(nx - target.x), abs(ny - target.y))
            if nd > best_dist:
                best_dir, best_dist = d, nd
        if best_dir < 0:
            # 被墙角堵死，拉不开：放弃走位，回落施法（站桩硬扛好过原地抖）
            self.kite_until = 0
            self.kite_target_id = 0
            return False
        self.kite_target_id = target.id
        self.kite_until = now + 6_000
        self.last_move_at = now
        srv._move(p.session, {"type": "move", "dir": best_dir, "run": False})
        self.set_status(f"放风筝 · 拉开到 {KITE_SAFE_DIST} 格", now)
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
            # 关键：本 tick 的移动权已被「走向拾取物」占用，必须 return。
            # 走（540ms）与跑（360ms）共用 last_move_at 节拍，若继续往下
            # 走到选怪逻辑再 _step_to 一次，怪步总能在拾取两步之间插进来
            # ——玩家在物品格 1~2 格外来回掉头，永远踩不上物品格（实测
            # 震荡复现）。仅当 _step_to 放弃目标（False=已拉黑释放）才
            # 接着选怪，避免空耗一拍。
            if self._step_to(srv, w, p, loot.x, loot.y,
                             ("loot", loot.id), f"捡{label}", now, exact=True):
                return

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
        # 法师远程（Magic.pas:266 施法半径 8 格）：进射程优先法术，没蓝/没学
        # 不贴身砍（法师物理输出极低），原地等回蓝；超射程照常走近。
        if p.job == 1:
            if dist <= 8:
                # 被强怪贴身：先拉开距离再施法（法师脆皮，站着硬扛会很快死）。
                # 硬直期动作锁会挡住走位，此时不施法也不动，等动作落地那一拍
                # 再撤——否则刚施完法就想跑，原版动作锁不允许（上一轮对齐项）。
                # 一旦进入放风筝（kite_target_id 锁未过期）就持续撤到 4 格，
                # 不能怪刚被拉开到 3 格就停手，否则永远贴着射程边缘挨打。
                kiting = (self.kite_target_id == target.id
                          and now < self.kite_until)
                if kiting or (dist <= KITE_TRIGGER_DIST
                              and self._kite_worthy(w, target)):
                    if self._kite_step(srv, w, p, target, now):
                        return
                if self._mage_fire(srv, w, p, target, now):
                    return
                self.set_status("法力不足 · 等待回蓝", now)
                return
            self._step_to(srv, w, p, target.x, target.y,
                          ("monster", target.id), f"前往 {target.name}", now)
            return
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
        if self.no_butch or not cfg.get("butch"):
            return False
        # 限流：背包只剩 few 格就别再挖肉塞满，继续打怪捡金币（不占格）。
        if len(p.bag) >= BAG_SIZE - BUTCH_BAG_RESERVE:
            self.butch_id = 0
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

        动作锁：原版 ``CanNextAction`` 要求 ``IsIdle``（ClMain.pas:3975），
        ``SendActMsg/SendSpellMsg`` 发出即置 ``ActionLock=TRUE``（:3581/:3592），
        出刀/施法动作未落地前不接受移动。托管刚出刀或施法的一拍内原地不动，
        等动作完成再走；但「走向拾取物」也走这里，锁只挡移动、不放弃目标。
        """
        if self._action_busy(p, now):
            return True                  # 动作未落地：本 tick 保持站位，目标不变
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

    def _action_busy(self, p, now: int) -> bool:
        """原版 ``ActionLock``：出刀/施法落地前不能移动。

        两条动作时间线取其一：近战出刀看 ``last_attack_at``（节拍 =
        攻速 ``attack_interval_ms``）；远程施法看 ``last_cast_at``（节拍 =
        ``last_cast_beat`` = 500+200+Delay 与服务端 CD 取大）。两者都可能
        更新在最近一拍，取「离现在更近、且节拍未走完」的那个为准。
        注意 ``last_attack_at`` 在火符/法师施法时也会更新（同一动作），
        不能把近战节拍和施法节拍叠加，否则会锁出比原版更长的硬直。
        """
        if self.last_cast_at >= self.last_attack_at:
            beat = self.last_cast_beat
            stamp = self.last_cast_at
        else:
            beat = attack_interval_ms(p.level)
            stamp = self.last_attack_at
        return beat > 0 and 0 <= now - stamp < beat

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


def _find_mana_potion(w, p):
    """从背包挑一瓶加蓝药（StdMode 0 且 MAC>0）。

    与 `_find_potion`（血药）对称：Shape1 太阳水即时回蓝优先，其余按回复量
    从小到大，别把大蓝瓶浪费在小掉蓝上。蓝线判断会把 inc_spell 池算进去，
    所以这里拿到的药是真需要喝的。"""
    best, best_key = None, None
    for it in p.bag:
        t = _template(w, it)
        if t is None or t.stdMode != 0 or t.macMin <= 0:
            continue
        key = (0 if t.shape == 1 else 1, t.macMin)
        if best_key is None or key < best_key:
            best, best_key = it, key
    return best


def _min_mage_spell_cost(w, p):
    """法师已学单体技能里的最低耗蓝；一招都没学返回 None（无需补蓝）。

    「空蓝才喝」的判据：当前蓝量（含回蓝池）连最便宜的一招都放不出来，
    才值得喝蓝药。`spell_cost` 与 `_cast_magic` 用同一公式
    （handlers_combat.py:60），避免判据和实际扣蓝口径不一致。
    """
    best = None
    for magic_id in MAGE_ATTACK_SKILLS:
        if magic_id not in p.skills:
            continue
        t = w.magic_templates.get(magic_id)
        if t is None:
            continue
        cost = spell_cost(t, skill_level(p, magic_id))
        if best is None or cost < best:
            best = cost
    return best


def _best_wearable(w, p, slot: int, accept):
    """从背包挑一件可放进 ``slot`` 且满足 accept(t) 的装备，耐久高者优先。

    只做候选筛选（StdMode 槽位匹配 + 耐久排序）；等级/重量/性别等硬门槛
    由 ``_take_on_slot._can_take_on`` 统一校验，这里不重复也不绕过。
    """
    best, best_dura = None, -1
    for it in p.bag:
        t = _template(w, it)
        if t is None or not slot_accepts(slot, t.stdMode) or not accept(t):
            continue
        dura = int(it.get("durability", 0) or 0)
        if dura > best_dura:
            best, best_dura = it, dura
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
    # 肉（StdMode 40）/ 毒药 / 占位垃圾：肉本来就是挖肉直接进包，地上的肉
    # 多半是自己清包丢掉的，捡回来等于白清，托管一律不主动捡。
    if t.stdMode in (1, 2, 40):
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

    def enable(self, p, preset=None, now: int | None = None,
               no_butch: bool | None = None) -> tuple[bool, str]:
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
        if no_butch is not None:
            pilot.no_butch = bool(no_butch)
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
        pilot.last_cast_at = 0
        pilot.last_cast_beat = 0
        pilot.kite_until = 0
        pilot.kite_target_id = 0
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
            "noButch": bool(pilot.no_butch) if pilot else False,
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
                        "preset": pilot.preset, "noButch": pilot.no_butch,
                        "status": pilot.status,
                        "kills": pilot.kills, "exp": p.exp,
                        "map": p.map.name if p.map else "",
                        "x": p.x, "y": p.y, "hp": p.hp, "maxHp": p.max_hp,
                        "elapsedMs": max(0, now_ms() - pilot.started_at)})
        return out
