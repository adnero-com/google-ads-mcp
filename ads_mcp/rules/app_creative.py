"""App ad creative checks (pure): text counts and lengths, image ratio/size/type, asset counts.

The limits are data in rules/app.py.
"""

from collections.abc import Sequence

from ads_mcp.rules.app import (
    AD_SHAPES,
    DESCRIPTIONS,
    HEADLINES,
    IMAGE_MAX_BYTES,
    IMAGE_MIME_TYPES,
    IMAGE_RATIOS,
    MAX_HTML5,
    MAX_IMAGES,
    MAX_VIDEOS,
    RATIO_FIX,
    RATIO_TOLERANCE,
    Kind,
    Problem,
    TextLimit,
)


def _text_problems(
    field: str, texts: Sequence[str], limit: TextLimit
) -> list[Problem]:
    count = (
        []
        if limit.min_count <= len(texts) <= limit.max_count
        else [
            Problem(
                code="TEXT_COUNT",
                field=field,
                message=f"{len(texts)} {field}; App ads take {limit.min_count} to {limit.max_count}.",
                fix=f"Give {limit.min_count}-{limit.max_count} {field}.",
            )
        ]
    )
    lengths = [
        Problem(
            code="TEXT_TOO_LONG",
            field=f"{field}[{i}]",
            message=f"'{t}' is {len(t)} characters; the limit is {limit.max_chars}.",
            fix=f"Shorten it to {limit.max_chars} characters.",
        )
        for i, t in enumerate(texts)
        if len(t) > limit.max_chars
    ]
    empty = [
        Problem(code="TEXT_EMPTY", field=f"{field}[{i}]", message="Empty text.")
        for i, t in enumerate(texts)
        if not t.strip()
    ]
    dupes = (
        [
            Problem(
                code="TEXT_DUPLICATE",
                field=field,
                message=f"Duplicate {field}.",
                fix="Make each one different.",
            )
        ]
        if len({t.strip().lower() for t in texts}) != len(texts)
        else []
    )
    return count + lengths + empty + dupes


def check_texts(
    headlines: Sequence[str], descriptions: Sequence[str]
) -> list[Problem]:
    return _text_problems("headlines", headlines, HEADLINES) + _text_problems(
        "descriptions", descriptions, DESCRIPTIONS
    )


def ratio_class(width: int, height: int) -> str | None:
    """'1:1' / '1.91:1' / '4:5' when within RATIO_TOLERANCE of one, else None."""
    if width <= 0 or height <= 0:
        return None
    actual = width / height
    hits = [
        r.name
        for r in IMAGE_RATIOS
        if abs(actual - r.ratio) / r.ratio <= RATIO_TOLERANCE
    ]
    return hits[0] if hits else None


def check_image(
    field: str, width: int, height: int, size_bytes: int, mime: str
) -> list[Problem]:
    ratio = ratio_class(width, height)
    spec = next((r for r in IMAGE_RATIOS if r.name == ratio), None)
    shape = (
        [
            Problem(
                code="ASPECT_RATIO_NOT_ALLOWED",
                field=field,
                message=f"{width}x{height} (ratio {width / max(height, 1):.3f}) is not 1:1, 1.91:1 or 4:5.",
                fix=RATIO_FIX,
            )
        ]
        if spec is None
        else (
            [
                Problem(
                    code="IMAGE_TOO_SMALL",
                    field=field,
                    message=f"{width}x{height} is below the {spec.name} minimum {spec.min_width}x{spec.min_height}.",
                    fix="Use a larger image (1200 px wide is recommended).",
                )
            ]
            if width < spec.min_width or height < spec.min_height
            else []
        )
    )
    size = (
        [
            Problem(
                code="IMAGE_TOO_LARGE",
                field=field,
                message=f"{size_bytes} bytes; the limit is 5 MB.",
                fix="Compress it.",
            )
        ]
        if size_bytes > IMAGE_MAX_BYTES
        else []
    )
    kind = (
        []
        if mime in IMAGE_MIME_TYPES
        else [
            Problem(
                code="IMAGE_TYPE",
                field=field,
                message=f"{mime or 'unknown type'}; App images are JPG or PNG.",
            )
        ]
    )
    return shape + size + kind


def check_counts(
    images: int, videos: int, html5: int, kind: Kind
) -> list[Problem]:
    shape = AD_SHAPES[kind]
    limits = [
        ("images", images, MAX_IMAGES),
        (shape.video_field, videos, MAX_VIDEOS),
        ("html5_media_bundles", html5, MAX_HTML5),
    ]
    over = [
        Problem(
            code="TOO_MANY_ASSETS",
            field=f,
            message=f"{n} {f}; at most {cap}.",
        )
        for f, n, cap in limits
        if n > cap
    ]
    html5_kind = (
        [
            Problem(
                code="HTML5_NOT_ALLOWED",
                field="html5_media_bundles",
                message=f"{shape.ad_field} takes no HTML5 bundles (install campaigns only).",
            )
        ]
        if html5 and not shape.html5
        else []
    )
    return over + html5_kind
