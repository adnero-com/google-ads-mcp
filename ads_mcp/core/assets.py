"""Asset listing and naming (pure): rows from GAQL -> assets with ratio class and where used."""

from collections.abc import Iterable, Mapping, Sequence
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ads_mcp.rules.app_creative import ratio_class

ASSET_TYPES = ("IMAGE", "YOUTUBE_VIDEO", "MEDIA_BUNDLE")
NAME_MAX = 120

LIST_FIELDS = (
    "asset.resource_name",
    "asset.id",
    "asset.name",
    "asset.type",
    "asset.image_asset.full_size.width_pixels",
    "asset.image_asset.full_size.height_pixels",
    "asset.image_asset.file_size",
    "asset.image_asset.mime_type",
    "asset.youtube_video_asset.youtube_video_id",
    "asset.youtube_video_asset.youtube_video_title",
)


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)
    where: str = Field(
        description="ad (in an ad's asset list), campaign or ad_group."
    )
    campaign_id: str = ""
    ad_group_id: str = ""
    ad_id: str = ""
    field_type: str = ""


class AssetOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    resource_name: str
    name: str = ""
    type: str
    width: int | None = None
    height: int | None = None
    ratio: str | None = Field(
        default=None,
        description="1:1, 1.91:1 or 4:5 when the image fits an App slot; None when it fits none.",
    )
    file_size: int | None = None
    mime_type: str | None = None
    youtube_video_id: str | None = None
    title: str | None = None
    used_in: tuple[Usage, ...] = ()


def list_query(asset_type: str | None) -> str:
    types = (asset_type,) if asset_type else ASSET_TYPES
    quoted = ",".join(f"'{t}'" for t in types)
    return (
        f"SELECT {', '.join(LIST_FIELDS)} FROM asset WHERE asset.type IN ({quoted}) "
        "ORDER BY asset.id DESC"
    )


def usage_queries(resource_names: Sequence[str]) -> tuple[str, ...]:
    """Where these assets are used: in ads (App, RSA...), and linked to campaigns or ad groups."""
    if not resource_names:
        return ()
    names = ",".join(f"'{n}'" for n in resource_names)
    return (
        "SELECT ad_group_ad_asset_view.asset, ad_group_ad_asset_view.field_type, campaign.id, ad_group.id, "
        f"ad_group_ad.ad.id FROM ad_group_ad_asset_view WHERE ad_group_ad_asset_view.asset IN ({names}) "
        "AND ad_group_ad.status != 'REMOVED'",
        "SELECT campaign_asset.asset, campaign_asset.field_type, campaign.id FROM campaign_asset "
        f"WHERE campaign_asset.asset IN ({names}) AND campaign_asset.status != 'REMOVED'",
        "SELECT ad_group_asset.asset, ad_group_asset.field_type, campaign.id, ad_group.id FROM ad_group_asset "
        f"WHERE ad_group_asset.asset IN ({names}) AND ad_group_asset.status != 'REMOVED'",
    )


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def usage_of(row: Mapping[str, Any]) -> tuple[str, Usage] | None:
    """(asset resource name, Usage) from one row of any usage query."""
    for view, where in (
        ("ad_group_ad_asset_view", "ad"),
        ("campaign_asset", "campaign"),
        ("ad_group_asset", "ad_group"),
    ):
        asset = row.get(f"{view}.asset")
        if asset:
            return str(asset), Usage(
                where=where,
                campaign_id=str(row.get("campaign.id", "") or ""),
                ad_group_id=str(row.get("ad_group.id", "") or ""),
                ad_id=str(row.get("ad_group_ad.ad.id", "") or ""),
                field_type=str(row.get(f"{view}.field_type", "") or ""),
            )
    return None


def assets_of(
    rows: Iterable[Mapping[str, Any]],
    usages: Iterable[Mapping[str, Any]],
    unused_only: bool,
) -> tuple[AssetOut, ...]:
    found = [u for u in (usage_of(r) for r in usages) if u]
    by_asset = {
        name: tuple(u for n, u in found if n == name) for name, _ in found
    }

    def one(r: Mapping[str, Any]) -> AssetOut:
        width = _int(r.get("asset.image_asset.full_size.width_pixels"))
        height = _int(r.get("asset.image_asset.full_size.height_pixels"))
        kind = str(r.get("asset.type", ""))
        name = str(r.get("asset.resource_name", ""))
        return AssetOut(
            id=str(r.get("asset.id", "")),
            resource_name=name,
            name=str(r.get("asset.name", "") or ""),
            type=kind,
            width=width,
            height=height,
            ratio=(
                ratio_class(width, height)
                if kind == "IMAGE" and width and height
                else None
            ),
            file_size=_int(r.get("asset.image_asset.file_size")),
            mime_type=(
                (r.get("asset.image_asset.mime_type") or None)
                if kind == "IMAGE"
                else None
            ),
            youtube_video_id=r.get("asset.youtube_video_asset.youtube_video_id")
            or None,
            title=r.get("asset.youtube_video_asset.youtube_video_title")
            or None,
            used_in=by_asset.get(name, ()),
        )

    every = tuple(one(r) for r in rows)
    return tuple(a for a in every if not a.used_in) if unused_only else every


def unique_name(wanted: str | None, fallback: str, sha256_hex: str) -> str:
    """Google requires unique image / media bundle names: the content hash makes them unique."""
    base = re.sub(r"\s+", " ", (wanted or fallback or "asset").strip())[
        : NAME_MAX - 10
    ]
    suffix = sha256_hex[:8]
    return base if base.endswith(suffix) else f"{base} {suffix}"


def name_from_url(url: str) -> str:
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", tail) or "asset"


def fits(ratio: str | None) -> tuple[str, ...]:
    """The App ad slots an image of this ratio fits."""
    return (
        (
            "app_ad.images",
            "app_engagement_ad.images",
            "app_pre_registration_ad.images",
        )
        if ratio
        else ()
    )
