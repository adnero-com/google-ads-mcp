# Copyright 2026 Adnero.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The edge of core/plan.py: the server secret, the clock and the record of applied plans.

Secret: GOOGLE_ADS_MCP_PLAN_SECRET (at least 32 characters). Unset: derived from the gateway key
GOOGLE_ADS_MCP_SHARED_SECRET (HMAC with a fixed label, so the key itself is never the plan key). Neither:
writes are refused (a per-process random key would break across workers and restarts). The secret is never
logged or returned.

Single use: a plan applies once. The record of applied plans lives in this process's memory until the plan
expires: with several workers (or after a restart) a plan could be applied once per worker, so the gateway
build runs one worker; a shared store is the fix if that changes.
"""

import functools
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

from fastmcp.exceptions import ToolError

from ads_mcp.core import plan as core

PLAN_SECRET_ENV = "GOOGLE_ADS_MCP_PLAN_SECRET"
SHARED_SECRET_ENV = "GOOGLE_ADS_MCP_SHARED_SECRET"
_MIN = 32

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _secret() -> bytes | None:
    own = os.environ.get(PLAN_SECRET_ENV, "")
    if len(own) >= _MIN:
        return own.encode()
    if own:
        logger.warning(
            "%s is shorter than %d characters; ignored", PLAN_SECRET_ENV, _MIN
        )
    shared = os.environ.get(SHARED_SECRET_ENV, "")
    if len(shared) >= _MIN:
        return hmac.new(
            shared.encode(), b"adnero-plan-key-v1", hashlib.sha256
        ).digest()
    logger.warning(
        "No %s and no gateway key: writes are refused", PLAN_SECRET_ENV
    )
    return None


_used: dict[str, int] = {}
_lock = threading.Lock()


def reset() -> None:
    """Forget the cached secret and the applied plans (tests, key rotation)."""
    getattr(_secret, "cache_clear", lambda: None)()
    with _lock:
        _used.clear()


def now() -> int:
    return int(time.time())


def _refusal(code: str) -> str:
    return f"Error: {core.MESSAGES[code]}"


def issue(digest: str) -> str:
    secret = _secret()
    if secret is None:
        raise ToolError(_refusal("UNCONFIGURED"))
    return core.issue(secret, digest, now(), secrets.token_hex(8))


def problem(plan_id: str | None, digest: str) -> str | None:
    """None when the plan applies to this digest; else the error text. Does not consume it."""
    secret = _secret()
    if secret is None:
        return _refusal("UNCONFIGURED")
    result = core.verify(secret, plan_id, digest, now())
    if not result.ok:
        return _refusal(result.error)
    with _lock:
        if plan_id.strip().lower() in _used:
            return _refusal("USED")
    return None


def consume(plan_id: str, digest: str) -> str | None:
    """Verifies and marks the plan applied, atomically: None when this call may apply it."""
    found = problem(plan_id, digest)
    if found:
        return found
    key = plan_id.strip().lower()
    t = now()
    with _lock:
        if key in _used:
            return _refusal("USED")
        for old in [k for k, exp in _used.items() if exp < t]:
            del _used[old]
        _used[key] = int(key.split(".")[1])
    return None
