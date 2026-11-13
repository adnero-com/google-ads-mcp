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

"""Pure helpers shared by the read tools: GAQL literals, dates, enum names, Google error codes."""

import datetime
import re
from typing import Any, Iterable

_DIGITS = re.compile(r"^\d+$")
DATE_PRESETS = (
    "TODAY",
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_14_DAYS",
    "LAST_30_DAYS",
    "LAST_BUSINESS_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "THIS_WEEK_SUN_TODAY",
    "THIS_WEEK_MON_TODAY",
    "LAST_WEEK_SUN_SAT",
    "LAST_WEEK_MON_SUN",
)
MAX_DAYS = 400


def quote(text: str) -> str:
    """A GAQL string literal: single quotes, backslash and quote escaped."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def re2_escape(text: str) -> str:
    """Text matched literally inside a REGEXP_MATCH pattern (RE2)."""
    return re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", str(text))


def digits(value: Any) -> str | None:
    text = str(value if value is not None else "").replace("-", "").strip()
    return text if _DIGITS.match(text) else None


def id_list(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(d for d in (digits(v) for v in values) if d))


def date_condition(
    start: str | None, end: str | None, preset: str | None
) -> str | Exception:
    """`segments.date ...` for a preset or a YYYY-MM-DD range (at most 400 days), else the error to raise."""
    if preset:
        p = preset.strip().upper()
        if p not in DATE_PRESETS:
            return ValueError(
                f"date preset is one of {', '.join(DATE_PRESETS)}."
            )
        return f"segments.date DURING {p}"
    try:
        a = datetime.date.fromisoformat(str(start))
        b = datetime.date.fromisoformat(str(end))
    except ValueError:
        return ValueError(
            "Give start and end as YYYY-MM-DD, or a preset such as LAST_30_DAYS."
        )
    if b < a:
        return ValueError("end is before start.")
    if (b - a).days > MAX_DAYS:
        return ValueError(f"At most {MAX_DAYS} days per report.")
    return f"segments.date BETWEEN '{a.isoformat()}' AND '{b.isoformat()}'"


def name(value: Any) -> str | None:
    """An enum as its NAME (never a number); None for an unset value."""
    if value is None:
        return None
    text = getattr(value, "name", None)
    if text is None:
        return str(value)
    return None if text in ("UNSPECIFIED", "") else text


def names(values: Iterable[Any]) -> list[str]:
    return [n for n in (name(v) for v in values or ()) if n]


def micros_to_units(micros: Any) -> float:
    return round(int(micros or 0) / 1_000_000, 2)


def ratio(a: float, b: float, digits_: int = 2) -> float | None:
    return round(a / b, digits_) if b else None


def tail_id(resource_name: str) -> str:
    """'geoTargetConstants/2124' -> '2124'; 'customers/1/adGroupAds/2~3' -> '2~3'."""
    return str(resource_name or "").rsplit("/", 1)[-1]


def _error_code_pairs(error: Any) -> list[tuple[str, str]]:
    code = getattr(error, "error_code", None)
    if code is None:
        return []
    try:  # proto-plus: the oneof says which error enum is set
        import proto

        pb = type(code).pb(code) if isinstance(code, proto.Message) else None
        which = pb.WhichOneof("error_code") if pb is not None else None
        if which:
            return [(which, name(getattr(code, which)) or "")]
    except Exception:  # a fake or another shape: fall through
        pass
    return [
        (k, name(v) or "")
        for k, v in vars(code).items()
        if not k.startswith("_") and name(v)
    ]


def google_errors(exc: Exception) -> list[dict]:
    """[{kind: 'identity_verification_error', code: 'BILLING_NOT_ON_MONTHLY_INVOICING', message}] (any depth)."""
    failure = getattr(exc, "failure", None)
    out = []
    for error in getattr(failure, "errors", None) or []:
        for kind, code in _error_code_pairs(error) or [("", "")]:
            out.append(
                {
                    "kind": kind,
                    "code": code,
                    "message": getattr(error, "message", "") or "",
                }
            )
    return out


def why(exc: Exception) -> str:
    """Google's own words for a refusal (never a token or header)."""
    errors = google_errors(exc)
    text = "; ".join(
        f"{e['code']}: {e['message']}" if e["code"] else e["message"]
        for e in errors
        if e["message"] or e["code"]
    )
    return text or str(exc)[:300] or type(exc).__name__
