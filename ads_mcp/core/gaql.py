"""GAQL query building and bounded paging (pure).

A search answer is bounded by rows (max_rows) and bytes (BYTE_CAP). Google pages hold up to
10,000 rows, more than one answer may carry, so our page token is a cursor over Google's:
(query fingerprint, Google's page token, offset inside that page). The fingerprint ties a
token to its customer and query; a token from another query is refused.
"""

import base64
import binascii
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from ads_mcp.core.result import Err, Ok, Result

DEFAULT_MAX_ROWS = 500
MAX_ROWS_LIMIT = 10_000
BYTE_CAP = 200_000

StopReason = Literal["END", "MAX_ROWS", "BYTE_CAP"]


@dataclass(frozen=True)
class Cursor:
    fingerprint: str
    google_token: str
    offset: int


@dataclass(frozen=True)
class Page:
    rows: tuple[Any, ...]
    consumed: int
    stopped_by: StopReason


def build_query(
    resource: str,
    fields: Sequence[str],
    conditions: Sequence[str] = (),
    orderings: Sequence[str] = (),
    limit: int | None = None,
) -> Result[str, str]:
    """The upstream parts form: SELECT fields FROM resource [WHERE a AND b] [ORDER BY] [LIMIT]."""
    resource = (resource or "").strip()
    chosen = [f.strip() for f in fields if f and f.strip()]
    if not resource or not chosen:
        return Err("'resource' and at least one of 'fields' are required.")
    if limit is not None and limit < 1:
        return Err("'limit' must be 1 or more.")
    parts = [f"SELECT {','.join(chosen)} FROM {resource}"]
    parts += [f" WHERE {' AND '.join(conditions)}"] if conditions else []
    parts += [f" ORDER BY {','.join(orderings)}"] if orderings else []
    parts += [f" LIMIT {limit}"] if limit else []
    parts += [" PARAMETERS omit_unselected_resource_names=true"]
    return Ok("".join(parts))


def choose_query(
    query: str | None,
    resource: str | None,
    fields: Sequence[str] | None,
    conditions: Sequence[str] = (),
    orderings: Sequence[str] = (),
    limit: int | None = None,
) -> Result[str, str]:
    """Exactly one form: a GAQL `query` string, or resource + fields (+ conditions, orderings, limit)."""
    text = (query or "").strip()
    has_parts = bool(resource) or bool(fields)
    if text and has_parts:
        return Err(
            "Pass either 'query' (a GAQL string) or 'resource' + 'fields', not both."
        )
    if text:
        if conditions or orderings or limit:
            return Err(
                "'conditions', 'orderings' and 'limit' go with 'resource' + 'fields'; put them in the GAQL "
                "'query' instead."
            )
        if not text.upper().startswith("SELECT "):
            return Err("'query' must be a GAQL SELECT statement.")
        return Ok(text)
    if not has_parts:
        return Err("Pass 'query' (a GAQL string) or 'resource' + 'fields'.")
    return build_query(
        resource or "", fields or [], conditions, orderings, limit
    )


def check_max_rows(max_rows: int | None) -> Result[int, str]:
    value = DEFAULT_MAX_ROWS if max_rows is None else max_rows
    if isinstance(value, bool) or not 1 <= value <= MAX_ROWS_LIMIT:
        return Err(f"'max_rows' must be 1 to {MAX_ROWS_LIMIT}.")
    return Ok(value)


def fingerprint(customer_id: str, query: str) -> str:
    return hashlib.sha256(f"{customer_id}\n{query}".encode()).hexdigest()[:16]


def encode_cursor(cursor: Cursor) -> str:
    raw = json.dumps(
        [cursor.fingerprint, cursor.google_token, cursor.offset],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str, expected: str) -> Result[Cursor, str]:
    bad = Err(
        "'page_token' is not one this search returned; run the search again without it."
    )
    try:
        padded = token + "=" * (-len(token) % 4)
        fp, google_token, offset = json.loads(
            base64.urlsafe_b64decode(padded.encode())
        )
    except (ValueError, TypeError, binascii.Error):
        return bad
    if (
        not isinstance(fp, str)
        or not isinstance(google_token, str)
        or not isinstance(offset, int)
        or offset < 0
    ):
        return bad
    if fp != expected:
        return Err(
            "'page_token' belongs to another query or account; pass the same query and customer_id it came "
            "from, or start again without it."
        )
    return Ok(Cursor(fp, google_token, offset))


def take_rows(
    rows: Iterable[Any],
    offset: int,
    max_rows: int,
    size_of: Callable[[Any], int],
    byte_cap: int = BYTE_CAP,
) -> Page:
    """Skips `offset` rows, then takes rows until max_rows or byte_cap (always at least one row).

    Iterates lazily so a long Google page is not formatted past what fits.
    """
    # Local accumulation; nothing escapes but the frozen Page (CLAUDE.md 1.4: stops early on a lazy iterator).
    taken: list[Any] = []
    used = 0
    skipped = 0
    for row in rows:
        if skipped < offset:
            skipped += 1
            continue
        if len(taken) >= max_rows:
            return Page(tuple(taken), len(taken), "MAX_ROWS")
        size = size_of(row)
        if taken and used + size > byte_cap:
            return Page(tuple(taken), len(taken), "BYTE_CAP")
        taken.append(row)
        used += size
    return Page(tuple(taken), len(taken), "END")


def next_cursor(
    current: Cursor, page: Page, google_next_token: str
) -> Cursor | None:
    """Where the next answer starts: the same Google page further on, Google's next page, or nothing."""
    if page.stopped_by != "END":
        return Cursor(
            current.fingerprint,
            current.google_token,
            current.offset + page.consumed,
        )
    if google_next_token:
        return Cursor(current.fingerprint, google_next_token, 0)
    return None
