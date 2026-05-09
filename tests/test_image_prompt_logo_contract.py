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
