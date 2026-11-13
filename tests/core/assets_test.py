"""core/assets.py: asset rows -> ratio class, usage, unused filter, unique names (pure)."""

import pytest

from ads_mcp.core import assets as A


def asset(i, kind="IMAGE", w=1200, h=628):
    return {
        "asset.resource_name": f"customers/1/assets/{i}",
        "asset.id": i,
        "asset.name": f"a{i}",
        "asset.type": kind,
        "asset.image_asset.full_size.width_pixels": w,
        "asset.image_asset.full_size.height_pixels": h,
        "asset.image_asset.file_size": "1000",
        "asset.image_asset.mime_type": "IMAGE_PNG",
    }


USAGES = [
    {
        "ad_group_ad_asset_view.asset": "customers/1/assets/1",
        "ad_group_ad_asset_view.field_type": "MARKETING_IMAGE",
        "campaign.id": 7,
        "ad_group.id": 8,
        "ad_group_ad.ad.id": 9,
    },
    {
        "campaign_asset.asset": "customers/1/assets/1",
        "campaign_asset.field_type": "SITELINK",
        "campaign.id": 7,
    },
    {
        "ad_group_asset.asset": "customers/1/assets/3",
        "ad_group_asset.field_type": "CALLOUT",
        "campaign.id": 7,
        "ad_group.id": 8,
    },
    {"something": "else"},
]


def test_assets_of():
    out = A.assets_of(
        [asset(1), asset(2, w=2048, h=2002), asset(3)],
        USAGES,
        unused_only=False,
    )
    assert [(a.id, a.ratio, len(a.used_in)) for a in out] == [
        ("1", "1.91:1", 2),
        ("2", None, 0),
        ("3", "1.91:1", 1),
    ]
    assert out[0].used_in[0].where == "ad" and out[0].used_in[0].ad_id == "9"
    assert out[0].file_size == 1000


def test_unused_only():
    out = A.assets_of([asset(1), asset(2), asset(3)], USAGES, unused_only=True)
    assert [a.id for a in out] == ["2"]


def test_video_has_no_ratio():
    row = {
        **asset(5, kind="YOUTUBE_VIDEO"),
        "asset.youtube_video_asset.youtube_video_id": "dQw4w9WgXcQ",
    }
    (a,) = A.assets_of([row], [], False)
    assert (a.ratio, a.youtube_video_id, a.mime_type) == (
        None,
        "dQw4w9WgXcQ",
        None,
    )


@pytest.mark.parametrize(
    "asset_type, has",
    [
        (None, "'IMAGE','YOUTUBE_VIDEO','MEDIA_BUNDLE'"),
        ("IMAGE", "IN ('IMAGE')"),
    ],
)
def test_list_query(asset_type, has):
    assert has in A.list_query(asset_type)


def test_usage_queries():
    assert A.usage_queries([]) == ()
    qs = A.usage_queries(["customers/1/assets/1"])
    assert len(qs) == 3 and all("'customers/1/assets/1'" in q for q in qs)


@pytest.mark.parametrize(
    "wanted, fallback, expected",
    [
        ("Hero", "x", "Hero abcdef12"),
        (None, "banner", "banner abcdef12"),
        ("Hero abcdef12", "x", "Hero abcdef12"),
        ("  many   spaces ", "x", "many spaces abcdef12"),
        (None, "", "asset abcdef12"),
    ],
)
def test_unique_name(wanted, fallback, expected):
    assert A.unique_name(wanted, fallback, "abcdef1234567890") == expected


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://x.com/a/hero-1200.png?v=2", "hero-1200"),
        ("https://x.com/", "x"),
        ("", "asset"),
    ],
)
def test_name_from_url(url, name):
    assert A.name_from_url(url) == name
