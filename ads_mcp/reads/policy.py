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

"""policy_report, pure part: the queries, the rows shaped, the account → campaign → ad group → ad → asset tree.

Exemptions: Google says whether a finding is exemptible only when a change is submitted (PolicyFindingError);
the report gives the key a person would ask to exempt (`ignorable_policy_topics` for ads and assets), never
exempts anything itself.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ads_mcp.reads.common import name, names, tail_id

MAX_ADS = 500
MAX_ASSETS = 2000
_ACTIVE = "!= 'REMOVED'"


@dataclass(frozen=True)
class Scope:
    campaign_id: str | None = None
    ad_group_id: str | None = None

    def where(self, resource: str) -> str:
        """Extra conditions for a query FROM `resource` (which carries campaign / ad_group attributes)."""
        parts = []
        if self.campaign_id:
            parts.append(f"campaign.id = {self.campaign_id}")
        if self.ad_group_id and resource != "campaign":
            parts.append(f"ad_group.id = {self.ad_group_id}")
        return "".join(f" AND {p}" for p in parts)


def queries(scope: Scope) -> dict:
    return {
        "campaigns": (
            "SELECT customer.descriptive_name, campaign.id, campaign.name, campaign.status, campaign.primary_status, "
            "campaign.primary_status_reasons, campaign.advertising_channel_type, "
            "campaign.advertising_channel_sub_type FROM campaign "
            f"WHERE campaign.status {_ACTIVE}{scope.where('campaign')}"
        ),
        "ad_groups": (
            "SELECT campaign.id, ad_group.id, ad_group.name, ad_group.status, ad_group.primary_status, "
            "ad_group.primary_status_reasons FROM ad_group "
            f"WHERE ad_group.status {_ACTIVE}{scope.where('ad_group')}"
        ),
        "ads": (
            "SELECT ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type, "
            "ad_group_ad.status, ad_group_ad.primary_status, ad_group_ad.primary_status_reasons, "
            "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.review_status, "
            "ad_group_ad.policy_summary.policy_topic_entries FROM ad_group_ad "
            f"WHERE ad_group_ad.status {_ACTIVE}{scope.where('ad_group_ad')} LIMIT {MAX_ADS + 1}"
        ),
        "assets": (
            "SELECT ad_group.id, ad_group_ad_asset_view.ad_group_ad, ad_group_ad_asset_view.field_type, "
            "ad_group_ad_asset_view.enabled, ad_group_ad_asset_view.policy_summary.approval_status, "
            "ad_group_ad_asset_view.policy_summary.review_status, "
            "ad_group_ad_asset_view.policy_summary.policy_topic_entries, asset.id, asset.name, asset.type "
            "FROM ad_group_ad_asset_view WHERE ad_group_ad_asset_view.enabled = TRUE"
            f"{scope.where('ad_group_ad_asset_view')} LIMIT {MAX_ASSETS + 1}"
        ),
        "locations": (
            "SELECT campaign.id, campaign_criterion.location.geo_target_constant, campaign_criterion.negative "
            "FROM campaign_criterion WHERE campaign_criterion.type = 'LOCATION' "
            f"AND campaign_criterion.status {_ACTIVE}{scope.where('campaign')}"
        ),
    }


def _countries(constraint: Any) -> list[str]:
    lists = [
        getattr(constraint, f, None)
        for f in (
            "country_constraint_list",
            "certificate_missing_in_country_list",
            "certificate_domain_mismatch_in_country_list",
        )
    ]
    return [
        tail_id(c.country_criterion)
        for lst in lists
        if lst is not None
        for c in getattr(lst, "countries", None) or []
        if getattr(c, "country_criterion", "")
    ]


def topics(entries: Iterable[Any]) -> list[dict]:
    """Policy findings: topic, type NAME, the countries a constraint names (ids; names filled in later)."""
    out = []
    for e in entries or []:
        countries = [
            c
            for k in getattr(e, "constraints", None) or []
            for c in _countries(k)
        ]
        evidence = [
            t
            for ev in getattr(e, "evidences", None) or []
            for t in getattr(getattr(ev, "text_list", None), "texts", None)
            or []
        ]
        out.append(
            {
                "topic": e.topic,
                "type": name(e.type_),
                **({"countries": countries} if countries else {}),
                **({"evidence": evidence[:5]} if evidence else {}),
            }
        )
    return out


def _exemption(found: list[dict]) -> list[str]:
    """Topics a person may ask to exempt (Google decides at submit); none when every finding is informational."""
    return sorted(
        {
            t["topic"]
            for t in found
            if t["type"] in ("PROHIBITED", "LIMITED", "FULLY_LIMITED")
        }
    )


def _policy(summary: Any) -> dict:
    found = topics(summary.policy_topic_entries)
    exempt = _exemption(found)
    return {
        "approval": name(summary.approval_status),
        "review": name(summary.review_status),
        "policy_topics": found,
        **(
            {"exemptible_keys": {"ignorable_policy_topics": exempt}}
            if exempt
            else {}
        ),
    }


def campaign(row: Any) -> dict:
    c = row.campaign
    return {
        "id": str(c.id),
        "name": c.name,
        "status": name(c.status),
        "primary_status": name(c.primary_status),
        "reasons": names(c.primary_status_reasons),
        "channel": name(c.advertising_channel_type),
        "sub_type": name(c.advertising_channel_sub_type),
    }


def ad_group(row: Any) -> dict:
    g = row.ad_group
    return {
        "campaign_id": str(row.campaign.id),
        "id": str(g.id),
        "name": g.name,
        "status": name(g.status),
        "primary_status": name(g.primary_status),
        "reasons": names(g.primary_status_reasons),
    }


def ad(row: Any) -> dict:
    a = row.ad_group_ad
    return {
        "ad_group_id": str(row.ad_group.id),
        "id": str(a.ad.id),
        "name": a.ad.name or None,
        "type": name(a.ad.type_),
        "status": name(a.status),
        "primary_status": name(a.primary_status),
        "reasons": names(a.primary_status_reasons),
        **_policy(a.policy_summary),
    }


def asset(row: Any) -> dict:
    v = row.ad_group_ad_asset_view
    return {
        "ad_id": tail_id(v.ad_group_ad).rsplit("~", 1)[-1],
        "ad_group_id": str(row.ad_group.id),
        "id": str(row.asset.id),
        "name": row.asset.name or None,
        "type": name(row.asset.type_),
        "field_type": name(v.field_type),
        **_policy(v.policy_summary),
    }


def location(row: Any) -> dict:
    return {
        "campaign_id": str(row.campaign.id),
        "geo_id": tail_id(row.campaign_criterion.location.geo_target_constant),
        "negative": bool(row.campaign_criterion.negative),
    }


def geo_ids(
    locations: list[dict], ads: list[dict], assets: list[dict]
) -> tuple[str, ...]:
    """Every geo id the report names: targeted / excluded locations and policy constraint countries."""
    ids = [l["geo_id"] for l in locations]
    ids += [
        c
        for x in ads + assets
        for t in x["policy_topics"]
        for c in t.get("countries", [])
    ]
    return tuple(dict.fromkeys(i for i in ids if i))


def _named(ids: Iterable[str], geo: Mapping[str, dict]) -> list[dict]:
    return [
        {
            "id": i,
            **(
                {k: geo[i][k] for k in ("name", "type", "country_code")}
                if i in geo
                else {"name": None}
            ),
        }
        for i in ids
    ]


def _with_geo_names(x: dict, geo: Mapping[str, dict]) -> dict:
    return {
        **x,
        "policy_topics": [
            (
                {**t, "countries": _named(t["countries"], geo)}
                if "countries" in t
                else t
            )
            for t in x["policy_topics"]
        ],
    }


def tree(
    account: dict,
    campaigns: list[dict],
    ad_groups: list[dict],
    ads: list[dict],
    assets: list[dict],
    locations: list[dict],
    geo: Mapping[str, dict],
    problems_only: bool = False,
) -> dict:
    """The nested report. problems_only keeps entities that are not ELIGIBLE or carry a non-descriptive finding."""

    def flagged(x: dict) -> bool:
        return x.get("primary_status") not in (None, "ELIGIBLE") or bool(
            _exemption(x.get("policy_topics", []))
        )

    def children(items: list[dict], key: str, value: str) -> list[dict]:
        return [
            {k: v for k, v in i.items() if k != key}
            for i in items
            if i[key] == value
        ]

    def keep(x: dict, kids: list) -> bool:
        return not problems_only or flagged(x) or bool(kids)

    def ad_node(a: dict, group_id: str) -> dict:
        mine = [
            s
            for s in assets
            if s["ad_id"] == a["id"] and s["ad_group_id"] == group_id
        ]
        kids = [
            {
                k: v
                for k, v in _with_geo_names(s, geo).items()
                if k not in ("ad_id", "ad_group_id")
            }
            for s in mine
            if keep(s, [])
        ]
        return {**_with_geo_names(a, geo), "assets": kids}

    def group_node(g: dict) -> dict:
        kids = [
            ad_node(a, g["id"]) for a in children(ads, "ad_group_id", g["id"])
        ]
        return {**g, "ads": [a for a in kids if keep(a, a["assets"])]}

    def campaign_node(c: dict) -> dict:
        locs = [l for l in locations if l["campaign_id"] == c["id"]]
        kids = [
            group_node(g) for g in children(ad_groups, "campaign_id", c["id"])
        ]
        return {
            **c,
            "geo": {
                "targeted": _named(
                    [l["geo_id"] for l in locs if not l["negative"]], geo
                ),
                "excluded": _named(
                    [l["geo_id"] for l in locs if l["negative"]], geo
                ),
            },
            "ad_groups": [g for g in kids if keep(g, g["ads"])],
        }

    nodes = [campaign_node(c) for c in campaigns]
    return {
        **account,
        "campaigns": [c for c in nodes if keep(c, c["ad_groups"])],
    }
