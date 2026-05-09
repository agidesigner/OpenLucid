"""Pin image-generation prompt rules that prevent duplicate logos."""
from __future__ import annotations

from types import SimpleNamespace


def _fake_offer():
    return SimpleNamespace(
        name="蝉镜AI",
        core_selling_points_json={"points": ["三步成片"]},
        target_audience_json={"items": ["教育培训机构"]},
    )


def test_brief_prompt_uses_provided_logo_once_and_forbids_invented_marks():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="做一张教育培训行业海报",
        offer=_fake_offer(),
        brandkit=None,
        aspect="9:16",
        has_logo=True,
        has_qr=False,
        extra_count=0,
    )

    assert "Use only the provided logo as the brand mark" in prompt
    assert "exactly once" in prompt
    assert "do not place a second logo" in prompt
    assert "Do not invent" in prompt
    assert "ordinary headline/body text" in prompt
    assert "Brand context: 蝉镜AI" in prompt
    assert "Reference mode: visual style inspiration" in prompt
    assert "Style references define how the poster should look" in prompt
    assert "Extract their shared design language" in prompt
    assert "do not default to a monochrome" in prompt
    assert "Do not copy specific text, logos, wordmarks, watermarks" in prompt


def test_single_style_reference_is_a_strong_style_anchor():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="做一张教育培训行业海报",
        offer=_fake_offer(),
        brandkit=None,
        aspect="9:16",
        has_logo=True,
        has_qr=False,
        extra_count=0,
        reference_count=1,
    )

    assert "One style reference is provided" in prompt
    assert "highly similar in layout structure" in prompt
    assert "If a style-reference logo is not the current brand logo" in prompt
    assert "do not default to a monochrome" not in prompt


def test_style_prompt_allows_dark_palette_when_user_asks_for_it():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="做一张黑金暗色高端风宣发海报",
        offer=_fake_offer(),
        brandkit=None,
        aspect="9:16",
        has_logo=True,
        has_qr=False,
        extra_count=0,
    )

    assert "Style references define how the poster should look" in prompt
    assert "do not default to a monochrome" not in prompt


def test_content_references_define_subject_not_style():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="新功能上线，做一张小红书宣发海报",
        offer=_fake_offer(),
        brandkit=None,
        aspect="9:16",
        has_logo=True,
        has_qr=False,
        extra_count=1,
        reference_count=1,
    )

    assert "Content reference image(s) (1)" in prompt
    assert "factual subject evidence" in prompt
    assert "user brief and content references to decide what the poster is about" in prompt
    assert "style references only for visual treatment" in prompt


def test_brief_prompt_without_logo_forbids_model_inventing_a_logo():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="做一张教育培训行业海报",
        offer=_fake_offer(),
        brandkit=None,
        aspect="9:16",
        has_logo=False,
        has_qr=False,
        extra_count=0,
    )

    assert "No logo reference was provided" in prompt
    assert "Do not invent any logo" in prompt
    assert "provided logo exactly once" not in prompt


def test_source_poster_prompt_preserves_content_but_prevents_logo_stacking():
    from app.application.image_service import _build_brief_prompt

    prompt = _build_brief_prompt(
        brief="参考图改成横版尺寸",
        offer=_fake_offer(),
        brandkit=None,
        aspect="16:9",
        has_logo=True,
        has_qr=False,
        extra_count=1,
        reference_mode="source_poster",
    )

    assert "Reference mode: source-poster layout transform" in prompt
    assert "Preserve its core message" in prompt
    assert "do not replace the source poster's content with unrelated offer context" in prompt
    assert "if it is unrelated or third-party, remove or replace it" in prompt
    assert "Never stack, overlap, or duplicate" in prompt
    assert "Do not copy content-reference logos, watermarks, or unrelated brand marks" in prompt


def test_auto_reference_mode_detects_layout_transform_only_with_manual_refs():
    from app.application.image_service import _resolve_brief_reference_mode

    assert (
        _resolve_brief_reference_mode(
            "参考图改成横版尺寸",
            has_manual_visual_refs=True,
            requested="auto",
        )
        == "source_poster"
    )
    assert (
        _resolve_brief_reference_mode(
            "参考图改成横版尺寸",
            has_manual_visual_refs=False,
            requested="auto",
        )
        == "style"
    )


def test_english_resize_brief_routes_to_source_poster():
    from app.application.image_service import _resolve_brief_reference_mode

    assert (
        _resolve_brief_reference_mode(
            "convert this poster to 16:9 and keep the copy",
            has_manual_visual_refs=True,
            requested="auto",
        )
        == "source_poster"
    )


def test_no_manual_refs_always_yields_style_even_with_resize_words():
    from app.application.image_service import _resolve_brief_reference_mode

    assert (
        _resolve_brief_reference_mode(
            "把这张改成 9:16",
            has_manual_visual_refs=False,
            requested="auto",
        )
        == "style"
    )
    assert (
        _resolve_brief_reference_mode(
            "做一张新品推广海报",
            has_manual_visual_refs=True,
            requested="auto",
        )
        == "style"
    )


def test_content_references_do_not_count_as_style_references():
    import uuid

    from app.application.image_service import _has_manual_visual_references
    from app.schemas.image_generation import BriefJobCreate, ReferenceUploadInput

    offer_id = str(uuid.uuid4())
    assert not _has_manual_visual_references(
        BriefJobCreate(
            offer_id=offer_id,
            brief="新功能上线，做一张海报",
            extra_asset_ids=[str(uuid.uuid4())],
            extra_uploads=[
                ReferenceUploadInput(
                    upload_id="uploads/tmp/feature.png",
                    role="supplemental",
                )
            ],
        )
    )
    assert _has_manual_visual_references(
        BriefJobCreate(
            offer_id=offer_id,
            brief="参考这张海报风格做一张新图",
            reference_asset_ids=[str(uuid.uuid4())],
        )
    )


def test_resolve_reference_assets_respects_disabled_auto_fill():
    import asyncio
    import uuid

    from app.application.image_service import _resolve_reference_assets

    class ExplodingSession:
        async def execute(self, _stmt):
            raise AssertionError("auto suggestion query should not run")

    async def run():
        return await _resolve_reference_assets(
            ExplodingSession(),
            offer_id=uuid.uuid4(),
            brief="参考图改成横版尺寸",
            user_picked_ids=[],
            allow_auto=False,
        )

    assert asyncio.run(run()) == []


def test_legacy_edits_prompt_has_same_logo_discipline():
    from app.application.image_service import _build_edits_prompt

    template = SimpleNamespace(
        aspect_ratio="9:16",
        composition_brief="top headline, body sections, bottom CTA",
    )
    data = SimpleNamespace(
        selling_point="让知识被更多人看见",
        slot_values={"title": "用蝉镜AI 让知识被更多人看见"},
    )

    prompt = _build_edits_prompt(
        template,
        data,
        has_logo=True,
        has_qr=False,
    )

    assert "ONLY logo / brand mark allowed" in prompt
    assert "Place that provided logo once" in prompt
    assert "Do NOT invent" in prompt
    assert "ordinary text only" in prompt
