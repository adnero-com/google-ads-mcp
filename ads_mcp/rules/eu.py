"""The EU political advertising self-declaration (pure), for every campaign type we create.

v26 makes `contains_eu_political_advertising` optional only when no positive location needs it. Google says
which locations do: `geo_target_constant.require_eu_political_advertising_declaration` (countries, regions
and cities alike), which the edge reads. The rule:
- the caller stated it: send that;
- no location (the campaign targets everywhere, the EU included): the caller must state it;
- the read failed: leave the field unset, so Google enforces it (never guess "does not contain");
- a targeted location requires it: the caller must state it;
- else: DOES_NOT_CONTAIN.
"""

from collections.abc import Sequence
from typing import Literal

from ads_mcp.core.result import Err, Ok, Result
from ads_mcp.rules.app import Problem

EuStatus = Literal["CONTAINS", "DOES_NOT_CONTAIN", "UNSET"]

_FIX = (
    "Ask the person whether the ads are EU political advertising, then pass "
    "contains_eu_political_advertising=false (they are not) or true."
)


def decide(
    declared: bool | None,
    geo_ids: Sequence[str],
    requiring: Sequence[str] | None,
) -> Result[EuStatus, Problem]:
    """`requiring`: the targeted geo ids whose constant requires the declaration; None when the read failed."""
    if declared is not None:
        return Ok("CONTAINS" if declared else "DOES_NOT_CONTAIN")
    if not geo_ids:
        return Err(
            Problem(
                code="EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED",
                field="contains_eu_political_advertising",
                message="No location is targeted, so the campaign reaches the EU: Google needs the EU political "
                "advertising self-declaration.",
                fix=_FIX,
            )
        )
    if requiring is None:
        return Ok("UNSET")
    if requiring:
        return Err(
            Problem(
                code="EU_POLITICAL_ADVERTISING_DECLARATION_REQUIRED",
                field="contains_eu_political_advertising",
                message=f"Location(s) {', '.join(requiring)} require the EU political advertising "
                "self-declaration.",
                fix=_FIX,
            )
        )
    return Ok("DOES_NOT_CONTAIN")
