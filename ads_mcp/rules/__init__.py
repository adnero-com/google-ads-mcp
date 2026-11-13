"""Hand-written rules no machine source holds (DESIGN.md principle 1), served next to the schemas.

`rules_for(resource)` is the hook the schema layer calls (schema_get_mutate): resource keys are
snake_case GAQL names ("ad_group_ad", "campaign", "asset"). Each rule is plain data:
{id, applies_to, rule, source}.
"""

from ads_mcp.rules import app as _app

_SOURCE_HELP = "Google Ads Help Center (App campaign asset specs)"
_SOURCE_API = "Google Ads API App campaign docs"
_SOURCE_LIVE = "Seen live (Google refused it)"


def _text(field: str, limit: _app.TextLimit) -> dict:
    return {
        "id": f"app_ad.{field}",
        "applies_to": f"ad.app_ad|app_engagement_ad|app_pre_registration_ad.{field}",
        "rule": f"{limit.min_count}-{limit.max_count} items, at most {limit.max_chars} characters each",
        "min_count": limit.min_count,
        "max_count": limit.max_count,
        "max_chars": limit.max_chars,
        "source": _SOURCE_HELP,
    }


_IMAGES = {
    "id": "app_image.ratio",
    "applies_to": "IMAGE assets used in App ads",
    "rule": "JPG or PNG, at most 5 MB; aspect ratio within `tolerance` (0.5%: Google refused 0.8% off live; the Help Center says 1%) of one of the listed ratios, at least the minimum size",
    "ratios": [
        {
            "name": r.name,
            "ratio": r.ratio,
            "min_width": r.min_width,
            "min_height": r.min_height,
        }
        for r in _app.IMAGE_RATIOS
    ],
    "tolerance": _app.RATIO_TOLERANCE,
    "max_bytes": _app.IMAGE_MAX_BYTES,
    "source": f"{_SOURCE_HELP}; {_SOURCE_LIVE}: ASPECT_RATIO_NOT_ALLOWED",
}
_HTML5 = {
    "id": "app_html5.bundle",
    "applies_to": "MEDIA_BUNDLE assets used in app_ad.html5_media_bundles",
    "rule": "zip at most 5 MB and 512 files, an .html primary entry, <meta name='ad.orientation'>, https URLs only",
    "max_bytes": _app.HTML5_MAX_BYTES,
    "max_files": _app.HTML5_MAX_FILES,
    "source": _SOURCE_HELP,
}

_RULES: dict[str, list[dict]] = {
    "ad_group_ad": [
        _text("headlines", _app.HEADLINES),
        _text("descriptions", _app.DESCRIPTIONS),
        {
            "id": "app_ad.counts",
            "applies_to": "ad.app_ad|app_engagement_ad|app_pre_registration_ad",
            "rule": "at most 20 images, 20 YouTube videos, 20 HTML5 bundles (HTML5: app_ad only)",
            "source": _SOURCE_HELP,
        },
        {
            "id": "app_ad.type_per_sub_type",
            "applies_to": "ad",
            "rule": "the ad info must match the campaign sub-type",
            "map": {s.sub_type: s.ad_field for s in _app.AD_SHAPES.values()},
            "source": _SOURCE_API,
        },
        {
            "id": "app_ad.no_pause",
            "applies_to": "status",
            "rule": _app.APP_AD_NO_PAUSE,
            "source": f"{_SOURCE_LIVE}: AD_TYPE_CANNOT_BE_PAUSED",
        },
        _IMAGES,
        _HTML5,
    ],
    "asset": [_IMAGES, _HTML5],
    "campaign": [
        {
            "id": "app_campaign.goal_bidding",
            "applies_to": "app_campaign_setting.bidding_strategy_goal_type",
            "rule": "the goal decides the bidding field, whether a target is needed and selective optimization",
            "goals": {
                name: {
                    "kinds": list(g.kinds),
                    "bidding": g.bidding,
                    "target": g.target,
                    "selective_optimization": g.selective_optimization,
                }
                for name, g in _app.GOALS.items()
            },
            "source": _SOURCE_API,
        },
        {
            "id": "app_campaign.immutable",
            "applies_to": "update",
            "rule": "these fields cannot change after create",
            "fields": dict(_app.IMMUTABLE_CAMPAIGN_FIELDS),
            "also": [_app.START_AFTER_START, _app.PRE_REGISTRATION_SELECTIVE],
            "source": _SOURCE_API,
        },
        {
            "id": "app_campaign.pre_registration_android",
            "applies_to": "advertising_channel_sub_type",
            "rule": "APP_CAMPAIGN_FOR_PRE_REGISTRATION needs app_store GOOGLE_APP_STORE",
            "source": _SOURCE_API,
        },
        {
            "id": "app_campaign.budget_not_shared",
            "applies_to": "campaign_budget",
            "rule": "App campaigns need their own budget (explicitly_shared=false)",
            "source": _SOURCE_API,
        },
    ],
    "ad_group_criterion": [
        {
            "id": "app_ad_group.user_list",
            "applies_to": "user_list",
            "rule": "APP_CAMPAIGN_FOR_ENGAGEMENT ad groups need a user list criterion; App criteria are never negative",
            "source": _SOURCE_API,
        }
    ],
}


def rules_for(resource: str) -> list[dict]:
    """The hand-written rules for a resource (snake_case GAQL name); [] when none."""
    return [dict(r) for r in _RULES.get(resource.strip().lower(), [])]
