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

"""App-ready assets: image / HTML5 upload from a URL, YouTube videos, and the asset list (edge).

Every upload is checked against the App rules (rules/app.py) BEFORE Google sees it, then goes
through the usual preview -> apply. The bytes are fetched again on apply; the checksum binds
their sha256, so a file that changed in between is refused.
"""

import base64
import binascii
import hashlib

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import adnero_mutate as m
from ads_mcp import propose_and_confirm as pac
from ads_mcp import safe_fetch
from ads_mcp.api_version import API_VERSION
from ads_mcp.core import assets as A
from ads_mcp.core import media
from ads_mcp.core.result import Err
from ads_mcp.rules import app as R
from ads_mcp.rules import app_creative as C
from ads_mcp.tools import assets_mcp

_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def _value(result):
    if isinstance(result, Err):
        raise ToolError(f"Error: {result.error}")
    return result.value


def _problems(problems) -> None:
    errors = [p for p in problems if p.severity == "ERROR"]
    if errors:
        lines = "\n".join(
            f"- {p.code} ({p.field}): {p.message}"
            + (f" Fix: {p.fix}" if p.fix else "")
            for p in errors
        )
        raise ToolError(f"Error: refused before Google (App rules):\n{lines}")


def _bytes(url: str | None, data_base64: str | None, cap: int) -> bytes:
    if bool(url) == bool(data_base64):
        raise ToolError(
            "Error: pass exactly one of 'url' (https) or 'data_base64'."
        )
    if url:
        return _value(safe_fetch.fetch(url, cap)).data
    try:
        data = base64.b64decode(data_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise ToolError("Error: 'data_base64' is not valid base64.")
    if len(data) > cap:
        raise ToolError(
            f"Error: the file is {len(data)} bytes; the limit is {cap}."
        )
    return data


def _create_asset(
    cid, asset_name, fill, tool, payload, items, validate_only, checksum, ctx
):
    def operations(client):
        op = client.get_type("MutateOperation", version=API_VERSION)
        asset = op.asset_operation.create
        asset.name = asset_name
        fill(client, asset)
        return [op]

    return m.mutate(
        tool=tool,
        customer_id=cid,
        payload=payload,
        operations=operations,
        change_items=items,
        validate_only=validate_only,
        checksum=checksum,
        ctx=ctx,
    )


@assets_mcp.tool(
    title="Upload an App campaign image (from a URL)", annotations=_WRITE
)
def upload_image(
    customer_id: str | int,
    url: str | None = None,
    data_base64: str | None = None,
    name: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Uploads a JPG/PNG image asset after checking it fits an App campaign image slot.

    The image is fetched from a public https URL (private and loopback hosts are refused, 5 MB cap) or
    taken from base64. It must be 1:1 (min 200x200), 1.91:1 (min 600x314) or 4:5 (min 320x400) within 0.5%,
    else it is refused before Google sees it. The asset name gets the content hash appended (Google needs
    unique names). Costs 1 Google Ads operation on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same
    arguments, validate_only=false and the plan_id.

    Args:
        customer_id: The Google Ads account.
        url: https URL of the image.
        data_base64: The image bytes as base64 (instead of url).
        name: Asset name (default: from the URL); the content hash is appended.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    data = _bytes(url, data_base64, R.IMAGE_MAX_BYTES)
    info = _value(media.sniff_image(data))
    _problems(
        C.check_image("image", info.width, info.height, len(data), info.mime)
    )
    sha = hashlib.sha256(data).hexdigest()
    ratio = C.ratio_class(info.width, info.height)
    asset_name = A.unique_name(name, A.name_from_url(url or ""), sha)

    def fill(client, asset):
        asset.type_ = client.enums.AssetTypeEnum.IMAGE
        asset.image_asset.data = data
        asset.image_asset.file_size = len(data)
        asset.image_asset.mime_type = getattr(
            client.enums.MimeTypeEnum, info.mime
        )

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Asset (IMAGE)",
        primary_key_readable=asset_name,
        fields={
            "Size": f"{info.width}x{info.height}",
            "Ratio": ratio,
            "Bytes": len(data),
            "Type": info.mime,
            "Fits App slots": ", ".join(A.fits(ratio)),
            "sha256": sha,
        },
    )
    return _create_asset(
        cid,
        asset_name,
        fill,
        "assets_upload_image",
        {"name": asset_name, "sha256": sha},
        items,
        validate_only,
        plan_id or checksum,
        ctx,
    )


@assets_mcp.tool(
    title="Upload an HTML5 ad (zip, from a URL)", annotations=_WRITE
)
def upload_html5(
    customer_id: str | int,
    url: str | None = None,
    data_base64: str | None = None,
    name: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Uploads an HTML5 zip (MEDIA_BUNDLE asset) for App install ads, after checking it.

    Checked before Google: at most 5 MB and 512 files, an .html primary entry, the orientation meta
    (<meta name="ad.orientation" content="portrait|landscape">) and https-only URLs. Only App install ads
    (app_ad.html5_media_bundles) take HTML5. Costs 1 Google Ads operation on each call.

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same
    arguments, validate_only=false and the plan_id.

    Args:
        customer_id: The Google Ads account.
        url: https URL of the .zip.
        data_base64: The zip as base64 (instead of url).
        name: Asset name (default: from the URL); the content hash is appended.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    data = _bytes(url, data_base64, R.HTML5_MAX_BYTES)
    info, problems = media.inspect_html5(data)
    _problems(problems)
    sha = hashlib.sha256(data).hexdigest()
    asset_name = A.unique_name(name, A.name_from_url(url or ""), sha)

    def fill(client, asset):
        asset.type_ = client.enums.AssetTypeEnum.MEDIA_BUNDLE
        asset.media_bundle_asset.data = data

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Asset (HTML5 MEDIA_BUNDLE)",
        primary_key_readable=asset_name,
        fields={
            "Files": info.files if info else None,
            "Primary entry": info.primary if info else None,
            "Orientation": info.orientation if info else None,
            "Bytes": len(data),
            "Fits App slots": "app_ad.html5_media_bundles",
            "sha256": sha,
        },
    )
    return _create_asset(
        cid,
        asset_name,
        fill,
        "assets_upload_html5",
        {"name": asset_name, "sha256": sha},
        items,
        validate_only,
        plan_id or checksum,
        ctx,
    )


@assets_mcp.tool(title="Add a YouTube video as an asset", annotations=_WRITE)
def add_youtube(
    customer_id: str | int,
    video: str,
    name: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
    plan_id: str | None = None,
    login_customer_id: str | int | None = None,
    ctx: Context | None = None,
) -> m.MutationResult:
    """Adds a YouTube video (already on YouTube) as a YOUTUBE_VIDEO asset for App ads (up to 20 per ad).

    Costs 1 Google Ads operation on each call (the preview counts too).

    Approval: call with validate_only=true (default): Google validates it and nothing changes (status PROPOSED, a plan_id). Show change_items to the person; once they approve, call again with the same
    arguments, validate_only=false and the plan_id.

    Args:
        customer_id: The Google Ads account.
        video: The 11-character video id, or a youtube.com / youtu.be / shorts URL.
        name: Optional asset name.
        validate_only: True (default) previews; False applies (needs `plan_id`).
        plan_id: From the preview (signed by the server, valid 15 minutes).
        checksum: Deprecated alias of plan_id.
        login_customer_id: The manager (MCC) account to call through.
    """
    m.use_login(login_customer_id)
    cid = m.customer_id_of(customer_id)
    video_id = _value(media.youtube_id(video))
    asset_name = (name or f"YouTube {video_id}").strip()[: A.NAME_MAX]

    def fill(client, asset):
        asset.type_ = client.enums.AssetTypeEnum.YOUTUBE_VIDEO
        asset.youtube_video_asset.youtube_video_id = video_id

    items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Asset (YOUTUBE_VIDEO)",
        primary_key_readable=asset_name,
        fields={"YouTube video id": video_id},
    )
    return _create_asset(
        cid,
        asset_name,
        fill,
        "assets_add_youtube",
        {"name": asset_name, "video_id": video_id},
        items,
        validate_only,
        plan_id or checksum,
        ctx,
    )
