"""免密登录 token：无状态 HMAC 签名，不落库（不改 accounts 表，避免与 Java 共用库冲突）。

token 格式：``base64url(payload).base64url(sig)``
  payload = JSON {"uid": account_id, "u": username, "exp": 过期 unix 秒}
  sig     = HMAC-SHA256(SECRET, payload_b64) 的前 16 字节

用法：
  - WS ``login`` 成功后由服务端签发，随 loginResult 下发 ``token``；
  - 下次进登录页，前端发 ``tokenLogin {token}``，服务端校验后沿用登录流程；
  - 挂机网页后台的 REST 也用同一个 token（query 参数或 JSON body）认证。

安全性：签名密钥默认内置（本地怀旧服，token 只用于免密，不涉及支付/隐私）；
部署可用环境变量 ``JOOTM2_TOKEN_SECRET`` 覆盖。token 有效期 30 天，过期需重新输密码。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

SECRET = os.environ.get("JOOTM2_TOKEN_SECRET", "jootm2-local-token-v1").encode("utf-8")
TTL_SECONDS = 30 * 24 * 3600   # 30 天


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(uid: int, username: str, now: int | None = None) -> str:
    """签发 token。"""
    now = now if now is not None else int(time.time())
    payload = json.dumps({"uid": int(uid), "u": username, "exp": now + TTL_SECONDS},
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = _b64e(payload)
    sig = hmac.new(SECRET, body.encode("ascii"), hashlib.sha256).digest()[:16]
    return f"{body}.{_b64e(sig)}"


def verify(token: str, now: int | None = None) -> dict[str, Any] | None:
    """校验 token，成功返回 {"uid","u","exp"}，失败/过期返回 None。"""
    if not token or not isinstance(token, str):
        return None
    try:
        body, sig = token.split(".", 1)
        expect = hmac.new(SECRET, body.encode("ascii"), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(_b64d(sig), expect):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        uid = int(payload["uid"])
        username = str(payload["u"])
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    now = now if now is not None else int(time.time())
    if exp <= now:
        return None
    return {"uid": uid, "u": username, "exp": exp}
