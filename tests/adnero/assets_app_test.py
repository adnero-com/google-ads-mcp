"""assets_* and app_check tools against a fake Google: checks before Google, operations built, facts read."""

import base64
import struct
import zlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp.core.result import Ok
from ads_mcp.safe_fetch import Fetched
from ads_mcp.tools import adnero_app as app
from ads_mcp.tools import adnero_assets as assets
from ads_mcp.tools import adnero_assets_list as assets_list
from ads_mcp.tools.search import SearchResult


def png(w, h):
    chunk = b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
        + b"\0" * 64
    )


@pytest.fixture(scope="module")
def gads():
    return GoogleAdsClient(
        credentials=AnonymousCredentials(), use_proto_plus=True, version="v26"
    )


@pytest.fixture
def google(gads):
    requests = []

    def record(request):
        requests.append(
            (request.validate_only, list(request.mutate_operations))
        )
        return SimpleNamespace(mutate_operation_responses=[])

    service = MagicMock()
    service.mutate.side_effect = record
    with (
        patch("ads_mcp.adnero_mutate.client", return_value=gads),
        patch(
            "ads_mcp.utils.get_googleads_service_from_client",
            return_value=service,
        ),
    ):
        yield requests


def fetched(data):
    return patch(
        "ads_mcp.safe_fetch.fetch", return_value=Ok(Fetched("u", "u", data, ""))
    )


def test_image_preview_then_apply(google):
    with fetched(png(1200, 628)):
        p = assets.upload_image(
            customer_id="1", url="https://cdn.example.com/hero.png"
        )
        assert p.status == "PROPOSED" and google[-1][0] is True
        a = assets.upload_image(
            customer_id="1",
            url="https://cdn.example.com/hero.png",
            validate_only=False,
            checksum=p.checksum,
        )
    assert a.status == "APPLIED" and google[-1][0] is False
    created = google[-1][1][0].asset_operation.create
    assert created.type_.name == "IMAGE"
    assert (
        created.name.startswith("hero ")
        and len(created.name) == len("hero ") + 8
    )
    assert created.image_asset.mime_type.name == "IMAGE_PNG"
    fields = {c.field: c.value for c in p.change_items}
    assert (
        fields["Ratio"] == "1.91:1"
        and "app_ad.images" in fields["Fits App slots"]
    )


def test_changed_bytes_between_preview_and_apply_are_refused(google):
    with fetched(png(1200, 628)):
        p = assets.upload_image(
            customer_id="1", url="https://cdn.example.com/hero.png", name="Hero"
        )
    with fetched(png(1200, 1200)), pytest.raises(ToolError):
        assets.upload_image(
            customer_id="1",
            url="https://cdn.example.com/hero.png",
            name="Hero",
            validate_only=False,
            checksum=p.checksum,
        )
    assert len(google) == 1, "only the preview reached Google"


@pytest.mark.parametrize(
    "data, error",
    [
        (png(2048, 2002), "ASPECT_RATIO_NOT_ALLOWED"),
        (png(100, 100), "IMAGE_TOO_SMALL"),
        (b"GIF89a" + b"\0" * 50, "Not a PNG or JPEG"),
    ],
)
def test_bad_images_never_reach_google(google, data, error):
    with fetched(data), pytest.raises(ToolError, match=error):
        assets.upload_image(
            customer_id="1", url="https://cdn.example.com/x.png"
        )
    assert google == []


def test_image_from_base64_and_input_rules(google):
    b64 = base64.b64encode(png(1200, 1200)).decode()
    assert (
        assets.upload_image(customer_id="1", data_base64=b64, name="Sq").status
        == "PROPOSED"
    )
    for kwargs in (
        {},
        {"url": "https://x/a.png", "data_base64": b64},
        {"data_base64": "!!"},
    ):
        with pytest.raises(ToolError):
            assets.upload_image(customer_id="1", **kwargs)


def test_html5_refused_and_accepted(google):
    import io
    import zipfile

    def z(page):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as f:
            f.writestr("index.html", page)
        return base64.b64encode(buf.getvalue()).decode()

    with pytest.raises(ToolError, match="HTML5_NO_ORIENTATION"):
        assets.upload_html5(customer_id="1", data_base64=z(b"<html></html>"))
    ok = assets.upload_html5(
        customer_id="1",
        data_base64=z(b'<meta name="ad.orientation" content="portrait">'),
        name="Playable",
    )
    assert ok.status == "PROPOSED"
    assert google[-1][1][0].asset_operation.create.type_.name == "MEDIA_BUNDLE"


@pytest.mark.parametrize(
    "video", ["dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"]
)
def test_youtube(google, video):
    assets.add_youtube(customer_id="1", video=video)
    created = google[-1][1][0].asset_operation.create
    assert created.youtube_video_asset.youtube_video_id == "dQw4w9WgXcQ"
    assert created.type_.name == "YOUTUBE_VIDEO"


def test_youtube_refuses_other_urls(google):
    with pytest.raises(ToolError):
        assets.add_youtube(customer_id="1", video="https://vimeo.com/1")


def page(rows, token=None):
    return SearchResult(
        rows=rows,
        row_count=len(rows),
        next_page_token=token,
        stopped_by="END",
        query="q",
    )


def test_assets_list_reads_usage_for_the_page():
    calls = []

    def fake(cid, query, max_rows, token, login):
        calls.append(query)
        if query.startswith("SELECT asset."):
            return page(
                [
                    {
                        "asset.resource_name": "customers/1/assets/1",
                        "asset.id": 1,
                        "asset.type": "IMAGE",
                        "asset.image_asset.full_size.width_pixels": 1200,
                        "asset.image_asset.full_size.height_pixels": 1500,
                    },
                    {
                        "asset.resource_name": "customers/1/assets/2",
                        "asset.id": 2,
                        "asset.type": "IMAGE",
                        "asset.image_asset.full_size.width_pixels": 1080,
                        "asset.image_asset.full_size.height_pixels": 1920,
                    },
                ],
                "NEXT",
            )
        if "ad_group_ad_asset_view" in query:
            return page(
                [
                    {
                        "ad_group_ad_asset_view.asset": "customers/1/assets/1",
                        "ad_group_ad.ad.id": 9,
                    }
                ]
            )
        return page([])

    with patch("ads_mcp.tools.search.search_page", side_effect=fake):
        out = assets_list.list_assets(customer_id="1", unused=True)
    assert [a.id for a in out.assets] == ["2"] and out.assets[0].ratio is None
    assert out.next_page_token == "NEXT"
    assert len(calls) == 4


def test_app_check_reads_facts_and_reports():
    def fake(cid, query, max_rows, token, login):
        if "FROM conversion_action" in query:
            return page(
                [
                    {
                        "conversion_action.id": 2,
                        "conversion_action.name": "purchase",
                        "conversion_action.status": "ENABLED",
                        "conversion_action.type": "FIREBASE_ANDROID_IN_APP_PURCHASE",
                        "conversion_action.category": "PURCHASE",
                        "conversion_action.app_id": "com.example.app",
                    }
                ]
            )
        if "FROM asset" in query:
            assert "asset.id IN (10,11)" in query
            return page(
                [
                    {
                        "asset.id": 10,
                        "asset.type": "IMAGE",
                        "asset.image_asset.full_size.width_pixels": 1200,
                        "asset.image_asset.full_size.height_pixels": 628,
                        "asset.image_asset.file_size": 9,
                        "asset.image_asset.mime_type": "IMAGE_PNG",
                    }
                ]
            )
        raise AssertionError(query)

    with patch("ads_mcp.tools.search.search_page", side_effect=fake):
        report = app.check(
            customer_id="1",
            kind="install",
            app_id="com.example.app",
            store="GOOGLE_APP_STORE",
            goal="optimize_in_app_conversions_target_conversion_cost",
            target_cpa_micros=5_000_000,
            conversion_action_ids=["2"],
            geo_target_ids=["2840"],
            headlines=["Play now", "Win big"],
            descriptions=["Download today."],
            image_asset_ids=["10", "11"],
        )
    assert not report.ok
    assert [p.code for p in report.problems] == ["ASSET_NOT_FOUND"]
    assert report.bidding == "target_cpa"


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "video"},
        {"store": "STEAM"},
        {"image_asset_ids": ["x1"]},
        {"target_cpa_micros": -1},
    ],
)
def test_app_check_input_errors(bad):
    args = dict(
        customer_id="1",
        kind="install",
        app_id="com.a.b",
        store="GOOGLE_APP_STORE",
        goal="OPTIMIZE_INSTALLS_WITHOUT_TARGET_INSTALL_COST",
    )
    with pytest.raises(ToolError):
        app.check(**{**args, **bad})
