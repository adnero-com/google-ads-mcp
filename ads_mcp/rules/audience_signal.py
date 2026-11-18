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

"""Audience signals (pure): where an Audience may attach, and what an attach or detach changes.

- App install campaigns (MULTI_CHANNEL / APP_CAMPAIGN): an ad group criterion `audience` (AudienceInfo), at most
  ONE per ad group; no user list criteria on install ad groups; no negatives, no bids.
- App engagement: ad groups take a user list (UserListInfo) of app users, not an Audience.
- App pre-registration: no ad group criteria at all.
- Performance Max: AssetGroupSignal `audience` per asset group (several allowed; immutable: remove and re-add).
https://developers.google.com/google-ads/api/docs/app-campaigns/create-ad-group
https://developers.google.com/google-ads/api/docs/performance-max/asset-group-signals
"""

from typing import Literal

from pydantic import BaseModel

TargetKind = Literal["AD_GROUP", "ASSET_GROUP"]


class Target(BaseModel, frozen=True):
    kind: TargetKind
    id: str
    name: str = ""
    campaign_id: str = ""


class Signal(BaseModel, frozen=True):
    """An audience signal in place: the criterion / asset group signal, on which target, which audience."""

    target_id: str
    resource_name: str
    audience_id: str


class AttachPlan(BaseModel, frozen=True):
    create: tuple[Target, ...] = ()
    remove: tuple[Signal, ...] = ()
    already: tuple[str, ...] = ()  # target ids that have this audience already
    problems: tuple[str, ...] = ()


def target_problem(channel: str, sub_type: str) -> str | None:
    """None when the campaign takes Audience signals; else why not, and what to do instead."""
    if channel == "PERFORMANCE_MAX":
        return None
    if channel == "MULTI_CHANNEL" and sub_type == "APP_CAMPAIGN":
        return None
    if sub_type == "APP_CAMPAIGN_FOR_ENGAGEMENT":
        return (
            "App engagement ad groups take a user list of your app's users, not an Audience: pass user_list_ids "
            "to app_ad_group_create."
        )
    if sub_type == "APP_CAMPAIGN_FOR_PRE_REGISTRATION":
        return "App pre-registration ad groups take no criteria at all (Google refuses them)."
    return (
        f"audience signals attach to App install campaigns and Performance Max asset groups; this campaign is "
        f"{channel or '?'} / {sub_type or '?'}. For other types (Demand Gen, Display, Search) use api_mutate."
    )


def plan_attach(
    targets: tuple[Target, ...],
    existing: tuple[Signal, ...],
    audience_id: str,
    replace: bool,
) -> AttachPlan:
    """Which targets get the audience, which signals go (replace), and what blocks it.

    An ad group holds at most one audience: another one there is a problem unless `replace`. Asset groups take
    several: `replace` removes their other audience signals (search themes stay).
    """

    def on(t: Target) -> tuple[Signal, ...]:
        return tuple(s for s in existing if s.target_id == t.id)

    already = tuple(
        t.id
        for t in targets
        if any(s.audience_id == audience_id for s in on(t))
    )
    todo = tuple(t for t in targets if t.id not in already)
    others = tuple(
        (t, s) for t in targets for s in on(t) if s.audience_id != audience_id
    )
    blocked = tuple(
        f"ad group {t.id} already has audience {s.audience_id} (one audience per App ad group): pass "
        "replace=true to swap it"
        for t, s in others
        if t.kind == "AD_GROUP" and not replace
    )
    remove = tuple(s for _, s in others) if replace else ()
    return AttachPlan(
        create=todo, remove=remove, already=already, problems=blocked
    )


def plan_detach(
    existing: tuple[Signal, ...], audience_id: str
) -> tuple[Signal, ...]:
    return tuple(s for s in existing if s.audience_id == audience_id)
