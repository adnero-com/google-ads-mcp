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

"""Edge reads the write tools share: GAQL to the end, and which geos need the EU declaration."""

from collections.abc import Sequence
import logging
from typing import Any

logger = logging.getLogger(__name__)
MAX_PAGES = 20


def rows_to_end(
    cid: str, query: str, login
) -> tuple[list[dict[str, Any]], bool]:
    """Every row of a query (page by page, one operation each); complete=False if it stopped at MAX_PAGES."""
    from ads_mcp.tools.search import search_page

    rows: list[dict[str, Any]] = []
    token = None
    for _ in range(MAX_PAGES):
        page = search_page(cid, query, 10_000, token, login)
        rows += page.rows
        token = page.next_page_token
        if not token:
            return rows, page.note is None
    return rows, False


def eu_requiring(
    cid: str, geo_ids: Sequence[str], login
) -> tuple[str, ...] | None:
    """The targeted geo ids whose constant requires the EU declaration (v26). None when it cannot be read:
    the caller then leaves the field unset and Google enforces it."""
    ids = [g for g in dict.fromkeys(str(g) for g in geo_ids) if g.isdigit()]
    if not ids:
        return ()
    try:
        rows, complete = rows_to_end(
            cid,
            "SELECT geo_target_constant.id, geo_target_constant.require_eu_political_advertising_declaration "
            f"FROM geo_target_constant WHERE geo_target_constant.id IN ({','.join(ids)})",
            login,
        )
    except (
        Exception
    ) as exc:  # the read is advisory; Google decides when it fails
        logger.warning("EU declaration read failed: %s", type(exc).__name__)
        return None
    if not complete:
        return None
    return tuple(
        str(r.get("geo_target_constant.id"))
        for r in rows
        if r.get(
            "geo_target_constant.require_eu_political_advertising_declaration"
        )
    )
