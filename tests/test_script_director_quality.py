from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace


def test_composer_prompt_includes_viral_rewrite_and_highlight_cues():
    from app.application.script_composer import compose_system_prompt

    prompt, platform, _ = asyncio.run(compose_system_prompt(platform_id="douyin"))

    assert platform.id == "douyin"
    assert "爆款选题拆解与重写" in prompt
    assert "不要平铺复述" in prompt
    assert "highlight_cues" in prompt
    assert "花字 / 重点停留规则" in prompt


def test_normalize_structured_content_sanitizes_highlight_cues():
    from app.application.script_platforms import get_platform
    from app.application.script_structures import get_structure
    from app.application.script_writer_service import _normalize_structured_content

    platform = get_platform("douyin")
    structure = get_structure("hook_body_cta")
    raw = {
        "estimated_total_seconds": 45,
        "sections": {
            "hook": {
                "text": "很多人买洗衣粉，只看香不香。",
                "visual_direction": "中景，家用洗衣场景。",
                "duration_seconds": 6,
            },
            "body": {
                "text": "真正影响体验的是冷水里能不能快速溶解，和顽固污渍能不能一次洗干净。",
                "visual_direction": "微距特写，冷水溶解实验。",
                "duration_seconds": 28,
            },
            "cta": {
                "text": "想看对比实测，评论区打洗衣。",
                "visual_direction": "数字人面对镜头。",
                "duration_seconds": 6,
            },
        },
        "highlight_cues": [
            {
                "insert_after_char": "很多人买洗衣粉，只看香不香。",
                "duration_seconds": 9,
                "emphasis_type": "unknown",
                "text": "只看香不香其实不够用",
            },
            {
                "insert_after_char": 35,
                "duration_seconds": 0.2,
                "emphasis_type": "proof_pop",
                "text": "冷水实测",
            },
            {
                "insert_after_char": "想看对比实测，评论区打洗衣。",
                "duration_seconds": 2,
                "emphasis_type": "cta_badge",
                "text": "评论区打洗衣",
            },
        ],
    }

    normalized = _normalize_structured_content(raw, platform, structure)

    cues = normalized["highlight_cues"]
    assert cues[0]["insert_after_char"] == len(raw["sections"]["hook"]["text"])
    assert cues[0]["duration_seconds"] == 2.8
    assert cues[0]["emphasis_type"] == "benefit_badge"
    assert cues[0]["text"] == "只看香不香其实不够用"
    assert cues[1]["duration_seconds"] == 1.2
    assert cues[1]["emphasis_type"] == "proof_pop"
    assert cues[-1]["emphasis_type"] == "cta_badge"


def test_compositor_accepts_highlight_cues_for_render_path():
    from app.adapters.video import broll_compositor

    sig = inspect.signature(broll_compositor.composite_broll)
    assert "highlight_cues" in sig.parameters

    src = inspect.getsource(broll_compositor.composite_broll)
    assert "_highlight_filters_for_window" in src
    assert "_draw_highlight_filter" in inspect.getsource(broll_compositor)


def test_compositor_hold_zoom_cue_drives_a_real_punch_in():
    """A hold_zoom cue must do more than draw a label — it should tighten
    the crop on the avatar shot hosting it, so the key beat reads as an
    actual camera punch-in (the "no zoom on high-attention moments" half
    of the feedback)."""
    from app.adapters.video import broll_compositor

    src = inspect.getsource(broll_compositor.composite_broll)
    # The window check feeds the zoom scalar, not just the drawtext layer.
    assert "_window_has_hold_zoom" in src
    assert "HOLD_ZOOM_LEVEL" in src
    assert "zoom = max(zoom, HOLD_ZOOM_LEVEL)" in src


def test_broll_rerank_summary_uses_semantic_asset_context():
    from app.application.broll_matching_service import (
        _asset_to_rerank_summary,
        _flatten_tags,
    )

    assert _flatten_tags({"scenario": ["厨房"], "selling_point": ["冷水溶解"]}) == [
        "厨房",
        "scenario:厨房",
        "冷水溶解",
        "selling_point:冷水溶解",
    ]

    asset = SimpleNamespace(
        id=uuid.uuid4(),
        file_name="cold-water-test.mp4",
        title="冷水溶解实验",
        asset_type="video",
        tags_json={"scenario": ["洗衣间"], "usage": ["实测"]},
        content_text="透明杯中展示冷水溶解速度，适合证明清洁力。",
        hook_score=0.7,
        reuse_score=0.9,
        slices=[
            SimpleNamespace(
                slice_type="proof",
                summary="冷水中粉末快速化开，没有结块。",
                transcript="倒入冷水后十秒开始扩散。",
                usage_tags_json={"proof": ["实验"]},
                scene_tags_json={"scene": ["透明杯"]},
                audience_tags_json={},
                hook_score=0.5,
                proof_score=0.95,
                reuse_score=0.88,
            )
        ],
    )

    summary = _asset_to_rerank_summary(asset)
    assert summary["title"] == "冷水溶解实验"
    assert "content_excerpt" in summary
    assert summary["slices"][0]["summary"] == "冷水中粉末快速化开，没有结块。"
    assert "proof:实验" in summary["slices"][0]["tags"]
