"""core/media.py: magic bytes, sizes, YouTube ids, HTML5 zips (pure, table-driven)."""

import io
import struct
import zipfile
import zlib

import pytest

from ads_mcp.core import media
from ads_mcp.core.result import Err, Ok


def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
    )


def jpeg(w, h):
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00"
        + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


@pytest.mark.parametrize(
    "data, expected",
    [
        (png(1200, 628), media.ImageInfo("IMAGE_PNG", 1200, 628)),
        (jpeg(1200, 1500), media.ImageInfo("IMAGE_JPEG", 1200, 1500)),
        (b"GIF89a....", None),
        (b"<html>", None),
        (b"\x89PNG\r\n\x1a\n", None),
        (b"\xff\xd8\xff\xe0\x00", None),
    ],
)
def test_sniff(data, expected):
    got = media.sniff_image(data)
    assert got == Ok(expected) if expected else isinstance(got, Err)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        (
            "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=3",
            "dQw4w9WgXcQ",
        ),
        ("https://youtu.be/dQw4w9WgXcQ?si=x", "dQw4w9WgXcQ"),
        ("youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://vimeo.com/123", None),
        ("short", None),
        ("https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_youtube_id(value, expected):
    got = media.youtube_id(value)
    assert got == Ok(expected) if expected else isinstance(got, Err)


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


GOOD = b'<html><head><meta name="ad.orientation" content="portrait,landscape"></head><img src="https://x/a.png"></html>'


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"index.html": GOOD, "a.png": b"x"}, []),
        ({"ad/index.html": GOOD}, []),
        (
            {"index.html": GOOD.replace(b"ad.orientation", b"viewport")},
            ["HTML5_NO_ORIENTATION"],
        ),
        ({"a.png": b"x"}, ["MISSING_PRIMARY_MEDIA_BUNDLE_ENTRY"]),
        (
            {"index.html": GOOD.replace(b"https://", b"http://")},
            ["URL_IN_MEDIA_BUNDLE_NOT_SSL_COMPLIANT"],
        ),
        (
            {"index.html": GOOD, **{f"f{i}.js": b"" for i in range(512)}},
            ["HTML5_TOO_MANY_FILES"],
        ),
    ],
)
def test_html5(files, expected):
    info, problems = media.inspect_html5(zip_of(files))
    assert sorted(p.code for p in problems) == expected
    assert info is not None


def test_html5_not_a_zip_and_too_large():
    _, problems = media.inspect_html5(b"x" * (5 * 1024 * 1024 + 1))
    assert sorted(p.code for p in problems) == [
        "HTML5_NOT_A_ZIP",
        "HTML5_TOO_LARGE",
    ]
