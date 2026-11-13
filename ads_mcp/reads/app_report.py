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

"""app_report, pure part: one GAQL per breakdown and the rows shaped with CPA / ROAS.

Installs are `conversions` when the campaign optimizes for installs (App campaigns report installs as
conversions of the install conversion action; by='conversion_action' splits them). Money is in account currency
units (micros / 1e6).
"""

from dataclasses import dataclass
from typing import Any, Literal

from ads_mcp.reads.common import micros_to_units, name, ratio, tail_id

By = Literal["day", "network", "asset", "combination", "conversion_action"]
MAX_ROWS = 1000
_METRICS = (
    "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
    "metrics.conversions_value, metrics.all_conversions"
)


@dataclass(frozen=True)
class Target:
    campaign_id: str | None = None
    ad_group_id: str | None = None

    def where(self) -> str:
        return (
            f"ad_group.id = {self.ad_group_id}"
            if self.ad_group_id
            else f"campaign.id = {self.campaign_id}"
        )

    @property
    def level(self) -> str:
        return "ad_group" if self.ad_group_id else "campaign"


def query(by: By, target: Target, dates: str) -> str:
    level = target.level
    if by == "day":
        return (
            f"SELECT segments.date, {_METRICS} FROM {level} WHERE {target.where()} AND {dates} "
            f"ORDER BY segments.date LIMIT {MAX_ROWS}"
        )
    if by == "network":
        return f"SELECT segments.ad_network_type, {_METRICS} FROM {level} WHERE {target.where()} AND {dates}"
    if by == "conversion_action":
        return (
            "SELECT segments.conversion_action, segments.conversion_action_name, "
            "segments.conversion_action_category, metrics.conversions, metrics.conversions_value, "
            f"metrics.all_conversions FROM {level} WHERE {target.where()} AND {dates}"
        )
    if by == "asset":
        return (
            "SELECT asset.id, asset.name, asset.type, asset.text_asset.text, asset.youtube_video_asset.youtube_video_id, "
            "ad_group.id, ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label, "
            f"ad_group_ad_asset_view.enabled, {_METRICS} FROM ad_group_ad_asset_view "
            f"WHERE {target.where()} AND {dates} ORDER BY metrics.cost_micros DESC LIMIT {MAX_ROWS}"
        )
    # combination: no metrics or dates on this view; Google ranks the combinations itself.
    return (
        "SELECT ad_group.id, ad_group.name, app_top_combination_view.resource_name, "
        "app_top_combination_view.ad_group_top_combinations FROM app_top_combination_view "
        f"WHERE {target.where()} LIMIT {MAX_ROWS}"
    )


def metrics(m: Any) -> dict:
    cost = micros_to_units(m.cost_micros)
    conv = round(float(m.conversions or 0), 2)
    value = round(float(m.conversions_value or 0), 2)
    return {
        "impressions": int(m.impressions or 0),
        "clicks": int(m.clicks or 0),
        "cost": cost,
        "conversions": conv,
        "conversions_value": value,
        "all_conversions": round(float(m.all_conversions or 0), 2),
        "cpa": ratio(cost, conv),
        "roas": ratio(value, cost),
    }


def row(by: By, r: Any) -> dict:
    if by == "day":
        return {"date": r.segments.date, **metrics(r.metrics)}
    if by == "network":
        return {
            "network": name(r.segments.ad_network_type),
            **metrics(r.metrics),
        }
    if by == "conversion_action":
        return {
            "conversion_action_id": tail_id(r.segments.conversion_action),
            "name": r.segments.conversion_action_name,
            "category": name(r.segments.conversion_action_category),
            "conversions": round(float(r.metrics.conversions or 0), 2),
            "conversions_value": round(
                float(r.metrics.conversions_value or 0), 2
            ),
            "all_conversions": round(float(r.metrics.all_conversions or 0), 2),
        }
    if by == "asset":
        a, v = r.asset, r.ad_group_ad_asset_view
        return {
            "asset_id": str(a.id),
            "ad_group_id": str(r.ad_group.id),
            "type": name(a.type_),
            "field_type": name(v.field_type),
            "performance_label": name(v.performance_label),
            "enabled": bool(v.enabled),
            **({"name": a.name} if a.name else {}),
            **({"text": a.text_asset.text} if a.text_asset.text else {}),
            **(
                {"youtube_video_id": a.youtube_video_asset.youtube_video_id}
                if a.youtube_video_asset.youtube_video_id
                else {}
            ),
            **metrics(r.metrics),
        }
    view = r.app_top_combination_view
    return {
        "ad_group_id": str(r.ad_group.id),
        "ad_group": r.ad_group.name,
        "category": tail_id(view.resource_name).rsplit("~", 1)[-1],
        "combinations": [
            [
                {
                    "asset_id": tail_id(u.asset),
                    "field_type": name(u.served_asset_field_type),
                }
                for u in combo.asset_combination_served_assets
            ]
            for combo in view.ad_group_top_combinations
        ],
    }


def totals(by: By, rows: list[dict]) -> dict | None:
    """Sum of the rows for day / network (they partition the traffic); None for the other breakdowns, whose rows
    overlap (assets share impressions) or carry no cost."""
    if by not in ("day", "network") or not rows:
        return None
    keys = (
        "impressions",
        "clicks",
        "cost",
        "conversions",
        "conversions_value",
        "all_conversions",
    )
    sums = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    sums["impressions"], sums["clicks"] = int(sums["impressions"]), int(
        sums["clicks"]
    )
    return {
        **sums,
        "cpa": ratio(sums["cost"], sums["conversions"]),
        "roas": ratio(sums["conversions_value"], sums["cost"]),
    }


ASSETS_BY_ID = (
    "SELECT asset.id, asset.name, asset.type, asset.text_asset.text, asset.youtube_video_asset.youtube_video_id "
    "FROM asset WHERE asset.id IN ({ids})"
)


def combination_asset_ids(rows: list[dict]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            u["asset_id"]
            for r in rows
            for combo in r.get("combinations", [])
            for u in combo
        )
    )


def asset_label(r: Any) -> dict:
    a = r.asset
    text = a.text_asset.text or a.youtube_video_asset.youtube_video_id or a.name
    return {"type": name(a.type_), **({"text": text} if text else {})}


def with_asset_labels(rows: list[dict], labels: dict) -> list[dict]:
    """Each served asset of a combination with its type and text / video id / name."""
    return [
        {
            **r,
            "combinations": [
                [{**u, **labels.get(u["asset_id"], {})} for u in combo]
                for combo in r["combinations"]
            ],
        }
        for r in rows
    ]
