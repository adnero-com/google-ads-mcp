"""What uploaded bytes really are (pure): image type and size from magic bytes, HTML5 zip contents,
YouTube ids. Content-Type headers are never trusted.
"""

from dataclasses import dataclass
import io
import re
import struct
import zipfile

from ads_mcp.core.result import Err, Ok, Result
from ads_mcp.rules.app import HTML5_MAX_BYTES, HTML5_MAX_FILES, Problem


@dataclass(frozen=True)
class ImageInfo:
    mime: str  # IMAGE_PNG | IMAGE_JPEG (the MimeType enum names)
    width: int
    height: int


_PNG = b"\x89PNG\r\n\x1a\n"
_SOF = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)


def _png(data: bytes) -> Result[ImageInfo, str]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return Err("A PNG without a readable header.")
    width, height = struct.unpack(">II", data[16:24])
    return Ok(ImageInfo("IMAGE_PNG", width, height))


def _jpeg(data: bytes) -> Result[ImageInfo, str]:
    # Walk the JPEG markers to the first start-of-frame; bounded by len(data).
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return Err("A JPEG with a broken marker.")
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in _SOF:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return Ok(ImageInfo("IMAGE_JPEG", width, height))
        i += 2 + length
    return Err("A JPEG without a frame header.")


def sniff_image(data: bytes) -> Result[ImageInfo, str]:
    """PNG or JPEG with its pixel size, from the bytes alone."""
    if data.startswith(_PNG):
        return _png(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg(data)
    return Err("Not a PNG or JPEG (App campaign images must be JPG or PNG).")


_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_URL = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#/].*)?$"
)


def youtube_id(value: str) -> Result[str, str]:
    """An 11-character YouTube video id from an id or any usual YouTube URL."""
    text = value.strip()
    if _YOUTUBE_ID.match(text):
        return Ok(text)
    match = _YOUTUBE_URL.match(text)
    if match:
        return Ok(match.group(1))
    return Err(
        f"'{value[:100]}' is not a YouTube video id or URL (youtube.com/watch?v=..., youtu.be/..., shorts/...)."
    )


@dataclass(frozen=True)
class Html5Info:
    files: int
    primary: str
    orientation: str


_ORIENTATION = re.compile(
    rb"""<meta\s+name=["']ad\.(?:orientation|size)["']\s+content=["']([^"']+)["']""",
    re.I,
)
_INSECURE = re.compile(rb"""(?:src|href)\s*=\s*["']http://""", re.I)


def inspect_html5(
    data: bytes,
) -> tuple[Html5Info | None, tuple[Problem, ...]]:
    """A zip's App HTML5 checks: <=5 MB, <=512 files, an .html primary entry, orientation meta, SSL only."""
    size = (
        (
            Problem(
                code="HTML5_TOO_LARGE",
                field="zip",
                message=f"{len(data)} bytes; the limit is 5 MB.",
            ),
        )
        if len(data) > HTML5_MAX_BYTES
        else ()
    )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = tuple(n for n in archive.namelist() if not n.endswith("/"))
        htmls = tuple(n for n in names if n.lower().endswith((".html", ".htm")))
        primary = next(
            (n for n in htmls if n.rsplit("/", 1)[-1].lower() == "index.html"),
            htmls[0] if htmls else "",
        )
        # Bounded read: the primary entry only, and at most the size cap.
        page = archive.open(primary).read(HTML5_MAX_BYTES) if primary else b""
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return None, size + (
            Problem(
                code="HTML5_NOT_A_ZIP",
                field="zip",
                message="Not a readable .zip file.",
            ),
        )
    count = (
        (
            Problem(
                code="HTML5_TOO_MANY_FILES",
                field="zip",
                message=f"{len(names)} files; the limit is {HTML5_MAX_FILES}.",
            ),
        )
        if len(names) > HTML5_MAX_FILES
        else ()
    )
    no_primary = (
        (
            Problem(
                code="MISSING_PRIMARY_MEDIA_BUNDLE_ENTRY",
                field="zip",
                message="No .html file in the zip.",
                fix="Put index.html at the zip's root.",
            ),
        )
        if not primary
        else ()
    )
    found = _ORIENTATION.search(page)
    orientation = found.group(1).decode("ascii", "replace") if found else ""
    no_meta = (
        (
            Problem(
                code="HTML5_NO_ORIENTATION",
                field=primary or "zip",
                message='No <meta name="ad.orientation" content="portrait|landscape"> (or ad.size).',
                fix='Add <meta name="ad.orientation" content="portrait,landscape"> to the <head>.',
            ),
        )
        if primary and not orientation
        else ()
    )
    insecure = (
        (
            Problem(
                code="URL_IN_MEDIA_BUNDLE_NOT_SSL_COMPLIANT",
                field=primary,
                message="The page loads http:// URLs; every URL must be https.",
            ),
        )
        if _INSECURE.search(page)
        else ()
    )
    return (
        Html5Info(len(names), primary, orientation),
        size + count + no_primary + no_meta + insecure,
    )
