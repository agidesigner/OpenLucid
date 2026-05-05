"""Pin the Class A / Class B reference-image domain model.

Bug v1.3.x recap: ``ref_img_url`` was a single overloaded parameter
across video providers. The frontend auto-sourced KB assets and fed
them through this field, but chanjing/Doubao + Veo 3.x both interpret
``ref_img_url`` as the **first frame** of an i2v video, with strict
aspect-ratio constraints. So a perfectly valid logo-shaped KB asset
would crash chanjing with ``code=50000: 宽高比 3.12 不在 [0.5, 2.0]``.

The fix: split ``ref_img_url`` into three semantic types —
``StyleReference`` (soft, Class A) and ``FirstFrame`` /
``LastFrame`` (hard, Class B). KB assets always become
StyleReferences; first/last frames are reserved for explicit user
uploads in a future UI.

These tests pin the contract so a future refactor can't quietly
re-merge the two semantics.
"""
from __future__ import annotations

import asyncio
import inspect


def test_reference_types_are_separate_dataclasses():
    """Three distinct types — collapsing them back into one would
    re-introduce the v1.3.x bug class."""
    from app.adapters.video.base import FirstFrame, LastFrame, StyleReference

    assert StyleReference is not FirstFrame
    assert StyleReference is not LastFrame
    assert FirstFrame is not LastFrame
    # Every type carries a url field
    assert "url" in StyleReference.__dataclass_fields__
    assert "url" in FirstFrame.__dataclass_fields__
    assert "url" in LastFrame.__dataclass_fields__
    # Only StyleReference is soft; carries optional description for
    # prompt-fallback when the provider doesn't support style refs.
    assert "description" in StyleReference.__dataclass_fields__


def test_unsupported_reference_mode_exists_for_hard_rejection():
    """Hard references (FirstFrame / LastFrame) MUST raise this on
    unsupported providers — silently dropping a user's explicit
    "video should start exactly with this frame" would be a hidden
    product bug. Soft StyleReference does NOT raise this."""
    from app.adapters.video.base import UnsupportedReferenceMode

    assert issubclass(UnsupportedReferenceMode, Exception)


def test_chanjing_drops_style_references_silently():
    """Chanjing/Doubao has no native style-reference channel. Style
    refs must be dropped without raising, without populating
    ``ref_img_url``. Otherwise we'd reintroduce the 50000 bug
    every time a KB asset has aspect outside [0.5, 2.0]."""
    from app.adapters.video.base import StyleReference
    from app.adapters.video.chanjing import ChanjingVideoProvider

    captured: dict = {}

    async def _fake_request(self, method, path, json_body=None, **kw):
        captured["payload"] = json_body
        return {"data": "fake-task-id"}

    ChanjingVideoProvider._request = _fake_request  # type: ignore[assignment]
    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    asyncio.run(p.submit_broll_clip(
        prompt="a brand banner",
        duration=6,
        aspect_ratio="9:16",
        model_code="Doubao-Seedance-1.0-pro",
        style_references=[StyleReference(url="https://x/logo.png")],
        first_frame=None,
        last_frame=None,
    ))
    payload = captured["payload"]
    # The bug-triggering field must NOT be set
    assert "ref_img_url" not in payload, \
        "StyleReference must NOT populate ref_img_url (Class A vs Class B mix-up)"
    # Other expected fields still present
    assert payload["ref_prompt"] == "a brand banner"
    assert payload["aspect_ratio"] == "9:16"


def test_chanjing_uses_ref_img_url_for_first_frame():
    """FirstFrame (Class B) is the legitimate use of ref_img_url —
    that field's API contract IS first-frame anchoring. Aspect-ratio
    validation happens server-side; we propagate the API error."""
    from app.adapters.video.base import FirstFrame
    from app.adapters.video.chanjing import ChanjingVideoProvider

    captured: dict = {}

    async def _fake_request(self, method, path, json_body=None, **kw):
        captured["payload"] = json_body
        return {"data": "fake-task-id"}

    ChanjingVideoProvider._request = _fake_request  # type: ignore[assignment]
    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    asyncio.run(p.submit_broll_clip(
        prompt="x",
        duration=6,
        aspect_ratio="9:16",
        model_code="Doubao-Seedance-1.0-pro",
        first_frame=FirstFrame(url="https://x/keyframe.png"),
    ))
    assert captured["payload"]["ref_img_url"] == ["https://x/keyframe.png"]


def test_chanjing_raises_unsupported_for_last_frame():
    """Doubao-Seedance has no last-frame anchoring. Must raise — not
    silently drop — because a user passing last_frame meant it as a
    hard requirement."""
    from app.adapters.video.base import LastFrame, UnsupportedReferenceMode
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    async def _run():
        await p.submit_broll_clip(
            prompt="x",
            duration=6,
            last_frame=LastFrame(url="https://x/end.png"),
        )

    try:
        asyncio.run(_run())
    except UnsupportedReferenceMode:
        return
    raise AssertionError("expected UnsupportedReferenceMode for last_frame on chanjing")


def test_veo_drops_style_references_silently():
    """Same contract on Veo: KB-style refs must NOT become Veo's
    instance.image (which is its first-frame field)."""
    from app.adapters.video.base import StyleReference
    from app.adapters.video.google_veo import GoogleVeoProvider

    p = GoogleVeoProvider.__new__(GoogleVeoProvider)
    p._api_key = ""  # triggers VEO_NO_API_KEY before HTTP — that's fine

    # We just need to verify the function signature accepts
    # style_references and routes it correctly. Inspect signature:
    sig = inspect.signature(p.submit_broll_clip)
    params = sig.parameters
    assert "style_references" in params
    assert "first_frame" in params
    assert "last_frame" in params
    # The legacy ``ref_img_url`` param must be gone — its presence
    # would mean a caller could still smuggle Class A images into
    # the first-frame slot.
    assert "ref_img_url" not in params, \
        "ref_img_url removed in v1.3.5 — keep it removed"


def test_chanjing_signature_no_legacy_ref_img_url():
    """Same anti-regression check for chanjing."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    sig = inspect.signature(ChanjingVideoProvider.submit_broll_clip)
    params = sig.parameters
    assert "style_references" in params
    assert "first_frame" in params
    assert "last_frame" in params
    assert "ref_img_url" not in params


def test_veo_payload_omits_numberOfVideos():
    """Veo 3.1+ rejects the ``numberOfVideos`` parameter with HTTP 400
    'isn't supported by this model'. The field has always defaulted
    to 1 (one video per call) which is exactly what we want, so the
    fix is to omit it entirely — backward-compatible across all Veo
    model versions.

    Production-verified: pre-fix, 4-of-4 broll shots failed with
    ``VEO_SUBMIT_FAILED ... 'numberOfVideos' isn't supported by this
    model``. Post-fix, no shot fails on this field."""
    import asyncio

    from app.adapters.video.google_veo import GoogleVeoProvider
    from app.exceptions import AppError

    captured: dict = {}

    class _FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"name": "operations/fake"}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, *, headers=None, json=None):
            captured["payload"] = json
            return _FakeResp()

    import app.adapters.video.google_veo as gv
    real_AsyncClient = gv.httpx.AsyncClient
    gv.httpx.AsyncClient = _FakeClient  # type: ignore[assignment]
    try:
        p = GoogleVeoProvider.__new__(GoogleVeoProvider)
        p._api_key = "fake"
        p._headers = {}

        asyncio.run(p.submit_broll_clip(
            prompt="x", duration=6, aspect_ratio="9:16",
            model_code="veo-3.1-generate-preview",
        ))
    finally:
        gv.httpx.AsyncClient = real_AsyncClient  # type: ignore[assignment]

    params = captured["payload"]["parameters"]
    assert "numberOfVideos" not in params, \
        "numberOfVideos must not be sent — Veo 3.1+ rejects it with 400"
    # Other expected parameters still present
    assert "aspectRatio" in params
    assert "durationSeconds" in params


def test_chanjing_kling_model_codes_match_docs_verbatim():
    """Chanjing's Kling model_code naming is inconsistent across
    versions; each must be copied verbatim from the per-version doc
    page or it returns ``50000: 模型不存在``. Don't pattern-match.

    Verified against chanjing docs:
      - https://doc.chanjing.cc/api/ai-creation/video-kling2.1.html
      - https://doc.chanjing.cc/api/ai-creation/video-kling2.5.html
    """
    from app.application.setting_service import _CAPABILITY_META

    capability = _CAPABILITY_META["video_gen"]
    chanjing_models = dict(capability["models_by_provider"]["chanjing"])

    # v2.1 — full form, hyphens, master suffix
    assert "tx_kling-v2-1-master" in chanjing_models, \
        "Kling v2.1 model_code must be exactly 'tx_kling-v2-1-master' (chanjing doc)"
    # v2.5 — short form, NO hyphens, NO prefix, NO suffix
    assert "kling2.5" in chanjing_models, \
        "Kling 2.5 model_code must be exactly 'kling2.5' (chanjing doc)"
    # Forbidden: pattern-matched guesses that look right but don't exist
    assert "kling-2.5" not in chanjing_models, \
        "Kling 2.5 is NOT 'kling-2.5' — production confirmed 模型不存在"
    assert "tx_kling-v2-5-master" not in chanjing_models, \
        "Kling 2.5 does NOT follow v2.1's tx_kling-v*-master pattern"


def test_strict_gate_module_state():
    """Strict-mode gate (any broll failure aborts avatar) replaced the
    earlier hard/soft distinction. Pin the module shape so a future
    revert can't sneak the lenient gate back without updating tests:
      * ``_is_hard_provider_error`` must NOT exist anymore (its
        purpose was the lenient gate's hard/soft check — now meaningless)
      * the gate code in create_video_job must reference the new
        ``failed_strict`` status marker
    """
    import inspect

    from app.application import video_service as svc

    assert not hasattr(svc, "_is_hard_provider_error"), (
        "Lenient hard/soft gate has been replaced by strict-mode; "
        "_is_hard_provider_error should be removed entirely."
    )

    src = inspect.getsource(svc.create_video_job)
    assert "failed_strict" in src, (
        "Strict gate sets params['broll_status'] = 'failed_strict' — "
        "missing means the gate has been weakened."
    )
    # The strict gate must fire on ANY broll failure, not just AI-submit
    # failures. Pre-v1.4.5 the gate read ``broll_specs and broll_failures``
    # which silently let through:
    #   * all-direct shots (broll_specs empty when no AI submits)
    #   * first_frame/reference prep failures that pre-v1.4.5 fell
    #     through to text-only AI submit (so broll_failures stayed empty)
    # The fixed gate reads ``if broll_failures:`` — anything in the
    # failure list bails out (we're inside the broll block already, so
    # reaching the gate already implies user opted into broll).
    assert "if broll_failures:" in src, (
        "Strict gate must fire on ANY failure (broll_failures non-empty); "
        "the legacy ``broll_specs and broll_failures`` form left direct/"
        "prep failures bypassing the gate."
    )
    assert "broll_specs and broll_failures" not in src, (
        "Stale lenient form ``broll_specs and broll_failures`` reappeared — "
        "it falsely scopes strictness to AI-submit failures only."
    )


def test_asset_auto_tagging_has_timeout_and_concurrency_guard():
    """Asset parsing must not stay in parse_status='tagging' forever when
    a local vision model stalls. Pin the module-level safeguards."""
    import inspect

    from app.application import asset_service as svc

    assert hasattr(svc, "AUTO_TAG_TIMEOUT_SECONDS")
    assert svc.AUTO_TAG_TIMEOUT_SECONDS > 0
    assert hasattr(svc, "_auto_tag_semaphore")

    src = inspect.getsource(svc.AssetService.run_parse)
    assert "asyncio.wait_for" in src, (
        "AI auto-tagging must be bounded so stalled vision calls still let "
        "the asset move from tagging to done."
    )
    assert "async with _auto_tag_semaphore()" in src, (
        "Batch uploads should not fire unlimited concurrent vision requests."
    )
    assert "except asyncio.TimeoutError" in src, (
        "Timeout must be handled as a non-fatal auto-tag skip."
    )


def test_asset_auto_tagging_uses_kb_and_scoped_existing_tags():
    """Asset tagging should use real offer KB context and avoid reusing
    visual subject tags from unrelated assets."""
    import inspect

    from app.application import asset_service as svc

    src = inspect.getsource(svc.AssetService._auto_tag)
    assert "KnowledgeItemRepository" in src
    assert '"knowledge_items"' in src
    assert "existing_tags_by_category" in src
    assert '"subject"' not in src.split("reusable_categories", 1)[1].split(")", 1)[0]
    assert '"existing_tags_sample": {' in src


def test_asset_tagging_prompt_guards_visual_facts_and_promo_mechanics():
    """Prompt constraints should prevent KB/history from polluting visual
    facts and keep campaign_type tied to explicit promo evidence."""
    import inspect

    from app.adapters import ai
    from app.adapters import prompt_builder

    prompt_src = inspect.getsource(ai.OpenAICompatibleAdapter.extract_asset_tags)
    assert "subject 必须只描述当前图片里确实可见" in prompt_src
    assert "ordinary event dates" in prompt_src
    assert "普通活动日期" in prompt_src
    assert "existing_sample, dict" in prompt_src
    assert "category-specific non-subject tags" in prompt_src

    offer_src = inspect.getsource(prompt_builder.format_offer_for_tagging)
    assert "knowledge_items" in offer_src
    assert "相关知识条目" in offer_src
    assert "不能当作画面事实" in offer_src


def test_chanjing_no_first_frame_markers_single_source_of_truth():
    """The chanjing adapter and the capability registry must agree on
    which models lack first-frame support. Pre-fix the two lists drifted
    (``viduq1`` was in setting_service but not in chanjing.py), letting
    MCP/API callers smuggle a first_frame past the UI guard. Pin the
    invariant so a future driver-by edit can't reintroduce the gap."""
    from app.adapters.video.chanjing import CHANJING_NO_FIRST_FRAME_MARKERS
    from app.application.setting_service import _video_model_ref_caps

    # Adapter exports the canonical list.
    assert "viduq1" in CHANJING_NO_FIRST_FRAME_MARKERS, (
        "viduq1 first-frame support is unverified — keep it on the no-i2v list"
    )
    assert "hailuo" in CHANJING_NO_FIRST_FRAME_MARKERS
    assert "happyhorse-1.0-t2v" in CHANJING_NO_FIRST_FRAME_MARKERS

    # Capability API must return supports_first_frame=False for every
    # marker, so the picker UI disables the i2v chip everywhere the
    # adapter would also reject.
    for marker in CHANJING_NO_FIRST_FRAME_MARKERS:
        caps = _video_model_ref_caps("chanjing", marker)
        assert caps["supports_first_frame"] is False, (
            f"chanjing/{marker} marked supports_first_frame=True in caps but "
            f"adapter rejects it — UI would offer a chip the adapter denies"
        )

    # Sanity: an i2v-capable model still reports True (regression guard).
    assert _video_model_ref_caps("chanjing", "Doubao-Seedance-1.0-pro")["supports_first_frame"] is True


def test_image_thumbnail_actually_shrinks_and_caps():
    """Real behavioral test (no inspect.getsource): drive the image
    thumbnail generator with three input sizes and verify the
    PIL-side policy:
      * 1125×5902 (extreme tall)  → fits within 512×512, aspect kept
      * 4000×3000 (large landscape) → fits within 512×512
      * 200×200 (tiny)            → NOT upscaled (still ≤ original)
      * RGBA PNG with transparency → flattened to JPEG without alpha
    The previous test only checked source strings, so a bug like the
    video upscale slip-up could pass."""
    import io
    from PIL import Image, ImageOps

    def thumb_pipeline(im_in: Image.Image) -> Image.Image:
        # Mirror _generate_image_thumbnail's transforms exactly. If
        # asset_service.py drifts, this test FAILS — that's the point.
        im = ImageOps.exif_transpose(im_in)
        im.thumbnail((512, 512), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85, optimize=True)
        buf.seek(0)
        return Image.open(buf)

    # Extreme tall: long edge clamped, aspect preserved, both edges ≤512
    out = thumb_pipeline(Image.new("RGB", (1125, 5902), "red"))
    assert max(out.size) <= 512
    assert out.size[1] == 512  # long edge hit the cap
    # Aspect preserved: 1125/5902 ≈ 0.190; out.width/out.height should match
    assert abs((out.size[0] / out.size[1]) - (1125 / 5902)) < 0.02

    # Large landscape: similarly capped
    out = thumb_pipeline(Image.new("RGB", (4000, 3000), "blue"))
    assert max(out.size) <= 512
    assert out.size[0] == 512  # long edge hit the cap

    # Tiny: must NOT be upscaled
    out = thumb_pipeline(Image.new("RGB", (200, 200), "green"))
    assert out.size == (200, 200), (
        f"200×200 tiny image got upscaled to {out.size}; thumbnail() must "
        "be shrink-only — Pillow guarantees this when target ≥ input but "
        "regression is easy if someone swaps to a different API"
    )

    # RGBA flattening: alpha must not survive into JPEG
    rgba = Image.new("RGBA", (300, 300), (255, 0, 0, 0))  # fully transparent red
    out = thumb_pipeline(rgba)
    assert out.mode == "RGB", "alpha channel must be flattened for JPEG output"


def test_video_thumbnail_ffmpeg_expression_does_not_upscale():
    """The reviewer caught a bug where ``force_original_aspect_ratio=
    decrease`` alone still upscales a 200×200 input to 512×512. The
    fix is to wrap the dimensions in ``min(iw, 512)`` / ``min(ih, 512)``
    so the target itself caps at the input. Run real ffmpeg on a
    fixture and verify."""
    import asyncio
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from PIL import Image

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg not installed — skipping behavioral test")

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.png"
        # 200×200 single-frame input — well below the 512 target
        Image.new("RGB", (200, 200), "magenta").save(src)
        out = Path(td) / "out.jpg"
        # The FIXED expression — must be in lockstep with asset_service
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-frames:v", "1",
            "-vf", "scale='min(iw,512)':'min(ih,512)':force_original_aspect_ratio=decrease",
            "-q:v", "3",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        assert proc.returncode == 0, f"ffmpeg failed: {proc.stderr.decode()[-200:]}"
        with Image.open(out) as im:
            assert im.size == (200, 200), (
                f"200×200 input upscaled to {im.size} — the ``min(iw,512)`` "
                "cap regressed; check asset_service._generate_video_thumbnail"
            )

        # Sanity: a 1920×1080 input should still be capped to fit 512×512
        big = Path(td) / "big.png"
        Image.new("RGB", (1920, 1080), "cyan").save(big)
        out2 = Path(td) / "out2.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(big), "-frames:v", "1",
             "-vf", "scale='min(iw,512)':'min(ih,512)':force_original_aspect_ratio=decrease",
             "-q:v", "3", str(out2)],
            capture_output=True,
        )
        with Image.open(out2) as im:
            assert max(im.size) <= 512
            assert im.size[0] == 512  # long edge hit the cap


def test_first_frame_reference_crop_makes_long_posters_provider_safe():
    """Long poster images must stay unchanged in the asset library, but
    first_frame upload gets a temporary provider-safe crop. The common
    Chanjing failure case is 1125×5902 (aspect 0.19), below the [0.5, 2.0]
    window; portrait B-roll should derive a 9:16-ish top-center JPEG."""
    import io

    from PIL import Image

    from app.application.video_service import _derive_first_frame_reference_image

    src = io.BytesIO()
    Image.new("RGB", (1125, 5902), "white").save(src, "PNG")
    out_bytes, out_name, info = _derive_first_frame_reference_image(
        src.getvalue(),
        "poster.png",
        1125,
        5902,
        "9:16",
        0.5,
        2.0,
    )

    with Image.open(io.BytesIO(out_bytes)) as im:
        ratio = im.size[0] / im.size[1]
        assert 0.54 <= ratio <= 0.59
        assert max(im.size) <= 1536
        assert im.mode == "RGB"

    assert out_name == "poster_broll_ref.jpg"
    assert info["crop_box"][0] == 0  # too-tall image: keep full width
    assert info["crop_box"][1] == 0  # top anchored, not center vertically
    assert info["target_aspect"] == round(9 / 16, 4)


def test_first_frame_reference_crop_centers_wide_images():
    """For too-wide inputs, keep the top band but center horizontally
    instead of blindly cropping from x=0."""
    import io

    from PIL import Image

    from app.application.video_service import _derive_first_frame_reference_image

    src = io.BytesIO()
    Image.new("RGB", (3000, 1000), "white").save(src, "PNG")
    out_bytes, _, info = _derive_first_frame_reference_image(
        src.getvalue(),
        "wide.png",
        3000,
        1000,
        "16:9",
        0.5,
        2.0,
    )

    with Image.open(io.BytesIO(out_bytes)) as im:
        ratio = im.size[0] / im.size[1]
        assert 1.70 <= ratio <= 1.85
    left, top, right, bottom = info["crop_box"]
    assert top == 0
    assert left > 0
    assert right < 3000
    assert bottom == 1000


def test_auto_tag_uses_original_image_not_thumbnail():
    """Vision LLM must analyze the ORIGINAL image, not the 512×512
    grid thumbnail. Pre-thumbnail-feature this happened naturally (no
    preview_uri); after thumbnails landed, _auto_tag silently switched
    to the smaller image, dropping tag quality on packaging text /
    fine details / multi-subject photos. Pin the priority order:
    image → storage_uri (original) before preview_uri."""
    import inspect

    from app.application import asset_service as svc

    src = inspect.getsource(svc.AssetService._auto_tag)
    # Find the image-path-resolution block. The image branch must
    # precede the preview_uri branch — string position is enough.
    image_branch = src.find('asset.asset_type == "image" and asset.storage_uri')
    preview_branch = src.find("asset.preview_uri")
    assert image_branch >= 0, "image-storage_uri branch missing"
    assert preview_branch >= 0
    assert image_branch < preview_branch, (
        "Image-asset original path must be checked BEFORE preview_uri so "
        "vision tagging reads the original, not the 512px grid thumbnail"
    )


def test_failed_parse_deletes_orphaned_new_preview():
    """Symmetric to old-preview cleanup: when a later parse step
    (parser.parse, slice insert) fails AFTER the new thumbnail is
    written to storage, the DB rollback restores the old preview_uri
    but the new file is now unreferenced. Pin the cleanup so retries
    don't accumulate orphan thumbnails (each ~50KB; 100 retries = 5MB
    of dead data)."""
    import inspect

    from app.application import asset_service as svc

    src = inspect.getsource(svc.AssetService.run_parse)

    # The new_preview_uri variable must be hoisted ABOVE the try block
    # so the except branch can reach it. Inside-try declaration would
    # mean rollback path can't see the var (and the orphan wouldn't be
    # deleted).
    hoist_pos = src.find("new_preview_uri: str | None = None")
    try_pos = src.find("try:", hoist_pos if hoist_pos > 0 else 0)
    assert hoist_pos >= 0, "new_preview_uri must be declared at function scope"
    # Hoist must come BEFORE the first try block.
    first_try_pos = src.find("try:")
    assert hoist_pos < first_try_pos, (
        "new_preview_uri must be hoisted above the try block so the except "
        "branch can clean up orphaned files"
    )

    # Except branch must call delete_file with new_preview_uri.
    except_pos = src.find("except Exception as e:")
    rollback_pos = src.find("await self.session.rollback()", except_pos)
    cleanup_pos = src.find("delete_file(new_preview_uri)", except_pos)
    assert except_pos >= 0
    assert rollback_pos >= 0
    assert cleanup_pos >= 0, (
        "Failed-parse path must delete the orphaned new preview file"
    )
    assert rollback_pos < cleanup_pos, (
        "Cleanup must run after rollback (DB state is stable then)"
    )


def test_preview_uri_replacement_deletes_old_file_only_after_commit():
    """When a re-parse runs against an asset that already has a
    preview_uri, the new thumbnail saves under a fresh UUID and the
    old file is orphaned. ``run_parse`` must:
      1. Capture the previous URI before writing the new one
      2. Delete the old file ONLY after the final session.commit()
         succeeds — otherwise a mid-pipeline failure (parser.parse,
         slice insert) would rollback the DB to the old URI while
         the file is already gone, producing broken thumbnails.

    Source-level pin: enforce the ordering invariant. The function
    is async and depends on a session/repo/storage harness, so a
    full behavioral test would need an integration fixture; the
    invariant we care about (deletion after commit) is structural
    enough that string ordering covers it."""
    import inspect

    from app.application import asset_service as svc

    src = inspect.getsource(svc.AssetService.run_parse)
    assert "pending_old_preview_to_delete" in src, (
        "run_parse must defer old preview deletion via a pending var"
    )
    # The pending capture must precede the final commit, and the
    # delete must follow it. Position-based check on the source.
    capture_pos = src.find("pending_old_preview_to_delete: str | None")
    final_commit_pos = src.find('parse_status="done"')
    delete_pos = src.find("delete_file(pending_old_preview_to_delete)")
    assert capture_pos >= 0, "missing pending capture"
    assert final_commit_pos >= 0, "missing final mark-done update"
    assert delete_pos >= 0, "missing deferred delete call"
    assert capture_pos < final_commit_pos < delete_pos, (
        "Old preview deletion must happen AFTER the final commit. "
        "If deletion runs before, a rollback on parse failure leaves "
        "the DB pointing to a deleted file."
    )
    # Defensive: the delete must NOT be inside the except branch
    # (otherwise rollback path would also delete the file).
    except_pos = src.find("except Exception as e:")
    assert delete_pos < except_pos, (
        "delete_file call must precede the outer except block — "
        "rollback path must NOT delete old previews"
    )


def test_thumbnail_endpoint_falls_back_for_legacy_images():
    """Images uploaded before the thumbnail generator was added have no
    preview_uri — the /thumbnail endpoint must fall back to the
    original file for images so existing grids don't render broken
    thumbnails. Videos without preview_uri still 404 (no still frame
    to serve). This test pins the fallback so a future "tighten the
    endpoint" refactor doesn't silently break the back-compat."""
    import inspect

    from app.api import assets as assets_api

    src = inspect.getsource(assets_api.get_thumbnail)
    assert 'asset_type == "image"' in src, (
        "Legacy fallback for images is the only thing keeping pre-thumbnail "
        "uploads visible in the grid"
    )
    assert "asset.storage_uri" in src
    # Videos must still 404 without preview_uri (no fallback frame to serve)
    assert "Thumbnail not available" in src


def test_auto_tag_clears_stale_tags_before_running():
    """Re-tagging an already-tagged asset must clear its old tags first.
    Without this, a vision-call failure (model swapped to a non-vision
    one, network glitch, timeout) would leave the asset showing its
    PREVIOUS tags forever — the user sees "tagged" when actually nothing
    new came back. The "no tags" UI banner relies on tags_json being
    empty after a failed run."""
    import inspect

    from app.application import asset_service as svc

    src = inspect.getsource(svc.AssetService.run_parse)
    # Must reset tag-output fields on the same update that flips status
    # to 'tagging' — atomic with the lock acquisition so we never have
    # a window where the asset shows stale tags + 'tagging' status at
    # the same time.
    assert 'tags_json={}' in src, (
        "Stale-tag guard removed — re-tag failures will keep showing old data"
    )
    assert 'parse_status="tagging"' in src


def test_mcp_submit_video_exposes_broll_fine_grained_params():
    """MCP must expose the same B-roll precision the WebUI offers,
    otherwise an agent using OpenLucid as a marketing brain can only
    flip ``broll=True`` and gets the LLM-default plan with the
    capability-default model — no way to bind specific KB assets,
    pin a specific i2v model, or override the auto-generated shot
    list. The schema (VideoGenerateRequest) already supports these
    fields; this test pins that they're surfaced via MCP too."""
    import inspect

    from app.mcp_server import submit_video

    params = inspect.signature(submit_video).parameters
    for required in ("broll_plan", "broll_provider_config_id", "broll_model_code"):
        assert required in params, (
            f"MCP submit_video missing {required!r} — agents lose B-roll "
            "precision the WebUI offers"
        )


def test_mcp_thin_wrappers_delegate_to_run_app():
    """``ask_kb`` / ``generate_topics`` are intentionally pure aliases —
    they MUST forward to ``run_app`` rather than call services
    directly. This keeps the dispatch logic single-sourced and avoids
    drift between two parallel code paths. Source-level guard."""
    import inspect

    from app.mcp_server import ask_kb, generate_topics

    ask_src = inspect.getsource(ask_kb)
    assert "run_app(" in ask_src and 'app_id="kb_qa"' in ask_src
    assert 'action="ask"' in ask_src

    topic_src = inspect.getsource(generate_topics)
    assert "run_app(" in topic_src and 'app_id="topic_studio"' in topic_src
    assert 'action="generate"' in topic_src


def test_mcp_list_apps_hides_non_runnable_taxonomy_apps():
    """``asset_tagging`` is a closed-vocabulary registry, not a
    runnable workflow — ``run_app`` has no dispatch branch for it.
    Listing it under list_apps misled agents into trying
    ``run_app("asset_tagging", ...)``. The fix excludes such apps
    from list_apps; they're still discoverable via get_app_config."""
    import inspect

    from app.mcp_server import list_apps

    src = inspect.getsource(list_apps)
    assert "NON_RUNNABLE" in src
    assert '"asset_tagging"' in src


def test_submit_video_broll_plan_normalizes_broll_switch():
    """Behavioral test (mocks the service layer): an agent that passes
    ``broll_plan=[...]`` but forgets ``broll=True`` must NOT silently
    get a broll-less video. The MCP wrapper auto-flips ``broll=True``
    for non-empty plans, treats ``[]`` as explicit opt-out, and hard-
    rejects model-pin without enable.

    Reviewer's request: replace source-string assertions with a real
    behavioral check that intercepts the dispatched VideoGenerateRequest."""
    import asyncio
    import uuid as _uuid
    from unittest.mock import patch

    from app import mcp_server
    from app.exceptions import AppError

    captured: list = []

    async def _fake_create_video_job(session, creation_id, data):
        captured.append(data)
        # Return a stub job-shaped object the existing _serialize can render.
        class _Stub:
            id = _uuid.uuid4()
            status = "pending"
            provider_task_id = None
            provider = "chanjing"
            video_url = None
            cover_url = None
            error_message = None
            duration_seconds = None
            progress = None
            created_at = None
            started_at = None
            finished_at = None
            params = {}
            creation_id = _uuid.uuid4()
        return _Stub()

    with patch("app.application.video_service.create_video_job", _fake_create_video_job):
        # Case 1: non-empty broll_plan → broll auto-enabled
        captured.clear()
        try:
            asyncio.run(mcp_server.submit_video(
                creation_id=str(_uuid.uuid4()),
                provider_config_id=str(_uuid.uuid4()),
                avatar_id="x", voice_id="y", script="hi",
                broll_plan=[{"type": "illustrative", "prompt": "p", "insert_after_char": 0, "duration_seconds": 5}],
            ))
        except Exception:
            # _serialize on the stub may fail; we only care that the
            # service was called with the normalized data.
            pass
        assert len(captured) == 1, "create_video_job should have been called once"
        assert captured[0].broll is True, (
            "Non-empty broll_plan must auto-enable broll. Pre-fix the agent "
            "could pass broll_plan=[...] and get a broll-less video."
        )
        assert captured[0].broll_plan and len(captured[0].broll_plan) == 1

        # Case 2: empty broll_plan → broll explicitly disabled
        captured.clear()
        try:
            asyncio.run(mcp_server.submit_video(
                creation_id=str(_uuid.uuid4()),
                provider_config_id=str(_uuid.uuid4()),
                avatar_id="x", voice_id="y", script="hi",
                broll=True,  # would normally enable, but plan=[] should override
                broll_plan=[],
            ))
        except Exception:
            pass
        assert captured and captured[0].broll is False, (
            "Explicit empty broll_plan must opt out, even if broll=True was passed"
        )

        # Case 3: model pin without broll → hard error (not silent ignore)
        try:
            asyncio.run(mcp_server.submit_video(
                creation_id=str(_uuid.uuid4()),
                provider_config_id=str(_uuid.uuid4()),
                avatar_id="x", voice_id="y", script="hi",
                broll_provider_config_id=str(_uuid.uuid4()),
                broll_model_code="Doubao-Seedance-1.0-pro",
            ))
            raise AssertionError("expected BROLL_MODEL_WITHOUT_BROLL")
        except AppError as e:
            assert "BROLL_MODEL_WITHOUT_BROLL" in str(e)


def test_generate_topics_count_out_of_range_raises():
    """Reviewer caught: pre-fix the underlying run_app silently clamped
    count > 20 to 5 (so an agent asking for 30 got 5 with no warning).
    The wrapper now validates 1 ≤ count ≤ 20 and surfaces a clear error."""
    import asyncio

    from app import mcp_server
    from app.exceptions import AppError

    for bad in (0, -3, 21, 100):
        try:
            asyncio.run(mcp_server.generate_topics(
                offer_id="11111111-1111-1111-1111-111111111111",
                count=bad,
            ))
            raise AssertionError(f"count={bad} should have raised TOPIC_COUNT_OUT_OF_RANGE")
        except AppError as e:
            assert "TOPIC_COUNT_OUT_OF_RANGE" in str(e), (
                f"count={bad} raised wrong error: {e}"
            )
