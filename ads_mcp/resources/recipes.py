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

"""App campaign recipes (adnero fork): steps + one atomic googleAds:mutate example body each, as MCP resources
`resource://recipes/app-install`, `.../app-engagement`, `.../app-pre-registration`.

The bodies are data (RECIPES) so tests check them against the generated schemas. `{customer_id}` and the
`<...>` values are placeholders the agent fills in.
"""

import json

from ads_mcp.coordinator import mcp

C = "customers/{customer_id}"
BUDGET, CAMPAIGN, AD_GROUP = (
    f"{C}/campaignBudgets/-1",
    f"{C}/campaigns/-2",
    f"{C}/adGroups/-3",
)

_COMMON_BEFORE = [
    "accounts_get {customer_id}: the currency (budgets and targets are micros of it), the time zone (start/end "
    "times are in it) and conversion tracking.",
    "lookup_geo {names: [...]} and lookup_language {query} for the ids of the locations (included and excluded) and "
    "languages.",
    "Assets: images (1:1, 1.91:1 or 4:5 within 1%, JPG/PNG, at most 5 MB, unique name), YouTube videos, HTML5 ZIPs: "
    "upload them first and keep their resource names (customers/{customer_id}/assets/<id>). Headlines and "
    "descriptions are text inline in the ad (2-5 headlines up to 30 characters, 1-5 descriptions up to 90).",
]
_COMMON_AFTER = [
    "schema_get_mutate {resource, operation: 'create', fields: [...]} shows the exact fields of each operation when in "
    "doubt.",
    "api_mutate {customer_id, path: 'customers/{customer_id}/googleAds:mutate', body: <example, filled in>} with "
    "validate_only=true: the schemas, then Google, check every operation; nothing changes. Show the change list to "
    "the person.",
    "Apply with validate_only=false and the plan_id once the person approves. Everything is created in one atomic "
    "request: all of it or none.",
    "The campaign is PAUSED. The App ad is ENABLED (App ads cannot be paused: pause the ad group or campaign instead). "
    "The person enables the campaign when ready.",
    "policy_report {customer_id, campaign_id}: primary status, approval and policy topics once Google has reviewed it; "
    "app_report {customer_id, campaign_id, by} for results.",
]


def _budget(name: str) -> dict:
    return {
        "campaignBudgetOperation": {
            "create": {
                "resourceName": BUDGET,
                "name": name,
                "amountMicros": "<daily budget in micros, e.g. 50000000 = 50.00>",
                "deliveryMethod": "STANDARD",
                "explicitlyShared": False,
            }
        }
    }


def _criteria() -> list[dict]:
    return [
        {
            "campaignCriterionOperation": {
                "create": {
                    "campaign": CAMPAIGN,
                    "location": {
                        "geoTargetConstant": "geoTargetConstants/2124"
                    },
                }
            }
        },
        {
            "campaignCriterionOperation": {
                "create": {
                    "campaign": CAMPAIGN,
                    "negative": True,
                    "location": {
                        "geoTargetConstant": "geoTargetConstants/20113"
                    },
                }
            }
        },
        {
            "campaignCriterionOperation": {
                "create": {
                    "campaign": CAMPAIGN,
                    "language": {"languageConstant": "languageConstants/1000"},
                }
            }
        },
    ]


def _campaign(sub_type: str, setting: dict, bidding: dict, extra: dict) -> dict:
    return {
        "campaignOperation": {
            "create": {
                "resourceName": CAMPAIGN,
                "name": "<campaign name>",
                "status": "PAUSED",
                "advertisingChannelType": "MULTI_CHANNEL",
                "advertisingChannelSubType": sub_type,
                "campaignBudget": BUDGET,
                "appCampaignSetting": setting,
                **bidding,
                **extra,
                "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
                "startDateTime": "<yyyy-MM-dd> 00:00:00",
            }
        }
    }


def _ad_group() -> dict:
    return {
        "adGroupOperation": {
            "create": {
                "resourceName": AD_GROUP,
                "name": "<ad group name>",
                "campaign": CAMPAIGN,
                "status": "ENABLED",
            }
        }
    }


def _ad(kind: str, videos_key: str) -> dict:
    return {
        "adGroupAdOperation": {
            "create": {
                "adGroup": AD_GROUP,
                "status": "ENABLED",
                "ad": {
                    kind: {
                        "headlines": [
                            {"text": "<headline 1>"},
                            {"text": "<headline 2>"},
                        ],
                        "descriptions": [{"text": "<description 1>."}],
                        "images": [{"asset": f"{C}/assets/<image asset id>"}],
                        videos_key: [
                            {"asset": f"{C}/assets/<youtube video asset id>"}
                        ],
                    }
                },
            }
        }
    }


RECIPES = {
    "app-install": {
        "title": "App campaign for installs (APP_CAMPAIGN)",
        "steps": _COMMON_BEFORE
        + [
            "Goal → bidding (one pair): installs with a target CPI: OPTIMIZE_INSTALLS_TARGET_INSTALL_COST + "
            "targetCpa.targetCpaMicros; installs, no target: OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST + "
            "maximizeConversions; in-app actions: OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST + targetCpa and "
            "selectiveOptimization.conversionActions (required; resolve {kind: 'conversion_action'}: same app id, not "
            "a DOWNLOAD category); value: OPTIMIZE_RETURN_ON_ADVERTISING_SPEND + targetRoas.targetRoas (3.0 = 300%) "
            "with Firebase conversion actions.",
            "appId is the Android package (com.example.app) or the iOS numeric App Store id; appId and appStore cannot "
            "change after creation. The budget is never shared.",
        ]
        + _COMMON_AFTER,
        "path": "customers/{customer_id}/googleAds:mutate",
        "body": {
            "mutateOperations": [
                _budget("<campaign name> budget"),
                _campaign(
                    "APP_CAMPAIGN",
                    {
                        "appId": "<com.example.app>",
                        "appStore": "GOOGLE_APP_STORE",
                        "biddingStrategyGoalType": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
                    },
                    {
                        "targetCpa": {
                            "targetCpaMicros": "<target cost per install in micros>"
                        }
                    },
                    {},
                ),
                *_criteria(),
                _ad_group(),
                _ad("appAd", "youtubeVideos"),
            ]
        },
    },
    "app-engagement": {
        "title": "App campaign for engagement (APP_CAMPAIGN_FOR_ENGAGEMENT)",
        "steps": _COMMON_BEFORE
        + [
            "The app needs enough installs, and an in-app conversion action: OPTIMIZE_IN_APP_CONVERSIONS_TARGET_"
            "CONVERSION_COST + targetCpa + selectiveOptimization.conversionActions.",
            "Every ad group MUST target a user list of the app's users (adGroupCriterion userList; resolve {kind: "
            "'user_list'}). The ad group may set its own targetCpaMicros.",
            "The ad is appEngagementAd, whose videos are `videos` (not youtubeVideos).",
        ]
        + _COMMON_AFTER,
        "path": "customers/{customer_id}/googleAds:mutate",
        "body": {
            "mutateOperations": [
                _budget("<campaign name> budget"),
                _campaign(
                    "APP_CAMPAIGN_FOR_ENGAGEMENT",
                    {
                        "appId": "<com.example.app>",
                        "appStore": "GOOGLE_APP_STORE",
                        "biddingStrategyGoalType": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
                    },
                    {
                        "targetCpa": {
                            "targetCpaMicros": "<target cost per action in micros>"
                        }
                    },
                    {
                        "selectiveOptimization": {
                            "conversionActions": [f"{C}/conversionActions/<id>"]
                        }
                    },
                ),
                *_criteria(),
                _ad_group(),
                {
                    "adGroupCriterionOperation": {
                        "create": {
                            "adGroup": AD_GROUP,
                            "userList": {"userList": f"{C}/userLists/<id>"},
                        }
                    }
                },
                _ad("appEngagementAd", "videos"),
            ]
        },
    },
    "app-pre-registration": {
        "title": "App pre-registration campaign (APP_CAMPAIGN_FOR_PRE_REGISTRATION, Android only)",
        "steps": _COMMON_BEFORE
        + [
            "Android (GOOGLE_APP_STORE) apps open for pre-registration in Google Play only.",
            "Bidding: OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME + targetCpa, and optimizationGoalSetting with "
            "APP_PRE_REGISTRATION. Google adds the conversion actions itself (no selectiveOptimization).",
        ]
        + _COMMON_AFTER,
        "path": "customers/{customer_id}/googleAds:mutate",
        "body": {
            "mutateOperations": [
                _budget("<campaign name> budget"),
                _campaign(
                    "APP_CAMPAIGN_FOR_PRE_REGISTRATION",
                    {
                        "appId": "<com.example.app>",
                        "appStore": "GOOGLE_APP_STORE",
                        "biddingStrategyGoalType": "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME",
                    },
                    {
                        "targetCpa": {
                            "targetCpaMicros": "<target cost per pre-registration in micros>"
                        }
                    },
                    {
                        "optimizationGoalSetting": {
                            "optimizationGoalTypes": ["APP_PRE_REGISTRATION"]
                        }
                    },
                ),
                *_criteria(),
                _ad_group(),
                _ad("appPreRegistrationAd", "youtubeVideos"),
            ]
        },
    },
}


def recipe_text(slug: str) -> str:
    return json.dumps(RECIPES[slug], indent=1, ensure_ascii=False)


@mcp.resource(
    uri="resource://recipes/app-install", mime_type="application/json"
)
def app_install_recipe() -> str:
    """How to create an App campaign for installs: steps, then one atomic googleAds:mutate example body."""
    return recipe_text("app-install")


@mcp.resource(
    uri="resource://recipes/app-engagement", mime_type="application/json"
)
def app_engagement_recipe() -> str:
    """How to create an App engagement campaign (user list required): steps and an example body."""
    return recipe_text("app-engagement")


@mcp.resource(
    uri="resource://recipes/app-pre-registration", mime_type="application/json"
)
def app_pre_registration_recipe() -> str:
    """How to create an Android App pre-registration campaign: steps and an example body."""
    return recipe_text("app-pre-registration")
