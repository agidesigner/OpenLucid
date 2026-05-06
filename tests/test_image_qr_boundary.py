"""Pin the QR / asset boundary checks in the image-gen flow.

The reviewer flagged that the original ``startswith(f'{offer_id}/')``
guard was bypassable via path traversal — e.g. ``offer_id/../other_offer/file``
would startswith() correctly but normpath collapses to ``other_offer/file``.

The hardened ``_safe_offer_subpath`` uses ``os.path.normpath`` and asserts
the FIRST path segment is the offer's id. These tests pin that contract
so a future "simpler" rewrite can't quietly re-introduce the bug.
"""
from __future__ import annotations

import uuid


_OFFER = uuid.UUID("3d82ad15-a36e-4ef7-8caa-826f33b0b387")
_OTHER_OFFER = uuid.UUID("00000000-0000-4000-8000-000000000000")


def test_safe_offer_subpath_accepts_legitimate_paths():
    from app.application.image_service import _safe_offer_subpath

    ok = _safe_offer_subpath(f"{_OFFER}/abc_qr.png", _OFFER)
    assert ok == f"{_OFFER}/abc_qr.png"

    # Nested subdir is fine as long as the first segment is the offer.
    ok2 = _safe_offer_subpath(f"{_OFFER}/sub/qr.png", _OFFER)
    assert ok2 == f"{_OFFER}/sub/qr.png"


def test_safe_offer_subpath_rejects_dot_dot_traversal():
    """The original bug class: ``offer_id/../other_offer/x`` slipped past
    a startswith guard but resolves outside the offer's subdirectory."""
    from app.application.image_service import _safe_offer_subpath

    poison = f"{_OFFER}/../{_OTHER_OFFER}/qr.png"
    assert _safe_offer_subpath(poison, _OFFER) is None


def test_safe_offer_subpath_rejects_other_offer_prefix():
    from app.application.image_service import _safe_offer_subpath

    other = f"{_OTHER_OFFER}/qr.png"
    assert _safe_offer_subpath(other, _OFFER) is None


def test_safe_offer_subpath_rejects_absolute_paths_outside_offer():
    """Absolute paths outside the offer's subdir are rejected outright."""
    from app.application.image_service import _safe_offer_subpath

    assert _safe_offer_subpath("/etc/passwd", _OFFER) is None
    assert _safe_offer_subpath(f"/{_OTHER_OFFER}/qr.png", _OFFER) is None


def test_safe_offer_subpath_strips_leading_slash_for_offer_owned_paths():
    """Product behavior: ``/<offer_id>/...`` is treated as a relative
    storage path (leading slash stripped before normalization). This
    keeps callers who accidentally include a leading slash from being
    rejected, as long as the FIRST segment is still the offer's id."""
    from app.application.image_service import _safe_offer_subpath

    assert _safe_offer_subpath(f"/{_OFFER}/qr.png", _OFFER) == f"{_OFFER}/qr.png"


def test_safe_offer_subpath_rejects_empty_or_dot_dot_only():
    from app.application.image_service import _safe_offer_subpath

    assert _safe_offer_subpath("", _OFFER) is None
    assert _safe_offer_subpath("..", _OFFER) is None
    assert _safe_offer_subpath("../etc/passwd", _OFFER) is None


def test_safe_offer_subpath_handles_backslash_normalization():
    """Defense-in-depth: a Windows-style path doesn't bypass the check."""
    from app.application.image_service import _safe_offer_subpath

    # Backslash-style separator should normalize to forward slashes.
    assert _safe_offer_subpath(f"{_OFFER}\\sub\\qr.png", _OFFER) == f"{_OFFER}/sub/qr.png"
    # Mixed separator path-traversal — should still reject.
    poison = f"{_OFFER}\\..\\{_OTHER_OFFER}\\qr.png"
    assert _safe_offer_subpath(poison, _OFFER) is None


def test_safe_reference_upload_subpath_accepts_one_off_offer_uploads():
    from app.application.image_service import _safe_reference_upload_subpath

    uri = f"tmp/image_refs/{_OFFER}/abc_screenshot.png"
    assert _safe_reference_upload_subpath(uri, _OFFER) == uri


def test_safe_reference_upload_subpath_rejects_other_offer_or_traversal():
    from app.application.image_service import _safe_reference_upload_subpath

    other = f"tmp/image_refs/{_OTHER_OFFER}/abc.png"
    assert _safe_reference_upload_subpath(other, _OFFER) is None

    poison = f"tmp/image_refs/{_OFFER}/../{_OTHER_OFFER}/abc.png"
    assert _safe_reference_upload_subpath(poison, _OFFER) is None

    assert _safe_reference_upload_subpath(f"{_OFFER}/abc.png", _OFFER) is None


# ── Filename sanitization (save_reference_upload) ──


def test_safe_upload_filename_strips_path_components():
    """Adversarial filenames must collapse to a basename."""
    from app.application.image_service import _safe_upload_filename

    assert _safe_upload_filename("../../etc/passwd") == "passwd"
    assert _safe_upload_filename("..\\..\\Windows\\system32\\cmd.exe") == "cmd.exe"
    assert _safe_upload_filename("/var/log/syslog") == "syslog"
    assert _safe_upload_filename("nested/dirs/file.png") == "file.png"


def test_safe_upload_filename_preserves_cjk():
    """CJK and other Unicode letters must round-trip — only path
    separators and control chars are stripped."""
    from app.application.image_service import _safe_upload_filename

    assert _safe_upload_filename("测试截图.png") == "测试截图.png"
    assert _safe_upload_filename("产品图_v2.jpg") == "产品图_v2.jpg"
    # Whitespace collapses to a single underscore.
    assert _safe_upload_filename("活动 海报 终稿.png") == "活动_海报_终稿.png"


def test_safe_upload_filename_neutralizes_control_chars():
    """NUL, ASCII control chars, and DEL are dangerous on most
    filesystems and must be replaced regardless of language."""
    from app.application.image_service import _safe_upload_filename

    assert _safe_upload_filename("hello\x00world.png") == "hello_world.png"
    assert _safe_upload_filename("ab\x07cd.png") == "ab_cd.png"
    assert _safe_upload_filename("trailing\x7f.png") == "trailing_.png"


def test_safe_upload_filename_falls_back_for_empty_or_dotonly():
    """When sanitization leaves nothing useful, default to 'reference'
    rather than letting a zero-length / dot-only filename hit storage."""
    from app.application.image_service import _safe_upload_filename

    assert _safe_upload_filename("") == "reference"
    assert _safe_upload_filename("   ") == "reference"
    assert _safe_upload_filename("...") == "reference"
    assert _safe_upload_filename("/") == "reference"
    assert _safe_upload_filename(None) == "reference"  # type: ignore[arg-type]


def test_safe_upload_filename_caps_length():
    """Long filenames are truncated so a malicious caller can't fill the
    filesystem inode table with multi-kilobyte names."""
    from app.application.image_service import _safe_upload_filename

    long = "a" * 500 + ".png"
    out = _safe_upload_filename(long)
    assert len(out) <= 120


# ── Image format / magic-byte detection ─────────────────────────


def test_detect_image_format_recognizes_png_jpeg_gif_webp():
    from app.application.image_service import _detect_image_format

    assert _detect_image_format(b"\x89PNG\r\n\x1a\nfoo") == "png"
    assert _detect_image_format(b"\xff\xd8\xff\xe0bar") == "jpeg"
    assert _detect_image_format(b"GIF87abaz") == "gif"
    assert _detect_image_format(b"GIF89aqux") == "gif"
    assert _detect_image_format(b"RIFF\x00\x00\x00\x00WEBPxxx") == "webp"


def test_detect_image_format_rejects_non_image_bytes():
    """Magic-byte sniff must return None for HTML / text / random bytes
    so the upload validator doesn't trust an extension-based label."""
    from app.application.image_service import _detect_image_format

    assert _detect_image_format(b"") is None
    assert _detect_image_format(b"<html>") is None
    assert _detect_image_format(b"plain text") is None
    # Bytes that LOOK like RIFF but aren't WEBP should also reject.
    assert _detect_image_format(b"RIFF\x00\x00\x00\x00WAVExxx") is None


# ── save_reference_upload validation gates ───────────────────────


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_save_reference_upload_rejects_empty_bytes():
    from app.application.image_service import save_reference_upload
    from app.exceptions import AppError
    import pytest

    with pytest.raises(AppError) as exc:
        _run(save_reference_upload(
            file_content=b"",
            file_name="x.png",
            offer_id=_OFFER,
        ))
    assert exc.value.code == "EMPTY_REFERENCE_UPLOAD"


def test_save_reference_upload_rejects_oversize_bytes():
    """11MB upload must be rejected before any disk write.

    The cap protects against malicious or accidental large uploads
    that would chew memory + disk + provider bandwidth."""
    from app.application.image_service import save_reference_upload
    from app.exceptions import AppError
    import pytest

    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024)
    with pytest.raises(AppError) as exc:
        _run(save_reference_upload(
            file_content=big,
            file_name="huge.png",
            offer_id=_OFFER,
        ))
    assert exc.value.code == "REFERENCE_UPLOAD_TOO_LARGE"
    assert exc.value.status_code == 413


def test_save_reference_upload_rejects_non_image_bytes():
    """Magic-byte sniff must reject HTML / text / random bytes — even
    if the filename ends in .png."""
    from app.application.image_service import save_reference_upload
    from app.exceptions import AppError
    import pytest

    with pytest.raises(AppError) as exc:
        _run(save_reference_upload(
            file_content=b"<html><body>hello</body></html>",
            file_name="evil.png",
            offer_id=_OFFER,
        ))
    assert exc.value.code == "INVALID_REFERENCE_FORMAT"


def test_save_reference_upload_rejects_corrupt_image():
    """Magic bytes can be faked; PIL.verify catches actually-corrupt
    files that would later blow up mid-generation."""
    from app.application.image_service import save_reference_upload
    from app.exceptions import AppError
    import pytest

    # PNG magic prefix, but the rest is garbage — PIL will read the
    # IHDR-less stream and raise.
    fake_png = b"\x89PNG\r\n\x1a\n" + b"GARBAGE_NOT_A_REAL_PNG_STREAM"
    with pytest.raises(AppError) as exc:
        _run(save_reference_upload(
            file_content=fake_png,
            file_name="fake.png",
            offer_id=_OFFER,
        ))
    assert exc.value.code == "CORRUPT_REFERENCE_IMAGE"
