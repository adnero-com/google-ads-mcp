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

"""The edge of core/plan.py: the server secret and the clock.

Secret: GOOGLE_ADS_MCP_PLAN_SECRET (at least 32 characters). Unset: derived from the gateway key
GOOGLE_ADS_MCP_SHARED_SECRET (HMAC with a fixed label, so the key itself is never the plan key).
Neither: a random per-process secret (plans die with the process), with a warning. The secret is
never logged or returned.
"""

import functools
import hashlib
import hmac
import logging
import os
import secrets
import time

from ads_mcp.core import plan as core

PLAN_SECRET_ENV = "GOOGLE_ADS_MCP_PLAN_SECRET"
SHARED_SECRET_ENV = "GOOGLE_ADS_MCP_SHARED_SECRET"
_MIN = 32

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _secret() -> bytes:
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
        "No %s: plans are signed with a per-process key and do not survive a restart",
        PLAN_SECRET_ENV,
    )
    return secrets.token_bytes(32)


def reset() -> None:
    """Forget the cached secret (tests, key rotation)."""
    _secret.cache_clear()


def now() -> int:
    return int(time.time())


def issue(digest: str) -> str:
    return core.issue(_secret(), digest, now())


def problem(plan_id: str | None, digest: str) -> str | None:
    """None when the plan applies to this digest; else the error text."""
    result = core.verify(_secret(), plan_id, digest, now())
    return None if result.ok else f"Error: {core.MESSAGES[result.error]}"
