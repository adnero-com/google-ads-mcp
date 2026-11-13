"""Unforgeable preview -> apply plans (pure).

A plan id is `p1.<expires_at>.<nonce>.<hmac>`: HMAC-SHA256, keyed by a server secret, over the plan
version, the expiry, a random nonce (each preview is a distinct plan, so single use never blocks a fresh
preview) and the digest of what is applied (tool + customer + canonical args). A
model can see the digest's inputs but not the key, so it cannot mint a plan without the
preview; any change to the args changes the digest and the plan no longer verifies. The clock
is an argument.
"""

import hashlib
import hmac
import json
import re
from typing import Any, Literal

from ads_mcp.core.result import Err, Ok, Result

PLAN_TTL_S = 15 * 60
_VERSION = "p1"

PlanError = Literal[
    "MISSING", "MALFORMED", "EXPIRED", "MISMATCH", "USED", "UNCONFIGURED"
]

MESSAGES: dict[str, str] = {
    "MISSING": "a plan_id from a validate_only=true call is required to apply.",
    "MALFORMED": "this plan_id was not issued by this server: preview again with validate_only=true.",
    "EXPIRED": "the plan expired (plans last 15 minutes): preview again with validate_only=true.",
    "USED": "this plan was already applied (a plan applies once): preview again with validate_only=true for "
    "another change.",
    "UNCONFIGURED": "writes are off on this server: it has no plan key (set GOOGLE_ADS_MCP_PLAN_SECRET, 32+ "
    "characters, the same on every worker).",
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


def _mac(secret: bytes, expires_at: int, nonce: str, digest: str) -> str:
    return hmac.new(
        secret,
        f"{_VERSION}|{expires_at}|{nonce}|{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()


def issue(
    secret: bytes, digest: str, now: int, nonce: str, ttl: int = PLAN_TTL_S
) -> str:
    """`nonce`: 16 lowercase hex characters, random per preview (the edge draws it)."""
    expires_at = int(now) + ttl
    return f"{_VERSION}.{expires_at}.{nonce}.{_mac(secret, expires_at, nonce, digest)}"


_SHAPE = re.compile(
    r"^p1\.([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})$", re.ASCII
)


def verify(
    secret: bytes, plan_id: str | None, digest: str, now: int
) -> Result[int, PlanError]:
    """Ok(expires_at) when this server issued the plan for exactly this digest and it has not expired.
    Any other shape (non-ASCII digits, a short MAC, not a string) is MALFORMED; it never raises.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        return Err("MISSING")
    shape = _SHAPE.match(plan_id.strip().lower())
    if shape is None:
        return Err("MALFORMED")
    expires_at, nonce, mac = int(shape.group(1)), shape.group(2), shape.group(3)
    if not hmac.compare_digest(mac, _mac(secret, expires_at, nonce, digest)):
        return Err("MISMATCH")
    if int(now) > expires_at:
        return Err("EXPIRED")
    return Ok(expires_at)
