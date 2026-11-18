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

"""Audiences as data (pure): what an Audience holds, its allowed values, its checks.

An Audience (v26 `Audience`, AudienceService / GoogleAdsService.Mutate audience_operation) is a reusable set of
dimensions: age (AgeSegment min 18/25/35/45/55/65, max 24/34/44/54/64 or open), gender, household income,
parental status, and audience segments (user lists, affinity/in-market interests, life events, detailed
demographics, custom segments). One dimension per type (TOO_MANY_DIMENSIONS_OF_SAME_TYPE); exclusions are user
lists only. https://developers.google.com/google-ads/api/docs/remarketing/audiences
"""

import re
from typing import Any

from pydantic import BaseModel

AGE_MINS = (18, 25, 35, 45, 55, 65)
AGE_MAXES = (24, 34, 44, 54, 64)
GENDERS = ("MALE", "FEMALE")
PARENTAL_STATUSES = ("PARENT", "NOT_A_PARENT")
INCOME_RANGES = (
    "INCOME_RANGE_0_50",
    "INCOME_RANGE_50_60",
    "INCOME_RANGE_60_70",
    "INCOME_RANGE_70_80",
    "INCOME_RANGE_80_90",
    "INCOME_RANGE_90_UP",
)
NAME_MAX = 255

# segment kind -> (resource collection, AudienceSegment oneof field, inner field)
SEGMENTS: dict[str, tuple[str, str, str]] = {
    "user_list": ("userLists", "user_list", "user_list"),
    "user_interest": (
        "userInterests",
        "user_interest",
        "user_interest_category",
    ),
    "life_event": ("lifeEvents", "life_event", "life_event"),
    "detailed_demographic": (
        "detailedDemographics",
        "detailed_demographic",
        "detailed_demographic",
    ),
    "custom_audience": (
        "customAudiences",
        "custom_audience",
        "custom_audience",
    ),
}

_AGE = re.compile(r"^\s*(\d{2})\s*(?:-\s*(\d{2})|\+)?\s*$")


class Demo(BaseModel, frozen=True):
    """One demographic dimension: its values and whether people Google cannot classify count too."""

    values: tuple[str, ...]
    include_undetermined: bool = False


class AudienceSpec(BaseModel, frozen=True):
    name: str
    description: str = ""
    ages: Demo | None = None  # values "25-54", "65+"
    genders: Demo | None = None
    parental_statuses: Demo | None = None
    income_ranges: Demo | None = None
    segments: tuple[tuple[str, str], ...] = ()  # (kind, id)
    exclude_user_list_ids: tuple[str, ...] = ()


def parse_age(text: str) -> tuple[int, int | None] | str:
    """'25-54' -> (25, 54); '65+' or '65' -> (65, None); else the problem."""
    match = _AGE.match(str(text))
    if not match:
        return f"age range '{text}' is not 'MIN-MAX' or 'MIN+' (e.g. '25-54', '65+')"
    low, high = int(match.group(1)), match.group(2)
    if low not in AGE_MINS:
        return f"age range '{text}': the minimum is one of {AGE_MINS}"
    if high is None:
        return (low, None)
    if int(high) not in AGE_MAXES or int(high) <= low:
        return f"age range '{text}': the maximum is one of {AGE_MAXES} and above the minimum (or 'MIN+')"
    return (low, int(high))


def age_text(low: int, high: int | None) -> str:
    return f"{low}+" if high is None else f"{low}-{high}"


def _enum_problems(
    demo: Demo | None, allowed: tuple[str, ...], what: str
) -> list[str]:
    if demo is None:
        return []
    bad = [v for v in demo.values if v not in allowed]
    return (
        [f"{what}: {', '.join(bad)} not allowed (use {', '.join(allowed)})"]
        if bad
        else []
    )


def problems(spec: AudienceSpec) -> list[str]:
    """Everything Google would refuse that we can see before asking it."""
    name = spec.name.strip()
    name_problem = (
        []
        if 0 < len(name) <= NAME_MAX
        else [f"name is 1-{NAME_MAX} characters"]
    )
    ages = [
        p
        for p in (parse_age(a) for a in (spec.ages.values if spec.ages else ()))
        if isinstance(p, str)
    ]
    empty_demo = [
        f"{what} is given but empty (omit it to leave it out)"
        for what, demo in (
            ("age_ranges", spec.ages),
            ("genders", spec.genders),
            ("parental_statuses", spec.parental_statuses),
            ("income_ranges", spec.income_ranges),
        )
        if demo is not None and not demo.values
    ]
    kinds = [
        f"segment kind '{k}' unknown"
        for k, _ in spec.segments
        if k not in SEGMENTS
    ]
    nothing = (
        []
        if any(
            (
                spec.ages,
                spec.genders,
                spec.parental_statuses,
                spec.income_ranges,
                spec.segments,
            )
        )
        else [
            "an audience needs at least one dimension (ages, genders, parental status, income or segments)"
        ]
    )
    return (
        name_problem
        + ages
        + _enum_problems(spec.genders, GENDERS, "genders")
        + _enum_problems(
            spec.parental_statuses, PARENTAL_STATUSES, "parental_statuses"
        )
        + _enum_problems(spec.income_ranges, INCOME_RANGES, "income_ranges")
        + empty_demo
        + kinds
        + nothing
    )


def demo_of(
    values: list[str] | None, undetermined: bool, upper: bool = True
) -> Demo | None:
    """A tool argument as a dimension: None = not given; values de-duplicated in order."""
    if values is None:
        return None
    clean = tuple(
        dict.fromkeys(
            str(v).strip().upper() if upper else str(v).strip()
            for v in values
            if str(v).strip()
        )
    )
    return Demo(values=clean, include_undetermined=bool(undetermined))


def summary(spec: AudienceSpec) -> dict[str, Any]:
    """What the person reviews: one line per dimension that is set."""

    def demo(d: Demo | None) -> str | None:
        if d is None:
            return None
        return ", ".join(d.values) + (
            " (+ undetermined)" if d.include_undetermined else ""
        )

    by_kind = {
        kind: [i for k, i in spec.segments if k == kind] or None
        for kind in SEGMENTS
    }
    return {
        "Description": spec.description or None,
        "Ages": demo(spec.ages),
        "Genders": demo(spec.genders),
        "Parental status": demo(spec.parental_statuses),
        "Household income": demo(spec.income_ranges),
        "User lists": by_kind["user_list"],
        "Interests (affinity / in-market)": by_kind["user_interest"],
        "Life events": by_kind["life_event"],
        "Detailed demographics": by_kind["detailed_demographic"],
        "Custom segments": by_kind["custom_audience"],
        "Excluded user lists": list(spec.exclude_user_list_ids) or None,
    }


def _tail(resource_name: str) -> str:
    return str(resource_name).rsplit("/", 1)[-1]


def _demo_from(dim: dict, key: str, values_key: str) -> Demo:
    return Demo(
        values=tuple(str(v) for v in dim.get(values_key, [])),
        include_undetermined=bool(dim.get("include_undetermined", False)),
    )


def spec_from_plain(
    name: str, description: str, dimensions: list[dict], exclusion: dict | None
) -> AudienceSpec:
    """An Audience as read (plain data, core/plain.py) back into a spec, for updates that keep what is not changed."""
    found: dict[str, Any] = {}
    segments: list[tuple[str, str]] = []
    for dim in dimensions or []:
        if "age" in dim:
            ranges = dim["age"].get("age_ranges", [])
            found["ages"] = Demo(
                values=tuple(
                    age_text(int(r.get("min_age", 18)), r.get("max_age"))
                    for r in ranges
                ),
                include_undetermined=bool(
                    dim["age"].get("include_undetermined", False)
                ),
            )
        elif "gender" in dim:
            found["genders"] = _demo_from(dim["gender"], "gender", "genders")
        elif "parental_status" in dim:
            found["parental_statuses"] = _demo_from(
                dim["parental_status"], "parental_status", "parental_statuses"
            )
        elif "household_income" in dim:
            found["income_ranges"] = _demo_from(
                dim["household_income"], "household_income", "income_ranges"
            )
        elif "audience_segments" in dim:
            for seg in dim["audience_segments"].get("segments", []):
                segments.extend(
                    (kind, _tail(seg[field][inner]))
                    for kind, (_, field, inner) in SEGMENTS.items()
                    if field in seg and inner in seg[field]
                )
    excluded = tuple(
        _tail(e["user_list"]["user_list"])
        for e in (exclusion or {}).get("exclusions", [])
        if "user_list" in e
    )
    return AudienceSpec(
        name=name,
        description=description or "",
        segments=tuple(segments),
        exclude_user_list_ids=excluded,
        **found,
    )
