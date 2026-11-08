#!/usr/bin/env python3
"""Java / Python 双栈协议形状对比（迁移回归基线）。

对两个服务端发送同一组 WebSocket JSON 消息，比较每种响应类型的
**字段结构**（递归到 player/map 等嵌套对象）与**稳定值**，忽略随机/易变
字段（实体 id、坐标、刷怪数量、背包 makeIndex、公告文本等）。

这一步专治「协议看起来能跑、但前端少一个字段就静默出错」的漂移。

用法：
    # 双栈实时对比（两个服务端须用各自独立的 SQLite 文件）
    python scripts/compare_stacks.py --java-port 8080 --py-port 8081

    # 录制 Java 侧为 golden 基线
    python scripts/compare_stacks.py --java-port 8080 --py-port 8081 \
        --save-golden scripts/golden/protocol_baseline.json

    # 离线回放：只起 Python 服务端，与基线对比（CI 用）
    python scripts/compare_stacks.py --py-port 8081 \
        --golden scripts/golden/protocol_baseline.json

退出码：0 = 一致 / 1 = 存在差异 / 2 = 连接或握手失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_ws import WS  # noqa: E402

STEPS = (
    "ping", "register", "login", "listCharacters",
    "createCharacter", "enterGame", "noticeOk", "move", "say", "logout",
)

# 阶段 4 全量模式：把其余 35 种消息也发给两端，比较响应类型与字段结构。
# 故意使用无效/中性参数（无目标、无物品），这样两端都走「早退或提示」路径，
# 结果稳定，不依赖随机战斗/掉落；专门验证协议层没有漏字段或改名。
FULL_EXTRA = (
    "attack", "dirAttack", "castMagic", "magicKeyChange",
    "pickup", "useItem", "takeOnItem", "takeOffItem", "quickSlot", "drop", "moveItem",
    "npcTalk", "npcSelect", "buyItem", "sellItem",
    "createGuild", "joinGuild", "leaveGuild", "guildInfo", "guildChat",
    "siege", "guildWar", "guildAlly",
    "partyInvite", "partyLeave", "partyChat",
    "storageOpen", "storageStore", "storageTake",
    "tradeRequest", "tradeAccept", "tradeCancel", "tradeAddItem",
    "tradeAddGold", "tradeConfirm",
)

FULL_STEPS = (STEPS[:7] + ("deleteCharacter",) + FULL_EXTRA
              + ("move", "say", "logout"))

EXPECT = {
    "ping": ("pong",),
    "register": ("registerResult",),
    "login": ("loginResult",),
    "listCharacters": ("characterList",),
    "createCharacter": ("createCharacterResult",),
    "enterGame": ("notice", "enterGameResult"),
    "noticeOk": ("enterGameResult", "enterGame", "stats"),
    "move": ("move",),
    "say": ("chat",),
    "logout": (),
    # 全量模式下这些消息允许「无响应」（参数无效时两端都静默早退）。
    **{name: () for name in ("deleteCharacter",) + FULL_EXTRA},
}

# 这些键的内容依赖随机刷怪 / 自增长 id / 账号名，只比较「存在性」不比较结构。
OPAQUE_KEYS = {
    "nearby", "groundItems", "items", "lines", "characters", "quickSlots",
    "skills", "entity", "players", "id", "attackerId", "targetId", "makeIndex",
}

# 这些字段在两端必须逐值相等（其余数值/布尔只比较类型）。
STABLE_KEYS = {"type", "ok", "error", "name", "level", "job", "sex", "hair",
               "title", "width", "height", "miniMapIndex", "dir", "run"}


def shape(obj, key: str | None = None, depth: int = 0):
    """递归描述 JSON 结构；list 只看首元素形状，opaque 键折叠为标记。"""
    if key in OPAQUE_KEYS:
        return "<any>"
    if depth > 6:
        return "<deep>"
    if isinstance(obj, dict):
        return {k: shape(v, k, depth + 1) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [shape(obj[0], None, depth + 1)] if obj else []
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, (int, float)):
        return "number"
    return "str"


def stable_values(msg: dict) -> dict:
    return {k: msg[k] for k in sorted(STABLE_KEYS) if k in msg}


def summarize(msgs: list[dict], user: str, char: str) -> dict:
    """按消息类型汇总：字段结构 + 稳定值。"""
    text = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
    text = text.replace(user, "<USER>").replace(char, "<CHAR>")
    scrubbed = json.loads(text)
    out: dict[str, dict] = {}
    for msg in scrubbed:
        if not isinstance(msg, dict):
            continue
        t = str(msg.get("type", "?"))
        if t in out:
            continue
        out[t] = {"shape": shape(msg), "stable": stable_values(msg)}
    return out


def full_messages(char: str) -> dict:
    """全量模式下 35 种新消息，统一用中性参数保证两端行为稳定。"""
    return {
        "deleteCharacter": {"type": "deleteCharacter", "name": "NoSuchChar"},
        "attack": {"type": "attack", "targetId": -1},
        "dirAttack": {"type": "dirAttack", "dir": 2},
        "castMagic": {"type": "castMagic", "magicId": 3, "targetX": -1,
                      "targetY": -1, "targetId": -1},
        "magicKeyChange": {"type": "magicKeyChange", "magicId": 3, "key": 1},
        "pickup": {"type": "pickup", "itemId": -1},
        "useItem": {"type": "useItem", "makeIndex": -1},
        "takeOnItem": {"type": "takeOnItem", "makeIndex": -1, "slot": 1},
        "takeOffItem": {"type": "takeOffItem", "slot": 1, "makeIndex": -1},
        "quickSlot": {"type": "quickSlot", "slot": 0, "makeIndex": 0},
        "drop": {"type": "drop", "makeIndex": -1},
        "moveItem": {"type": "moveItem", "makeIndex": -1, "targetIndex": 0},
        "npcTalk": {"type": "npcTalk", "npcId": -1},
        "npcSelect": {"type": "npcSelect", "npcId": -1, "value": "@main"},
        "buyItem": {"type": "buyItem", "npcId": -1, "index": -1, "count": 1},
        "sellItem": {"type": "sellItem", "npcId": -1, "makeIndex": -1},
        "createGuild": {"type": "createGuild", "name": "CmpGuild"},
        "joinGuild": {"type": "joinGuild", "name": "CmpGuild"},
        "leaveGuild": {"type": "leaveGuild"},
        "guildInfo": {"type": "guildInfo"},
        "guildChat": {"type": "guildChat", "text": "guild"},
        "siege": {"type": "siege"},
        "guildWar": {"type": "guildWar", "name": "None"},
        "guildAlly": {"type": "guildAlly", "name": "None"},
        "partyInvite": {"type": "partyInvite", "name": "NoSuchPlayer"},
        "partyLeave": {"type": "partyLeave"},
        "partyChat": {"type": "partyChat", "text": "party"},
        "storageOpen": {"type": "storageOpen"},
        "storageStore": {"type": "storageStore", "makeIndex": -1},
        "storageTake": {"type": "storageTake", "makeIndex": -1},
        "tradeRequest": {"type": "tradeRequest", "name": "NoSuchPlayer"},
        "tradeAccept": {"type": "tradeAccept"},
        "tradeCancel": {"type": "tradeCancel"},
        "tradeAddItem": {"type": "tradeAddItem", "makeIndex": -1},
        "tradeAddGold": {"type": "tradeAddGold", "amount": 1},
        "tradeConfirm": {"type": "tradeConfirm"},
    }


def build_steps(tag: str, suffix: str, full: bool = False) -> tuple[str, str, dict]:
    user = f"cmp{tag}{suffix}"
    char = f"C{tag[-4:]}{suffix.upper()}"
    steps = {
        "ping": {"type": "ping"},
        "register": {"type": "register", "username": user, "password": "cmp12345"},
        "login": {"type": "login", "username": user, "password": "cmp12345"},
        "listCharacters": {"type": "listCharacters"},
        "createCharacter": {"type": "createCharacter", "name": char,
                            "sex": 0, "hair": 1, "job": 0},
        "enterGame": {"type": "enterGame", "name": char},
        "noticeOk": {"type": "noticeOk", "name": char},
        "move": {"type": "move", "dir": 2},
        "say": {"type": "say", "text": "hello from compare"},
        "logout": {"type": "logout"},
    }
    if full:
        steps.update(full_messages(char))
    return user, char, steps


def steps_for(full: bool) -> tuple[str, ...]:
    return FULL_STEPS if full else STEPS


def run_stack(host: str, port: int, tag: str, suffix: str,
              full: bool = False) -> dict:
    user, char, steps = build_steps(tag, suffix, full)
    try:
        ws = WS(host, port, "/ws", timeout=0.45)
    except (OSError, RuntimeError) as e:
        raise RuntimeError(f"{host}:{port} 连接失败: {e}") from e
    report = {"handshake": ws.status, "steps": {}}
    try:
        for name in steps_for(full):
            try:
                ws.send(steps[name])
                got = ws.drain(max_frames=24)
            except (EOFError, OSError, BrokenPipeError):
                # logout 后服务端会主动关闭连接，属正常情况。
                report["steps"][name] = {"types": [], "summary": {}}
                break
            types = [m.get("type", "?") for m in got if isinstance(m, dict)]
            report["steps"][name] = {
                "types": types,
                "summary": summarize(got, user, char),
            }
    finally:
        ws.close()
    return report


def diff_shape(a, b, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"  + {path}.{k}   仅 Python 有")
            elif k not in b:
                out.append(f"  - {path}.{k}   仅 Java/基线有")
            else:
                diff_shape(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if a and b:
            diff_shape(a[0], b[0], path + "[]", out)
    elif a != b:
        out.append(f"  ~ {path}   类型 {a} -> {b}")


def compare(baseline: dict, target: dict, baseline_name: str,
            steps: tuple[str, ...] = STEPS, strict: bool = True) -> tuple[int, list[str]]:
    problems: list[str] = []
    print(f"\n=== 逐步响应类型（{baseline_name} vs Python，共 {len(steps)} 步）===")
    for name in steps:
        b = baseline["steps"].get(name, {})
        t = target["steps"].get(name, {})
        bt, tt = set(b.get("types", [])), set(t.get("types", []))
        # 期望列表是「任一命中即可」（enterGame 正常只回 notice）。
        ok = not EXPECT[name] or any(e in tt for e in EXPECT[name])
        missing = sorted(bt - tt)
        extra = sorted(tt - bt)
        flag = "OK" if ok else "FAIL"
        print(f"  {name:18s} {flag:4s}  {baseline_name}={','.join(sorted(bt)) or '-'}"
              f"  python={','.join(sorted(tt)) or '-'}")
        if not ok:
            problems.append(f"  ! {name}: Python 缺少期望响应 {EXPECT[name]}")
        if strict and missing and name not in ("noticeOk", "move"):
            problems.append(f"  ! {name}: Python 缺少 {baseline_name} 的类型 {missing}")
        if strict and extra and name not in ("noticeOk", "move", "logout"):
            problems.append(f"  ! {name}: Python 多出类型 {extra}")
        if not strict and (missing or extra):
            # 全量模式允许随机/状态差异，只报告不判失败。
            print(f"       差异(仅记录): 缺少={missing} 多出={extra}")

    print(f"\n=== 字段结构差异（{baseline_name} vs Python）===")
    shape_diffs: list[str] = []
    all_types = sorted(set().union(*[
        set(s.get("summary", {})) for r in (baseline, target) for s in r["steps"].values()
    ]))
    for t in all_types:
        b = next((s["summary"][t] for s in baseline["steps"].values()
                  if t in s.get("summary", {})), None)
        p = next((s["summary"][t] for s in target["steps"].values()
                  if t in s.get("summary", {})), None)
        if b is None or p is None:
            continue
        diff_shape(b["shape"], p["shape"], t, shape_diffs)
        if b["stable"] != p["stable"]:
            shape_diffs.append(f"  ~ {t}.stable  {b['stable']} -> {p['stable']}")
    if shape_diffs:
        for line in shape_diffs:
            print(line)
        problems.extend(shape_diffs)
    else:
        print("  (无差异)")
    print(f"\n结论: {'协议形状一致' if not problems else f'发现 {len(problems)} 处差异'}")
    return (0 if not problems else 1), problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Java/Python 双栈协议形状对比")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--java-port", type=int, help="Java 服务端端口（作为基线）")
    ap.add_argument("--py-port", type=int, default=8081, help="Python 服务端端口")
    ap.add_argument("--golden", help="离线 golden 基线 JSON（此时不需要 Java 服务端）")
    ap.add_argument("--save-golden", help="把基线写入该 JSON 文件")
    ap.add_argument("--full", action="store_true",
                    help="全量模式：对比 Java switch 的全部 46 种消息")
    a = ap.parse_args()

    tag = "%d%d" % (os.getpid() % 10000, int(time.time()) % 10000)
    golden_path = Path(a.golden) if a.golden else None
    if golden_path is not None:
        if not golden_path.is_file():
            print(f"找不到 golden 基线: {golden_path}")
            return 2
        baseline = json.loads(golden_path.read_text(encoding="utf-8"))
        full = bool(baseline.get("_full", False)) or a.full
        baseline_name = "golden"
        print(f"已加载 golden 基线: {golden_path} (full={full})")
    else:
        full = a.full
        if not a.java_port:
            print("需要 --java-port 或 --golden")
            return 2
        try:
            baseline = run_stack(a.host, a.java_port, tag, "j", full=full)
        except RuntimeError as e:
            print(e)
            return 2
        baseline_name = "java"
        print(f"Java 基线采集完成: {a.host}:{a.java_port} ({baseline['handshake'].strip()}, "
              f"{len(steps_for(full))} 步)")

    try:
        target = run_stack(a.host, a.py_port, tag, "p", full=full)
    except RuntimeError as e:
        print(e)
        return 2
    print(f"Python 采集完成: {a.host}:{a.py_port} ({target['handshake'].strip()})")

    if a.save_golden:
        out = Path(a.save_golden)
        out.parent.mkdir(parents=True, exist_ok=True)
        baseline["_full"] = full
        out.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已保存: {out} (full={full})")

    code, _ = compare(baseline, target, baseline_name,
                      steps=steps_for(full), strict=not full)
    return code


if __name__ == "__main__":
    sys.exit(main())
