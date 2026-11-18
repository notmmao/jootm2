"""AI Bot 玩家（活跃气氛的假人）：docs/OFFLINE-AFK-PLAN.md §4。

与离线修炼共用 AutoPilot 决策内核（打怪型 Bot 走 safe 档 + ``avoid_real_targets``
决不抢真人目标锁），城内 Bot 只做「漫游 + 台词」。特点：

* **不落库**：Bot 是虚构角色，重启按 ``gamedata/bots.json`` 重建。
* **氛围优先，效率靠后**：打得慢、说话随机，像真人新手，不做任何加速。
* **与真人交互边界**：可被 PK（走正常规则，死亡回城复活继续）、不回应交易/组队；
  聊天是纯规则触发（定时 + 随机台词库），不接 LLM。
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .afk import NullSession
from .autopilot import (AutoPilot, WALK_INTERVAL_MS, _walkable, normalize_preset)
from .world import Player, direction_delta, direction_toward, now_ms

DEFAULT_CHAT_EVERY = (180, 420)      # 台词间隔区间（秒）
REVIVE_DELAY_MS = 10_000             # Bot 死亡后躺尸几秒再回城复活


def _load_config(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [b for b in data if isinstance(b, dict) and b.get("name")]


class BotPilot(AutoPilot):
    """打怪型 Bot 的决策循环：safe 档 + 决不抢真人目标。"""

    avoid_real_targets: bool = True     # 类级默认（slots dataclass 字段默认值）

    def adjust_cfg(self, cfg: dict) -> dict:
        adjusted = dict(cfg)
        adjusted["loot_sight"] = 2      # Bot 捡东西很随意（氛围优先）
        adjusted["pickup_min_price"] = 0
        return adjusted


class Bot:
    def __init__(self, manager: "BotManager", p: Player, cfg: dict):
        self.manager = manager
        self.p = p
        self.cfg = cfg
        self.mode = str(cfg.get("mode") or "chat")      # chat=城里漫游 / fight=野外打怪
        self.anchor = (p.x, p.y)
        self.radius = int(cfg.get("wanderRadius") or 10)
        self.last_move_at = 0
        self.next_chat_at = now_ms() + random.randint(30, 120) * 1000
        self.chat_every = cfg.get("chatEvery") or DEFAULT_CHAT_EVERY
        self.lines = cfg.get("lines") or {}
        self.revive_at = 0
        self.pilot: BotPilot | None = None

    # ---- 漫游 ----------------------------------------------------------
    def tick(self, now: int) -> None:
        p = self.p
        if getattr(p, "dead", False) or not p.alive:
            if not self.revive_at:
                self.revive_at = now + REVIVE_DELAY_MS
            elif now >= self.revive_at:
                self.revive_at = 0
                self.manager.revive(p)
            return
        if self.mode == "fight":
            self._tick_fight(now)
        self._tick_wander(now)
        self._tick_chat(now)

    def _tick_fight(self, now: int) -> None:
        mgr = self.manager.service.autopilot
        pilot = mgr.get(self.p)
        if pilot is None or not pilot.active:
            mgr.enable(self.p, normalize_preset(self.cfg.get("preset") or "safe"))
            pilot = mgr.get(self.p)
            if pilot is not None:
                pilot.avoid_real_targets = True
        # 决策 tick 由 service.autopilot.tick 统一驱动（这里只负责补挂/补参数，
        # 双重 tick 会破坏 540ms 走路节拍）。

    def _tick_wander(self, now: int) -> None:
        if now - self.last_move_at < WALK_INTERVAL_MS:
            return
        p = self.p
        gm = p.map
        if gm is None or now_ms() - getattr(p, "map_change_at", 0) < 350:
            return
        self.last_move_at = now
        ax, ay = self.anchor
        dist = max(abs(p.x - ax), abs(p.y - ay))
        if dist >= self.radius or (dist > 2 and random.random() < 0.35):
            d = direction_toward(p.x, p.y, ax, ay, random.randrange(8))
        else:
            d = random.randrange(8)
        dx, dy = direction_delta(d)
        if not _walkable(gm, p.x + dx, p.y + dy):
            return
        self.manager.service._move(p.session, {"type": "move", "dir": d, "run": False})

    def _tick_chat(self, now: int) -> None:
        if now < self.next_chat_at:
            return
        lo, hi = self.chat_every[:2] if len(self.chat_every) >= 2 else DEFAULT_CHAT_EVERY
        self.next_chat_at = now + random.randint(int(lo), int(hi)) * 1000
        pool = self.lines.get("idle") if isinstance(self.lines, dict) else None
        if not pool:
            return
        line = random.choice(pool)
        self.manager.service.world.broadcast_all({
            "type": "chat", "id": self.p.id, "name": self.p.name, "text": line[:200]})


class BotManager:
    """Bot 池：启动按 bots.json 重建，GM @bot 命令管理，service.tick 驱动。"""

    def __init__(self, service: Any, config_path: Path):
        self.service = service
        self.config_path = config_path
        self._bots: dict[int, Bot] = {}     # key = Player.id

    # ---- 装载 / 重建 ----------------------------------------------------
    def rebuild(self) -> None:
        for p in list(self._bots.values()):
            self.remove(p.p)
        self._bots.clear()
        defs = _load_config(self.config_path)
        w = self.service.world
        for cfg in defs:
            p = self._spawn(w, cfg)
            if p is not None:
                self._bots[p.id] = Bot(self, p, cfg)
        if defs:
            print(f"[bot] 已生成 {len(self._bots)}/{len(defs)} 个 AI Bot")

    def _spawn(self, w, cfg: dict) -> Player | None:
        name = str(cfg.get("name") or "").strip()
        gm = w.get_or_load_map(str(cfg.get("map") or "0"))
        if not name or gm is None:
            print(f"[bot] 跳过无效配置: {cfg.get('name')}")
            return None
        pos = w._free_near(gm, int(cfg.get("x") or gm.width // 2),
                           int(cfg.get("y") or gm.height // 2),
                           int(cfg.get("spawnRadius") or 6))
        if pos is None:
            print(f"[bot] {name} 找不到可站立坐标（map={gm.name}）")
            return None
        job = int(cfg.get("job") or 0)
        level = max(1, int(cfg.get("level") or 10))
        p = Player(id=w.new_id(), name=name, x=pos[0], y=pos[1], dir=4,
                   hp=1, mp=1, level=level, exp=0, account_id=0,
                   character_id=0, sex=int(cfg.get("sex") or 0),
                   hair=int(cfg.get("hair") or 1), job=job, gold=0,
                   home_map=gm.name, home_x=pos[0], home_y=pos[1],
                   map=gm, session=NullSession(), world_ref=w,
                   is_bot=True)
        p.afk_offline = False
        w.add_player(p)
        w.player_entered(p)
        p.recompute(w)
        # 原版升级不回满，这里按「满状态生成」（Bot 是氛围道具，不必抠血条）
        p.hp, p.mp = p.max_hp, p.max_mp
        self._dress(p, cfg)
        w.broadcast_entity_add(p)
        return p

    def _dress(self, p: Player, cfg: dict) -> None:
        """给 Bot 发基础装备并**全槽位**自动穿戴（用户 2026-11-18 反馈 Bot 全裸）。

        旧实现只自动穿武器，衣服躺在背包没人穿；城里 Bot 的配置甚至没配
        items。逐槽位从背包挑能穿的（等级/性别等硬门槛由 takeOnItem handler
        统一校验，穿不上就留背包，不绕过校验）。"""
        items = cfg.get("items")
        if not items:
            items = ["木剑", "布衣(男)" if p.sex == 0 else "布衣(女)"]
        w = self.service.world
        for name in items:
            w.give_item(p, str(name), 1)
        from .autopilot import _best_wearable
        for slot in range(13):
            cand = _best_wearable(w, p, slot, lambda t: True)
            if cand is not None:
                self.service._take_on_item(p.session, {
                    "type": "takeOnItem", "slot": slot,
                    "makeIndex": int(cand.get("makeIndex", 0))})

    def remove(self, p: Player) -> None:
        pilot = self.service.autopilot.get(p)
        if pilot is not None:
            self.service.autopilot.disable(p, "Bot 已移除")
        w = self.service.world
        w.broadcast_around(p.map, {"type": "entityRemove", "id": p.id},
                           p.x, p.y, getattr(w, "SIGHT_RANGE", 12))
        w.remove_player(p)

    def revive(self, p: Player) -> None:
        """Bot 死亡复活：回出生点满状态继续溜达（已拍板「自动复活」语义）。"""
        w = self.service.world
        home = w.get_or_load_map(p.home_map) or w.default_map
        p.dead = False
        p.alive = True
        p.poison_ticks = 0
        p.inc_health = p.inc_spell = 0
        p.target_id = -1
        if w.space_move(p, home.name, p.home_x, p.home_y) is not None:
            w.broadcast_entity_add(p)
        p.hp, p.mp = p.max_hp, p.max_mp
        p.recompute(w)
        pilot = self.service.autopilot.get(p)
        if pilot is not None:
            pilot.last_exp = p.exp
            pilot.last_bag = len(p.bag)
            pilot.last_gold = p.gold
            pilot.last_progress_at = now_ms()
            pilot.path.clear()
            pilot.path_key = ()
            pilot.target_id = 0

    # ---- tick ----------------------------------------------------------
    def tick(self, now: int) -> None:
        if not self._bots:
            return
        for bot in list(self._bots.values()):
            try:
                bot.tick(now)
            except Exception as e:
                print(f"[bot] {bot.p.name} tick 异常: {type(e).__name__}: {e}")
                self.revive(bot.p)

    # ---- 查询 ----------------------------------------------------------
    def all(self) -> list[Bot]:
        return list(self._bots.values())

    def by_name(self, name: str) -> Bot | None:
        return next((b for b in self._bots.values() if b.p.name == name), None)

    def say(self, name: str, text: str) -> bool:
        bot = self.by_name(name)
        if bot is None:
            return False
        self.service.world.broadcast_all({
            "type": "chat", "id": bot.p.id, "name": bot.p.name, "text": text[:200]})
        return True
