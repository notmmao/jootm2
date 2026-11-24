"""离线修炼详细日志（JSONL，按「日期 + 角色名」单文件追加）。

为什么要独立日志：离线挂机的运行数据（每分钟经验/击杀/金币、补给占时、
死亡与异常）此前只存在内存里，停机后彻底丢失，事后无法回答「这轮为什么
只打了 55 只怪」「补给占了多久」「卡在哪张图」这类问题。这里把运行轨迹
落成可机读的 JSONL，为后续调优（拾取策略、补给频率、地图适配、爆率）提供
一手依据。

设计约束：

* **纯标准库**：只依赖 ``json`` / ``time`` / ``pathlib``，不引入第三方；
* **绝不拖慢 tick**：事件先入内存缓冲，按「行数 / 时间」批量 flush，
  ``session_start`` / ``session_end`` / ``death`` / ``error`` / ``reject``
  这类关键事件立即落盘；
* **日志失败不得影响挂机**：所有 IO 异常吞掉并降级为静默（宁可丢日志，
  不能让一个磁盘满把离线角色搞停）；
* **一行一事件**：``kind`` 字段区分类型，便于 grep / jq / 脚本聚合。

文件路径：``<afk.logDir>/<YYYY-MM-DD>_<角色名>.jsonl``
（同一天同一角色多次修炼追加进同一文件，靠 ``session_start`` 分段）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 立即落盘的事件（崩溃/宕机时最不能丢的几类）
_FLUSH_NOW_KINDS = frozenset({
    "session_start", "session_end", "death", "error", "stop", "reject",
})
_FLUSH_MAX_LINES = 64          # 攒够这么多行就刷
_FLUSH_MAX_INTERVAL = 5.0      # 或距上次刷盘超过这么多秒
_BAD_CHARS = '<>:"/\\|?*\n\r\t'


def _safe_name(name: str) -> str:
    """角色名直接进文件名：清掉路径分隔符与 Windows 非法字符。"""
    out = "".join("_" if c in _BAD_CHARS else c for c in (name or "unknown"))
    out = out.strip().strip(".") or "unknown"
    return out[:64]


class AfkSessionLog:
    """一个离线修炼会话的日志写入器（按角色复用同一文件）。"""

    def __init__(self, root: Path | str, character_name: str,
                 character_id: int = 0, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self.character_name = character_name or "unknown"
        self.character_id = int(character_id or 0)
        self._buf: list[str] = []
        self._fh = None
        self._last_flush = 0.0
        self._dropped = 0
        self.patherr = ""
        self.path: Path | None = None
        if not self.enabled:
            return
        try:
            day = time.strftime("%Y-%m-%d")
            base = Path(root)
            base.mkdir(parents=True, exist_ok=True)
            self.path = base / f"{day}_{_safe_name(self.character_name)}.jsonl"
            # 追加模式：同一天多轮修炼共用一份，便于横向对比
            self._fh = self.path.open("a", encoding="utf-8", buffering=1)
            self._last_flush = time.time()
        except Exception as e:                       # 磁盘只读/权限等
            self.patherr = f"{type(e).__name__}: {e}"
            self._fh = None
            self.enabled = False

    # ---- 写入 -----------------------------------------------------------

    def event(self, kind: str, now: int | None = None, **fields: Any) -> None:
        """记一条事件。``now`` 是**游戏时间**（ms，模拟器会接管），
        与墙钟 ``t`` 同时记录：分析用游戏时间，排查宕机用墙钟。"""
        if not self.enabled or self._fh is None:
            return
        rec: dict[str, Any] = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
        }
        if now is not None:
            rec["ms"] = int(now)
        rec.update(fields)
        try:
            self._buf.append(json.dumps(rec, ensure_ascii=False,
                                        separators=(",", ":"), default=str))
        except Exception:
            self._dropped += 1
            return
        if kind in _FLUSH_NOW_KINDS or len(self._buf) >= _FLUSH_MAX_LINES \
                or time.time() - self._last_flush >= _FLUSH_MAX_INTERVAL:
            self.flush()

    def sample(self, now: int, **fields: Any) -> None:
        """周期采样（默认每分钟一次），字段随调用方给。"""
        self.event("sample", now, **fields)

    def reject(self, now: int | None, stage: str, reason: str, **fields: Any) -> None:
        """开启/续挂被拒。``stage`` 标明拒绝发生的环节，便于统计哪种原因最多。"""
        self.event("reject", now, stage=stage, reason=reason, **fields)

    def flush(self) -> None:
        if not self.enabled or self._fh is None or not self._buf:
            return
        try:
            self._fh.write("\n".join(self._buf) + "\n")
            self._fh.flush()
            self._buf.clear()
            self._last_flush = time.time()
        except Exception as e:
            # 写失败就关掉，避免每 tick 反复抛异常刷屏
            self._dropped += len(self._buf)
            self._buf.clear()
            self._close_fh()
            self.patherr = f"{type(e).__name__}: {e}"
            self.enabled = False

    def close(self) -> None:
        self.flush()
        self._close_fh()

    def _close_fh(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass

    @property
    def ok(self) -> bool:
        return bool(self.enabled and self._fh is not None)
