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

Single use: a plan applies once; a plan Google refused on apply is released, so the same plan may be retried
within its 15 minutes (an apply whose outcome is unknown, a timeout, stays used). The record of applied plans
lives in this process's memory, so:
- a plan issued before this process started is refused (a restart cannot replay one);
- the server refuses to start with more than one worker or replica (WEB_CONCURRENCY, UVICORN_WORKERS,
  GOOGLE_ADS_MCP_REPLICAS > 1): there is no shared plan store yet, and each worker would accept a plan once.
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
_WORKER_ENVS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GOOGLE_ADS_MCP_REPLICAS")


def _clock() -> int:
    return int(time.time())


_started = _clock()


def check_single_worker() -> None:
    """Startup: one worker and one replica only (the applied-plan record is in-process memory)."""
    many = {
        name: os.environ[name]
        for name in _WORKER_ENVS
        if os.environ.get(name, "").strip() not in ("", "0", "1")
    }
    if many:
        raise RuntimeError(
            f"{', '.join(f'{k}={v[:10]}' for k, v in many.items())}: this server keeps applied plans in memory, "
            "so it runs as ONE worker and ONE replica; a shared plan store is needed for more (none exists yet)."
        )


def reset() -> None:
    """Forget the cached secret and the applied plans, and start the clock again (tests, key rotation)."""
    global _started
    getattr(_secret, "cache_clear", lambda: None)()
    with _lock:
        _used.clear()
    _started = now()


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
    if result.value - core.PLAN_TTL_S < _started:
        return _refusal("RESTARTED")
    with _lock:
        if plan_id.strip().lower() in _used:
            return _refusal("USED")
    return None


def release(plan_id: str) -> None:
    """Google refused the apply: the plan may be applied again (until it expires)."""
    with _lock:
        _used.pop(plan_id.strip().lower(), None)


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
