"""Pin script_writer's JSON-artifact scrubbing.

These tests cover the failure mode observed in production where the
LLM (typically Claude via OpenAI-compat) produced a prose draft, then
emitted a half-attempted ``` ```json {``` JSON opening mid-stream, then
continued with more prose. Without scrubbing, the literal ```` ```json
{ ```` artifact survives the heuristic and lands in the saved Creation.

A real-world example is captured at
``/Users/ajin/aitools/opendirector/app/application/script_writer_service.py:_INLINE_JSON_ARTIFACT_RE``.
"""
from __future__ import annotations


def test_scrub_strips_leading_json_fence():
    """Whole-string ```json … ``` fence — the original behavior."""
    from app.application.script_writer_service import _scrub_json_artifacts

    text = '```json\n{"text": "hello"}\n```'
    out = _scrub_json_artifacts(text)
    # Heuristic kicks in once the leading fence is stripped and the body
    # looks like a JSON object containing a "text" field.
    assert out == "hello"


def test_scrub_strips_inline_json_artifact_keeps_longest_chunk():
    """The Claude failure mode: prose, then ```json {``, then more prose.
    Without this fix, the artifact lands in the saved narration and the
    user sees backticks-and-braces in their output."""
    from app.application.script_writer_service import _scrub_json_artifacts

    bad = (
        "上周我刷到一个律师的视频，专业能力很强，但就是不想面"
        "```json\n{\n"
        "上周我刷到一个律师的短视频，讲得特别专业，"
        "评论区有人问，这是真人吗。这是更完整的一段叙述，"
        "比第一段更长更打磨。"
    )
    out = _scrub_json_artifacts(bad)
    # The artifact must be gone.
    assert "```" not in out
    assert "json" not in out.lower() or "deepseek" in out.lower()  # tolerate "json" if it appears in prose context
    # The longer, more polished chunk should win.
    assert "更完整的一段叙述" in out
    # The truncated draft fragment is dropped.
    assert "上周我刷到一个律师的视频，专业能力很强" not in out


def test_scrub_handles_closing_fence_too():
    """LLM sometimes emits both opening AND closing fences mid-stream."""
    from app.application.script_writer_service import _scrub_json_artifacts

    bad = (
        "完整的第一段口播，足够长以满足保留阈值，绝对超过20个字，"
        "讲清楚了背景和问题。"
        "```json\n{\n}\n```\n"
        "完整的第二段口播，更长更打磨，包含具体例子和呼吁。"
        "讲了多个段落，最后引导用户行动。"
    )
    out = _scrub_json_artifacts(bad)
    assert "```" not in out
    # Longest narrative wins (second chunk).
    assert "第二段口播" in out


def test_scrub_passes_through_clean_prose():
    """No artifacts → no change. Don't touch good output."""
    from app.application.script_writer_service import _scrub_json_artifacts

    clean = "这是一段完全没问题的口播文案，没有任何 JSON 痕迹。"
    out = _scrub_json_artifacts(clean)
    assert out == clean


def test_scrub_drops_pure_json_scaffolding_chunks():
    """If a split piece is just ``{`` or ``}`` or whitespace, drop it
    even if it'd be the only one — caller falls back to longest chunk."""
    from app.application.script_writer_service import _scrub_json_artifacts

    bad = "完整的口播文案在这里，长度足够，包含具体内容和细节描述。" + "\n```json\n{\n}\n```"
    out = _scrub_json_artifacts(bad)
    assert "```" not in out
    assert "完整的口播文案在这里" in out


def test_scrub_threshold_keeps_short_real_chunks_when_alone():
    """Edge case: every chunk is short. We have a 20-char minimum to
    weed out scaffolding, but if everything is short, the input was
    probably short prose without any artifact — pass it through."""
    from app.application.script_writer_service import _scrub_json_artifacts

    short = "短文案"
    out = _scrub_json_artifacts(short)
    # No artifact = no transformation = passes through.
    assert out == "短文案"
