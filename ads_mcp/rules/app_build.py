"""App campaign writes, the pure part: dates, ad content edits, update refusals.

The edge (tools/adnero_app_*.py) reads the current state, calls these, and builds the protos.
"""

from collections.abc import Sequence
from datetime import date
import re

from pydantic import BaseModel, ConfigDict

from ads_mcp.core.result import Err, Ok, Result
from ads_mcp.rules import app as R

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def day_bounds(
    start: str | None, end: str | None
) -> Result[tuple[str | None, str | None], str]:
    """'YYYY-MM-DD' days -> Google's start/end_date_time (App campaigns are day-granular)."""
    for label, value in (("start_date", start), ("end_date", end)):
        if value is not None and not _DATE.match(value):
            return Err(f"'{label}' must be YYYY-MM-DD, got '{value}'.")
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                return Err(f"'{label}' is not a real date: '{value}'.")
    if start and end and end < start:
        return Err("'end_date' is before 'start_date'.")
    return Ok(
        (
            f"{start} 00:00:00" if start else None,
            f"{end} 23:59:59" if end else None,
        )
    )


class AdContent(BaseModel):
    """An App ad's assets: texts inline, the others as asset resource names."""

    model_config = ConfigDict(frozen=True)

    headlines: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    videos: tuple[str, ...] = ()
    html5: tuple[str, ...] = ()


class AdEdit(BaseModel):
    model_config = ConfigDict(frozen=True)

    add_headlines: tuple[str, ...] = ()
    remove_headlines: tuple[str, ...] = ()
    add_descriptions: tuple[str, ...] = ()
    remove_descriptions: tuple[str, ...] = ()
    add_images: tuple[str, ...] = ()
    remove_images: tuple[str, ...] = ()
    add_videos: tuple[str, ...] = ()
    remove_videos: tuple[str, ...] = ()
    add_html5: tuple[str, ...] = ()
    remove_html5: tuple[str, ...] = ()


_FIELDS = ("headlines", "descriptions", "images", "videos", "html5")


def _edited(
    current: tuple[str, ...], add: tuple[str, ...], remove: tuple[str, ...]
) -> tuple[tuple[str, ...], list[str]]:
    missing = [r for r in remove if r not in current]
    kept = tuple(c for c in current if c not in set(remove))
    added = tuple(a for a in dict.fromkeys(add) if a not in kept)
    return kept + added, missing


def edit_ad(
    current: AdContent, edit: AdEdit
) -> Result[tuple[AdContent, tuple[str, ...]], list[R.Problem]]:
    """The ad after the edit, and which fields changed; problems when a removal names nothing there."""
    results = {
        f: _edited(
            getattr(current, f),
            getattr(edit, f"add_{f}"),
            getattr(edit, f"remove_{f}"),
        )
        for f in _FIELDS
    }
    problems = [
        R.Problem(
            code="NOT_IN_AD",
            field=f"remove_{f}",
            message=f"'{m}' is not in the ad's {f}.",
        )
        for f, (_, missing) in results.items()
        for m in missing
    ]
    if problems:
        return Err(problems)
    after = AdContent(**{f: results[f][0] for f in _FIELDS})
    changed = tuple(
        f for f in _FIELDS if getattr(after, f) != getattr(current, f)
    )
    if not changed:
        return Err(
            [
                R.Problem(
                    code="NOTHING_TO_CHANGE",
                    field="ad",
                    message="The edit changes nothing.",
                )
            ]
        )
    return Ok((after, changed))


def kind_of(sub_type: str) -> R.Kind | None:
    """APP_CAMPAIGN -> install, ...; None for anything that is not an App campaign."""
    return next(
        (k for k, s in R.AD_SHAPES.items() if s.sub_type == sub_type), None
    )


APP_AD_TYPES = ("APP_AD", "APP_ENGAGEMENT_AD", "APP_PRE_REGISTRATION_AD")


def pause_refusal(ad_types: Sequence[tuple[str, str]]) -> str | None:
    """campaigns_set_status AD + PAUSED: why App ads in the list cannot be paused, or None."""
    app_ads = [i for i, t in ad_types if t in APP_AD_TYPES]
    if not app_ads:
        return None
    return (
        f"Error: {', '.join(app_ads)} {'is an App ad' if len(app_ads) == 1 else 'are App ads'}. "
        f"{R.APP_AD_NO_PAUSE} Pause the ad group (level AD_GROUP) or the campaign (level CAMPAIGN) instead, or "
        "change the ad's assets with app_ad_update."
    )
