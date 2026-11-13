"""Unforgeable preview -> apply plans (pure).

A plan id is `p1.<expires_at>.<hmac>`: HMAC-SHA256, keyed by a server secret, over the plan
version, the expiry and the digest of what is applied (tool + customer + canonical args). A
model can see the digest's inputs but not the key, so it cannot mint a plan without the
preview; any change to the args changes the digest and the plan no longer verifies. The clock
is an argument.
"""

import hashlib
import hmac
import json
from typing import Any, Literal

from ads_mcp.core.result import Err, Ok, Result

PLAN_TTL_S = 15 * 60
_VERSION = "p1"

PlanError = Literal["MISSING", "MALFORMED", "EXPIRED", "MISMATCH"]

MESSAGES: dict[str, str] = {
    "MISSING": "a plan_id from a validate_only=true call is required to apply.",
    "MALFORMED": "this plan_id was not issued by this server: preview again with validate_only=true.",
    "EXPIRED": "the plan expired (plans last 15 minutes): preview again with validate_only=true.",
    "MISMATCH": "the plan does not match these arguments (something changed since the preview, or the plan "
    "is from another change): preview again with validate_only=true.",
}


def canonical(value: Any) -> bytes:
    """Sorted keys, no spaces, UTF-8: the same arguments always give the same bytes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def digest_of(tool: str, customer_id: str, args: Any) -> str:
    return hashlib.sha256(
        canonical({"tool": tool, "customer_id": customer_id, "args": args})
    ).hexdigest()


def _mac(secret: bytes, expires_at: int, digest: str) -> str:
    return hmac.new(
        secret, f"{_VERSION}|{expires_at}|{digest}".encode(), hashlib.sha256
    ).hexdigest()


def issue(secret: bytes, digest: str, now: int, ttl: int = PLAN_TTL_S) -> str:
    expires_at = int(now) + ttl
    return f"{_VERSION}.{expires_at}.{_mac(secret, expires_at, digest)}"


def verify(
    secret: bytes, plan_id: str | None, digest: str, now: int
) -> Result[int, PlanError]:
    """Ok(expires_at) when this server issued the plan for exactly this digest and it has not expired."""
    if not plan_id or not plan_id.strip():
        return Err("MISSING")
    parts = plan_id.strip().lower().split(".")
    if len(parts) != 3 or parts[0] != _VERSION or not parts[1].isdigit():
        return Err("MALFORMED")
    expires_at = int(parts[1])
    if not hmac.compare_digest(parts[2], _mac(secret, expires_at, digest)):
        return Err("MISMATCH")
    if int(now) > expires_at:
        return Err("EXPIRED")
    return Ok(expires_at)
