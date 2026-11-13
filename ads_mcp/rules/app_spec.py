"""The App campaign spec and the account facts app_check reads (typed, frozen data)."""

from pydantic import BaseModel, ConfigDict, Field

from ads_mcp.rules import app as R


class AppSpec(BaseModel):
    """What an App campaign is made of (the arguments of app_check / app_campaign_create)."""

    model_config = ConfigDict(frozen=True)

    kind: R.Kind = Field(description="install, engagement or pre_registration.")
    app_id: str = Field(
        description="Google Play package name (com.example.app) or App Store numeric id."
    )
    store: R.Store
    goal: str = Field(
        description="An AppCampaignBiddingStrategyGoalType name, e.g. OPTIMIZE_INSTALLS_TARGET_INSTALL_COST."
    )
    target_cpa_micros: int | None = None
    target_roas: float | None = None
    conversion_action_ids: tuple[str, ...] = ()
    geo_target_ids: tuple[str, ...] = ()
    excluded_geo_target_ids: tuple[str, ...] = ()
    language_ids: tuple[str, ...] = ()
    headlines: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()
    image_asset_ids: tuple[str, ...] = ()
    youtube_video_asset_ids: tuple[str, ...] = ()
    html5_asset_ids: tuple[str, ...] = ()
    user_list_ids: tuple[str, ...] = ()
    contains_eu_political_advertising: bool | None = Field(
        default=None,
        description="Required when a targeted location requires the EU declaration (Google says which) or no "
        "location is set; otherwise defaults to false.",
    )


class ConversionActionFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str = ""
    status: str = ""
    type: str = ""
    category: str = ""
    app_id: str = ""


class AssetFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    type: str
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    mime: str = ""


class UserListFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str = ""


class AccountFacts(BaseModel):
    model_config = ConfigDict(frozen=True)
    conversion_actions: tuple[ConversionActionFact, ...] = ()
    assets: tuple[AssetFact, ...] = ()
    user_lists: tuple[UserListFact, ...] = ()
    app_tracked: bool | None = Field(
        default=None,
        description="A conversion action tracks the app; None: unknown (derived from conversion_actions).",
    )
    complete: bool = Field(
        default=True,
        description="Every lookup read to the end. False: missing ids are warnings, never refusals.",
    )
    eu_requiring_geo_ids: tuple[str, ...] | None = Field(
        default=(),
        description="Targeted geo ids whose constant requires the EU declaration; None when the read failed.",
    )


class CheckReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool = Field(description="True when nothing would make Google refuse.")
    problems: tuple[R.Problem, ...]
    campaign_sub_type: str
    ad_field: str
    bidding: str = Field(
        description="The campaign bidding field the goal sets."
    )
