"""App campaign rules no machine-readable source holds: data + pure checks (DESIGN.md principle 1).

Sources: Google Ads Help Center (text/image/video/HTML5 limits), the API's App campaign docs (goal ->
bidding table, ad types per sub-type, immutables) and our own live failures (AD_TYPE_CANNOT_BE_PAUSED,
ASPECT_RATIO_NOT_ALLOWED). Each check returns problems as values; nothing raises.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

Kind = Literal["install", "engagement", "pre_registration"]
Store = Literal["GOOGLE_APP_STORE", "APPLE_APP_STORE"]
Severity = Literal["ERROR", "WARNING"]


class Problem(BaseModel):
    """One thing that would make Google refuse (ERROR) or that deserves a look (WARNING)."""

    model_config = ConfigDict(frozen=True)

    code: str
    field: str
    message: str
    fix: str = ""
    severity: Severity = "ERROR"


@dataclass(frozen=True)
class TextLimit:
    min_count: int
    max_count: int
    max_chars: int


@dataclass(frozen=True)
class ImageRatio:
    name: str
    ratio: float
    min_width: int
    min_height: int


@dataclass(frozen=True)
class Goal:
    """One App bidding goal (AppCampaignBiddingStrategyGoalType) and what it needs."""

    kinds: tuple[Kind, ...]
    bidding: Literal[
        "target_cpa",
        "target_roas",
        "maximize_conversions",
        "maximize_conversion_value",
    ]
    target: Literal["cpa", "roas", "none"]
    selective_optimization: Literal["optional", "required", "not_allowed"]


@dataclass(frozen=True)
class AdShape:
    """The ad info per sub-type: its field on Ad, and its video field name."""

    sub_type: str
    ad_field: str
    video_field: str
    html5: bool


HEADLINES = TextLimit(min_count=2, max_count=5, max_chars=30)
DESCRIPTIONS = TextLimit(min_count=1, max_count=5, max_chars=90)

IMAGE_RATIOS: tuple[ImageRatio, ...] = (
    ImageRatio("1:1", 1.0, 200, 200),
    ImageRatio("1.91:1", 1.91, 600, 314),
    ImageRatio("4:5", 0.8, 320, 400),
)
# The Help Center says 1%, but Google refused 881x1092 (0.84% off 4:5) and 2048x1081 (0.81% off 1.91:1) live
# (ASPECT_RATIO_NOT_ALLOWED, 2026-11-13). 0.5% passes every recommended size and nothing Google refused.
RATIO_TOLERANCE = 0.005
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = ("IMAGE_JPEG", "IMAGE_PNG")
MAX_IMAGES = 20
MAX_VIDEOS = 20
MAX_HTML5 = 20
HTML5_MAX_BYTES = 5 * 1024 * 1024
HTML5_MAX_FILES = 512

AD_SHAPES: dict[Kind, AdShape] = {
    "install": AdShape("APP_CAMPAIGN", "app_ad", "youtube_videos", True),
    "engagement": AdShape(
        "APP_CAMPAIGN_FOR_ENGAGEMENT", "app_engagement_ad", "videos", False
    ),
    "pre_registration": AdShape(
        "APP_CAMPAIGN_FOR_PRE_REGISTRATION",
        "app_pre_registration_ad",
        "youtube_videos",
        False,
    ),
}

GOALS: dict[str, Goal] = {
    "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST": Goal(
        ("install",), "target_cpa", "cpa", "optional"
    ),
    "OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST": Goal(
        ("install",), "maximize_conversions", "none", "optional"
    ),
    "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST": Goal(
        ("install",), "target_cpa", "cpa", "required"
    ),
    "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST": Goal(
        ("install", "engagement"), "target_cpa", "cpa", "required"
    ),
    "OPTIMIZE_IN_APP_CONVERSIONS_WITHOUT_TARGET_CPA": Goal(
        ("install",), "maximize_conversions", "none", "required"
    ),
    "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND": Goal(
        ("install",), "target_roas", "roas", "required"
    ),
    "OPTIMIZE_TOTAL_VALUE_WITHOUT_TARGET_ROAS": Goal(
        ("install",), "maximize_conversion_value", "none", "required"
    ),
    "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME": Goal(
        ("pre_registration",), "target_cpa", "cpa", "not_allowed"
    ),
}

# Campaign fields that cannot change after create (field -> why).
IMMUTABLE_CAMPAIGN_FIELDS: dict[str, str] = {
    "advertising_channel_type": "Immutable on Campaign.",
    "advertising_channel_sub_type": "Immutable on Campaign: install, engagement and pre-registration are separate campaigns.",
    "app_campaign_setting.app_id": "IMMUTABLE on AppCampaignSetting: make a new campaign for another app.",
    "app_campaign_setting.app_store": "IMMUTABLE on AppCampaignSetting: make a new campaign for another store.",
}
START_AFTER_START = "start_date_time cannot change once the campaign has started (CANNOT_MODIFY_START_DATE_IF_ALREADY_STARTED)."
PRE_REGISTRATION_SELECTIVE = "Pre-registration campaigns' conversion actions are set by Google and cannot change."
APP_AD_NO_PAUSE = (
    "App ads cannot be paused (AD_TYPE_CANNOT_BE_PAUSED): they are created ENABLED and serving is controlled by the "
    "ad group or campaign status."
)
RATIO_FIX = "Crop to exactly 1:1, 1.91:1 or 4:5 (e.g. 1200x1200, 1200x628, 1200x1500); 9:16 and 16:9 creative must be a YouTube video."
