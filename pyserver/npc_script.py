"""NPC 脚本（Npc_Def / Market_Def）解析与执行引擎。

直译 Java ``NpcScript`` + ``NpcScriptEngine``：``[@label]`` 分段、
``#IF/#ACT/#SAY/#ELSEACT/#ELSESAY`` 状态机、条件与动作命令子集，
以及 ``[goods]`` 商店货物表。脚本文件老版是 GBK，现在由
``gamedata.read_lines`` 按内容自动识别（UTF-8 优先、回落 GBK），两种都能读。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gamedata import read_lines
from .util import BAG_SIZE, delphi_round

MAX_DEPTH = 64


@dataclass(slots=True)
class Cmd:
    name: str = ""
    param: str = ""
    tag: str = ""
    extra: str = ""

    def int_at(self, which: str, default: int = 0) -> int:
        try:
            return int(getattr(self, which).strip())
        except (ValueError, AttributeError):
            return default


@dataclass(slots=True)
class TextStmt:
    text: str = ""


@dataclass(slots=True)
class IfStmt:
    conditions: list[Cmd] = field(default_factory=list)
    actions: list[Cmd] = field(default_factory=list)
    else_actions: list[Cmd] = field(default_factory=list)
    say_text: str | None = None
    else_say_text: str | None = None


@dataclass(slots=True)
class Section:
    label: str
    stmts: list[Any] = field(default_factory=list)


def _split_quoted(line: str) -> list[str]:
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        while i < n and line[i].isspace():
            i += 1
        if i >= n:
            break
        if line[i] == '"':
            end = line.find('"', i + 1)
            if end < 0:
                out.append(line[i:])
                break
            out.append(line[i + 1:end])
            i = end + 1
        else:
            start = i
            while i < n and not line[i].isspace():
                i += 1
            out.append(line[start:i])
    return out


def _decode(line: str) -> Cmd:
    p = _split_quoted(line)
    return Cmd(*(p + ["", "", "", ""])[:4])


class NpcScript:
    def __init__(self):
        self.sections: dict[str, Section] = {}
        # M2 ``LocalDB.pas:3251-3256``：``%N`` 是 m_nPriceRate，**只有 N >= 55
        # 才接受**，否则保持默认 100；``+N`` 加入 m_ItemTypeList（CheckItemType），
        # 是 ``TMerchant.GetItemPrice`` 在没有价格记录时的 StdMode 过滤条件。
        self.price_rate: int = 100
        self.item_types: list[int] = []
        # 脚本头 ``(@buy @sell @repair @s_repair)`` 声明商人能力。原版
        # LocalDB.pas:3240-3250 由此置 m_boBuy/m_boSell/m_boRepair/m_boS_repair，
        # TMerchant.UserSelect 按标签开对应窗口、ClientRepairItem 再校验一次。
        self.abilities: set[str] = set()

    def section(self, label: str) -> Section | None:
        sec = self.sections.get(label)
        if sec is not None:
            return sec
        low = label.lower()
        return next((v for k, v in self.sections.items() if k.lower() == low), None)

    def section_count(self) -> int:
        return len(self.sections)

    @classmethod
    def parse(cls, lines: list[str]) -> "NpcScript":
        script = cls()
        cur: Section | None = None
        state = "NONE"
        cur_if: IfStmt | None = None
        text_buf: list[str] = []

        def flush():
            nonlocal state, cur_if
            text = "\n".join(text_buf)
            text_buf.clear()
            if not text or cur is None:
                return
            if cur_if is not None and state in ("IF_SAY", "ELSE_SAY"):
                if state == "IF_SAY":
                    cur_if.say_text = (cur_if.say_text + "\n" + text) if cur_if.say_text else text
                else:
                    cur_if.else_say_text = ((cur_if.else_say_text + "\n" + text)
                                            if cur_if.else_say_text else text)
            else:
                cur.stmts.append(TextStmt(text))

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if line in ("{", "}"):
                continue   # Gee 引擎的文本块起止标记（Delphi 原版无此语法）
            if line.startswith("[") and line.endswith("]"):
                flush()
                cur_if = None
                label = line[2:-1].strip() if line.startswith("[@") else line[1:-1].strip()
                cur = Section(label)
                script.sections[label] = cur
                state = "NONE"
                continue
            # 脚本头（位于首个 [@段] 之前）：价格倍率 ``%100`` 与商品类型 ``+5``。
            # 原版 LocalDB.pas:3251-3268 逐行解析，%rate 只有 >=55 才写入。
            if cur is None:
                if line.startswith('%'):
                    m = re.search(r"%\s*(\d+)", line)
                    if m:
                        r = int(m.group(1))
                        if r >= 55:
                            script.price_rate = max(1, min(1000, r))
                elif line.startswith('+'):
                    try:
                        n = int(line[1:].strip())
                        if n >= 0:
                            script.item_types.append(n)
                    except ValueError:
                        pass
                elif line.startswith('('):
                    # 能力声明头，如 ``(@buy @sell @repair @s_repair )``
                    for tag in re.findall(r"@([A-Za-z_]+)", line):
                        script.abilities.add(tag.lower())
                continue
            if line.startswith("#"):
                upper = line.upper()
                if upper.startswith("#IF"):
                    flush()
                    cur_if = IfStmt()
                    cur.stmts.append(cur_if)
                    state = "IF_COND"
                elif upper.startswith("#ACT"):
                    flush()
                    if cur_if is None:
                        cur_if = IfStmt()
                        cur.stmts.append(cur_if)
                    state = "IF_ACT"
                elif upper.startswith("#SAY"):
                    flush()
                    if cur_if is None:
                        cur_if = IfStmt()
                        cur.stmts.append(cur_if)
                    state = "IF_SAY"
                elif upper.startswith("#ELSEACT"):
                    flush()
                    if cur_if is None:
                        cur_if = IfStmt()
                        cur.stmts.append(cur_if)
                    state = "ELSE_ACT"
                elif upper.startswith("#ELSESAY"):
                    flush()
                    if cur_if is None:
                        cur_if = IfStmt()
                        cur.stmts.append(cur_if)
                    state = "ELSE_SAY"
                elif upper.startswith("#CALL"):
                    # Gee/传奇流通引擎的脚本引用：#CALL [商人\肉店.txt] @肉店
                    # （Delphi 原版无此指令；1.76 资料站包的商店正文全在
                    # QuestDiary 子脚本里，独立成行等价于直接执行）
                    flush()
                    if cur_if is None:
                        cur_if = IfStmt()
                        cur.stmts.append(cur_if)
                    cur_if.actions.append(_decode(line))
                continue
            if state == "IF_COND" and cur_if is not None:
                cur_if.conditions.append(_decode(line))
            elif state == "IF_ACT" and cur_if is not None:
                cur_if.actions.append(_decode(line))
            elif state == "ELSE_ACT" and cur_if is not None:
                cur_if.else_actions.append(_decode(line))
            else:
                text_buf.append(line)
        flush()
        return script

    def goods_rows(self) -> list[list[str]]:
        sec = self.section("goods")
        if sec is None:
            return []
        rows: list[list[str]] = []
        for stmt in sec.stmts:
            if not isinstance(stmt, TextStmt):
                continue
            for line in stmt.text.split("\n"):
                t = line.strip()
                if t and not t.startswith(";"):
                    parts = t.split()
                    # 原版 LocalDB.pas:3261-3268 用 GetValidStrCap 取三列，
                    # 且 `if (s48 <> '') and (s50 <> '')` 才登记：名字与
                    # 刷新时间缺一不可。少列的行是残缺配置，直接忽略。
                    if len(parts) >= 3:
                        rows.append(parts)
        return rows


def load_script(directory: Path, name: str, map_id: str) -> NpcScript | None:
    """与 NpcData.loadScript 一致：优先 ``name-map.txt``，再 ``name.txt``，大小写不敏感。"""
    if not directory.is_dir():
        return None
    for candidate in (directory / f"{name}-{map_id}.txt", directory / f"{name}.txt"):
        if candidate.is_file():
            return NpcScript.parse(read_lines(candidate))
    want = f"{name}-{map_id}.txt".lower()
    for p in directory.iterdir():
        if p.is_file() and p.name.lower() == want:
            return NpcScript.parse(read_lines(p))
    return None


@dataclass(slots=True)
class Result:
    say_text: str = ""
    options: list[dict] = field(default_factory=list)
    closed: bool = False
    moved: bool = False
    moved_map: Any = None
    moved_x: int = 0
    moved_y: int = 0
    shop_mode: str | None = None
    # 修理模式（原版 ObjNpc.pas:1419-1465 UserSelect 对 @repair / @s_repair
    # 发 RM_SENDUSERREPAIR / RM_SENDUSERSREPAIR → 客户端 SpotDlgMode=dmRepair
    # + ShowShopSellDlg，ClMain.pas:6235-6241）："repair" / "s_repair"。
    repair_mode: str | None = None
    # 仓库模式（原版 ObjNpc.pas:1560-1566 UserSelect 对 @storage/@getback 标签
    # 的处理 → SM_SENDUSERSTORAGEITEM 开投放框 / SM_SAVEITEMLIST 开列表）：
    # "deposit" = 存入（点背包物品直接发 storageStore），"getback" = 取出列表。
    storage_mode: str | None = None


class NpcScriptEngine:
    def __init__(self, world):
        self.world = world
        self._subs: dict[str, NpcScript | None] = {}   # QuestDiary #CALL 子脚本缓存
        # 脚本 PARAM1/PARAM2/PARAM3（原版 NPC 的 m_sParam1/m_nParam2/m_nParam3，
        # ObjNpc.pas nSC_PARAM1..3：存在 NPC 实例上，供 MonGen 用）。
        self._npc_params: dict[int, dict] = {}

    def _params(self, npc) -> dict:
        return self._npc_params.setdefault(npc.id, {"map": "", "x": 0, "y": 0})

    def execute(self, p, npc, start_label: str) -> Result:
        script = npc.script if npc else None
        # Gee 风格：NPC 主脚本只有 #CALL，按钮标签(@buy/@sell)住在子脚本里。
        # 主脚本查不到该 label 时，回退到它 #CALL 引用的子脚本中找。
        if script is not None:
            lbl = (start_label or "main").lstrip("@")
            if script.section(lbl) is None:
                for sub in self._called_subs(script):
                    if sub.section(lbl) is not None:
                        script = sub
                        break
        return self.execute_script(p, npc, script, start_label)

    def _load_sub(self, rel: str) -> "NpcScript | None":
        if rel in self._subs:
            return self._subs[rel]
        base = self.world.config.envir_dir / "QuestDiary"
        sub = None
        if base.is_dir():
            want = rel.replace("\\", "/")
            cand = base / want
            if not cand.is_file():
                lw = want.lower()
                cand = next((p for p in base.rglob("*.txt")
                             if p.as_posix().lower().endswith(lw)), None)
            if cand is not None and cand.is_file():
                sub = NpcScript.parse(read_lines(cand))
        self._subs[rel] = sub
        return sub

    def _called_subs(self, script: NpcScript) -> list[NpcScript]:
        subs, seen = [], set()
        for sec in script.sections.values():
            for st in sec.stmts:
                if not isinstance(st, IfStmt):
                    continue
                for c in list(st.actions) + list(st.else_actions):
                    if c.name.lower() in ("#call", "call"):
                        rel = c.param.strip().strip("[]").strip('"')
                        if rel and rel not in seen:
                            seen.add(rel)
                            s = self._load_sub(rel)
                            if s is not None:
                                subs.append(s)
        return subs

    def execute_script(self, p, npc, script, start_label: str) -> Result:
        r = Result()
        label = start_label or "main"
        if label.startswith("@"):
            label = label[1:]
        depth = 0
        while label is not None and depth < MAX_DEPTH:
            depth += 1
            sec = script.section(label) if script is not None else None
            if sec is None:
                break
            ctrl = self._run_section(p, npc, sec, r)
            if r.closed or r.moved:
                break
            if ctrl is None or ctrl == "BREAK" or not ctrl.startswith("@"):
                break
            if ctrl.startswith("@SUB\t"):      # #CALL 跳子脚本（Gee 引用）
                _, rel, lbl = ctrl.split("\t")
                sub = self._load_sub(rel)
                if sub is None:
                    break
                script, label = sub, lbl
                continue
            label = ctrl[1:]
        return r

    def _run_section(self, p, npc, sec: Section, r: Result) -> str | None:
        for stmt in sec.stmts:
            if isinstance(stmt, TextStmt):
                self._append_say(r, stmt.text, p)
            elif isinstance(stmt, IfStmt):
                ok = all(self._eval_condition(p, npc, c) for c in stmt.conditions)
                if ok:
                    if stmt.say_text is not None:
                        self._append_say(r, stmt.say_text, p)
                    ctrl = self._run_actions(p, npc, stmt.actions, r)
                else:
                    if stmt.else_say_text is not None:
                        self._append_say(r, stmt.else_say_text, p)
                    ctrl = self._run_actions(p, npc, stmt.else_actions, r)
                if ctrl is not None:
                    return ctrl
        return None

    @staticmethod
    def _append_say(r: Result, raw: str, p) -> None:
        text = raw.replace("\\", "\n").replace("<$USERNAME>", p.name) \
                  .replace("<$USERGOLD>", str(p.gold))
        clean: list[str] = []
        i = 0
        while i < len(text):
            lt = text.find("<", i)
            if lt < 0:
                clean.append(text[i:])
                break
            gt = text.find(">", lt)
            if gt < 0:
                clean.append(text[i:])
                break
            clean.append(text[i:lt])
            inner = text[lt + 1:gt]
            clean.append("<" + inner + ">" if "@" in inner else inner)
            i = gt + 1
        lines = [t.strip() for t in "".join(clean).split("\n") if t.strip()]
        cleaned = "\n".join(lines)
        if cleaned:
            r.say_text = (r.say_text + "\n" + cleaned) if r.say_text else cleaned

    def _quest_index(self, param: str) -> int:
        s = (param or "").strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        try:
            n = int(s)
            return n if 0 <= n < 1000 else 0
        except ValueError:
            return 0

    def _quest_get(self, p, idx: int) -> int:
        return p.quest_params.get(idx, 0)

    def _eval_condition(self, p, npc, c: Cmd) -> bool:
        n = c.name.lower()
        if n == "checkgold":
            return p.gold >= c.int_at("param")
        if n == "checklevel":
            return p.level >= c.int_at("param")
        if n in ("checkitem", "checkitemw"):
            return self.world.count_item(p, c.param) >= c.int_at("tag", 1)
        if n == "checkpkpoint":
            return getattr(p, "pk_point", 0) >= c.int_at("param")
        if n == "checkjob":
            s = c.param.lower()
            if s.startswith("war"):
                return p.job == 0
            if s.startswith("wiz"):
                return p.job == 1
            if s.startswith("tao"):
                return p.job == 2
            return True
        if n == "gender":
            return p.sex == (0 if c.param.lower() == "man" else 1)
        if n == "check":
            # 原版 QI_CHECK（ObjNpc.pas:670-679）不是数值 >=，而是「零/非零
            # 匹配」：n := GetQuestMark(param)（只返回 0/1），n=0 时 tag<>0
            # 判假、n<>0 时 tag=0 判假。即 ``(quest==0) == (tag==0)``。
            # 旧实现写成 ``quest >= tag``，导致 ``check [N] 0`` 恒真——玛法
            # 领路人的 @away 因此永远跳回 @next1，5000 金币可无限领。
            # 全局 214 处 check 的 tag 全是 0/1：tag=1 行为不变（quest>=1
            # 等价 quest!=0），只修 tag=0 的恒真 bug。
            q = self._quest_get(p, self._quest_index(c.param))
            return (q == 0) == (c.int_at("tag") == 0)
        if n == "equal":
            return self._quest_get(p, self._quest_index(c.param)) == c.int_at("tag")
        if n == "large":
            return self._quest_get(p, self._quest_index(c.param)) > c.int_at("tag")
        if n == "small":
            return self._quest_get(p, self._quest_index(c.param)) < c.int_at("tag")
        if n == "random":
            k = c.int_at("param")
            return k <= 0 or int(random.random() * k) == 0
        # ---- 测验房三条件（原版 ObjNpc.pas:7153-7175）--------------------
        if n == "checkmonmap":
            # 地图上怪数 >= n 才通过；地图不存在原版 FindMap=nil 直接通过。
            map_id = c.param.strip()
            if self.world.get_or_load_map(map_id) is None:
                return True
            return self.world.count_map_monsters(map_id) >= c.int_at("tag", 1)
        if n == "checkhum":
            # 地图上玩家数 >= n 才通过（GetMapHuman(sParam1) < n → False）。
            return self.world.count_map_humans(c.param.strip()) >= c.int_at("tag", 1)
        if n == "checkbaggage":
            return len(p.bag) < BAG_SIZE
        return True

    def _run_actions(self, p, npc, acts: list[Cmd], r: Result) -> str | None:
        for c in acts:
            ctrl = self._run_action(p, npc, c, r)
            if ctrl is not None:
                return ctrl
        return None

    def _run_action(self, p, npc, c: Cmd, r: Result) -> str | None:
        n = c.name.lower()
        if n in ("take", "takew"):
            self.world.take_item(p, c.param, c.int_at("tag", 1))
        elif n == "give":
            self.world.give_item(p, c.param, c.int_at("tag", 1))
        elif n == "set":
            p.quest_params[self._quest_index(c.param)] = c.int_at("tag")
        elif n == "reset":
            p.quest_params[self._quest_index(c.param)] = 0
        elif n == "inc":
            k = self._quest_index(c.param)
            p.quest_params[k] = p.quest_params.get(k, 0) + 1
        elif n == "dec":
            k = self._quest_index(c.param)
            p.quest_params[k] = p.quest_params.get(k, 0) - 1
        elif n in ("mapmove", "map"):
            self._do_map_move(p, c, r)
            return "BREAK"
        elif n == "goto":
            return c.param if c.param.startswith("@") else "@" + c.param
        elif n in ("#call", "call"):
            # #CALL [商人\肉店.txt] @肉店 → 带内信号交给 execute_script 循环切脚本
            rel = c.param.strip().strip("[]").strip('"')
            lbl = c.tag.strip().lstrip("@")
            if rel and lbl:
                return f"@SUB\t{rel}\t{lbl}"
            return None
        elif n == "break":
            return "BREAK"
        # ---- 测验房动作（原版 ObjNpc.pas:7799-7865）----------------------
        elif n == "param1":
            self._params(npc)["map"] = c.param.strip()
        elif n == "param2":
            self._params(npc)["x"] = c.int_at("param")
        elif n == "param3":
            self._params(npc)["y"] = c.int_at("param")
        elif n == "mongen":
            # 原版：x = Random(range*2+1) + (Param2 - range)，y 同理（:7860-7861），
            # 逐只 RegenMonsterByName(Param1 地图, x, y, 怪名)（:7862）。模板不存在
            # 时原版静默失败（如沉默版 9Ehw-0114 的「暗之沃玛护卫」是死引用）。
            pr = self._params(npc)
            name, count, rng = c.param.strip(), c.int_at("tag", 1), c.int_at("extra")
            if pr.get("map") and name:
                for _ in range(max(count, 0)):
                    ox = random.randint(0, rng * 2) + pr["x"] - rng
                    oy = random.randint(0, rng * 2) + pr["y"] - rng
                    self.world.regen_monster_by_name(pr["map"], ox, oy, name)
        elif n == "monclear":
            self.world.clear_map_monsters(c.param.strip())
        elif n == "timerecall":
            # 原版 :7800-7806：记录玩家当前 map/x/y，nParam1 分钟后 SpaceMove 回去。
            self.world.start_time_recall(p, c.int_at("param", 1))
        elif n == "close":
            r.closed = True
            return "BREAK"
        elif n == "buy":
            r.shop_mode = "buy"
            return "BREAK"
        elif n == "sell":
            r.shop_mode = "sell"
            return "BREAK"
        return None

    def _do_map_move(self, p, c: Cmd, r: Result) -> None:
        map_id = c.param.strip()
        target = self.world.get_or_load_map(map_id)
        if target is None:
            target = p.map
        use_random = c.tag.strip() == "" or c.extra.strip() == ""
        if use_random:
            x, y = self.world.random_walk_pos(target)
        else:
            x, y = c.int_at("tag"), c.int_at("extra")
        # 原版脚本 MAPMOVE（ObjNpc.pas:7836-7837）= RM_SPACEMOVE_FIRE + SpaceMove：
        # 传送员/脚本传送带两段特效（消失 250 + 现身 260），与过门不同。
        moved = self.world.space_move(p, map_id, x, y)
        if moved is not None:
            r.moved = True
            r.moved_map = moved
            r.moved_x, r.moved_y = x, y

    def goods(self, npc) -> list[dict]:
        """返回 ``[{"index","name","price","stock"}]``。

        M2 ``Market_Def`` 的真实列义（脚本头 ``%100`` + ``+N`` 分类行）是：
        ``[goods]`` 每行 = ``物品名 库存数量 刷新时间``，**第二列不是单价**。

        买价链路（本地 Delphi 源码逐字核对）：
        ``ObjNpc.pas:798-823 CheckItemPrice`` 首次补货写入
        ``Round(StdItem.Price * 1.1)`` → ``:999-1024 GetItemPrice`` 取该记录
        → ``:1385-1412 GetUserPrice`` = ``Round(nPrice / 100 * m_nPriceRate)``。
        所以实际售价是 **原价的 1.1 倍再乘脚本倍率**，不是原价。
        1.76 玩家实测价（木剑 55 / 青铜剑 990 / 凝霜 8800 / 修罗 11000）
        正好等于 StdItems.csv 的 50 / 900 / 8000 / 10000 × 1.1，
        这就是用户反馈 27「武器没这么便宜」的根因。
        """
        out: list[dict] = []
        if npc is None or npc.script is None:
            return out
        script = npc.script
        rows = script.goods_rows()
        if not rows:
            # 1.76 流通包（Gee）主城商店：主脚本只有 #CALL，[goods] 在子脚本里
            for sub in self._called_subs(script):
                rows = sub.goods_rows()
                if rows:
                    script = sub
                    break
        rate = getattr(script, "price_rate", 100) or 100
        for parts in rows:
            t = self.world.item_template_by_name(parts[0])
            if t is None:
                continue
            try:
                stock = int(parts[1])
            except ValueError:
                stock = 0
            base = delphi_round(t.price * 1.1)          # CheckItemPrice
            price = max(1, delphi_round(base / 100 * rate))   # GetUserPrice
            out.append({"index": t.index, "name": t.name, "price": price, "stock": stock})
        return out
