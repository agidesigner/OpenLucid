"""Trend-bridge mode for topic_studio (the "ride a hot topic" feature).

The feature has two halves and these tests pin both:

1. **G1 (UI gateway)** — `TopicPlanGenerateRequest` carries
   `external_context_text` / `external_context_url`, the field plumbs
   through `TopicStudioRunRequest`, and the topic_studio prompt
   actually emits the trend-bridge instructions when external context
   is supplied (and stays clean when it isn't, for backward compat).

2. **G2 (KB depth)** — `_build_infer_knowledge_system_prompt` carries
   the "architecture-layer facts" rule in BOTH languages. This is what
   makes the KB carry stable anchors like "multi-LLM scripting" so the
   trend-bridge step has something real to grab onto. The rule must
   stop short of version numbers / time-bound claims (the GPT-5 / V4
   problem) — these tests pin the boundary.

Plan reference: /Users/ajin/.claude/plans/index-html-offer-kb-offer-kb-velvety-pnueli.md
"""
from __future__ import annotations


def test_request_schema_carries_new_fields():
    from app.schemas.topic_plan import TopicPlanGenerateRequest

    req = TopicPlanGenerateRequest(
        offer_id="00000000-0000-0000-0000-000000000001",
        external_context_text="DeepSeek v4 launched today...",
        external_context_url="https://example.com/release",
    )
    assert req.external_context_text.startswith("DeepSeek")
    assert req.external_context_url == "https://example.com/release"


def test_request_schema_caps_external_text_at_8000_chars():
    """8001 chars must reject — guards prompt token budget."""
    from pydantic import ValidationError

    from app.schemas.topic_plan import TopicPlanGenerateRequest

    try:
        TopicPlanGenerateRequest(
            offer_id="00000000-0000-0000-0000-000000000001",
            external_context_text="x" * 8001,
        )
    except ValidationError:
        return
    raise AssertionError("8001 chars should have been rejected by max_length=8000")


def test_topic_studio_run_request_accepts_external_context():
    """The API-level schema mirrors the internal one — without this,
    the WebUI's payload silently drops the field at the route boundary."""
    from app.schemas.app import TopicStudioRunRequest

    body = TopicStudioRunRequest(
        offer_id="00000000-0000-0000-0000-000000000001",
        external_context_text="trend text",
        external_context_url="https://example.com",
        instruction="be punchy",
    )
    assert body.external_context_text == "trend text"
    assert body.instruction == "be punchy"


def test_kb_prompt_zh_pins_architecture_layer_rule():
    from app.adapters.ai import _build_infer_knowledge_system_prompt

    prompt = _build_infer_knowledge_system_prompt("zh-CN")
    # Positive: the rule must mention "architecture-layer facts" framing
    assert "架构层事实" in prompt
    # Positive: must give a concrete preserve-the-name example so the LLM
    # gets the WHAT, not just the WHY.
    assert "DeepSeek" in prompt and "Kimi" in prompt and "Grok" in prompt
    # Negative: must explicitly forbid version-number style facts —
    # without this the rule devolves into "include every brand name
    # you see", which is exactly the false-claim risk we're avoiding.
    assert "版本号" in prompt or "时效性" in prompt
    assert "GPT-5" in prompt  # cited as a counter-example


def test_kb_prompt_en_pins_architecture_layer_rule():
    from app.adapters.ai import _build_infer_knowledge_system_prompt

    prompt = _build_infer_knowledge_system_prompt("en-US")
    assert "ARCHITECTURE-LAYER FACTS" in prompt or "architecture-layer" in prompt.lower()
    assert "DeepSeek" in prompt
    assert "version numbers" in prompt.lower() or "time-bound" in prompt.lower()
    assert "GPT-5" in prompt


def test_topic_studio_prompt_classic_mode_has_no_trend_block():
    """When external_context is empty, the prompt must look exactly
    like the pre-feature prompt — same JSON-array shape, no trend
    mode language. Anything else is a backward-compat regression."""
    from app.adapters.ai import OpenAICompatibleAdapter

    # Pull the internal prompt builder by invoking through a minimal stub.
    # We capture the system+user via monkeypatching _chat to record args.
    import asyncio

    captured: dict = {}

    class _Captor(OpenAICompatibleAdapter):
        async def _chat(self, system, user, *a, **kw):
            captured["system"] = system
            captured["user"] = user
            # Return a valid empty array so the parser doesn't blow up.
            return "[]"

    adapter = _Captor.__new__(_Captor)
    adapter.provider = "test"
    adapter.model = "test"
    adapter.last_thinking = None

    async def _run():
        await adapter.generate_topic_plans(
            offer_context={"offer": {"name": "X"}, "selling_points": [], "knowledge_items": []},
            count=3,
            language="zh-CN",
            external_context_text=None,
        )

    asyncio.run(_run())
    sys = captured["system"]
    usr = captured["user"]
    assert "趁热点模式" not in sys and "Trend-Bridge Mode" not in sys
    # JSON shape: classic mode = array, not wrapper-object
    assert "JSON array" in sys or "JSON 数组" in sys or "strict JSON array" in sys
    assert "外部热点" not in usr and "External Trend" not in usr


def test_topic_studio_prompt_trend_mode_emits_required_blocks():
    """When external_context is provided, the prompt must add the
    structured-extraction step + four-tier rule + the wrapper-object
    JSON shape. Without these, the LLM falls back to old-style topic
    output and the UI gets nothing to gate on."""
    from app.adapters.ai import OpenAICompatibleAdapter

    import asyncio

    captured: dict = {}

    class _Captor(OpenAICompatibleAdapter):
        async def _chat(self, system, user, *a, **kw):
            captured["system"] = system
            captured["user"] = user
            return '{"hotspot": {"event": "x"}, "plans": []}'

    adapter = _Captor.__new__(_Captor)
    adapter.provider = "test"
    adapter.model = "test"
    adapter.last_thinking = None

    async def _run():
        return await adapter.generate_topic_plans(
            offer_context={"offer": {"name": "蝉镜"}, "selling_points": [], "knowledge_items": []},
            count=3,
            language="zh-CN",
            external_context_text="DeepSeek v4 发布说明 ...",
        )

    plans = asyncio.run(_run())
    sys = captured["system"]
    usr = captured["user"]

    # Trend-bridge system block present
    assert "趁热点模式" in sys
    # Four-tier rule present
    assert "strong" in sys and "medium" in sys and "weak" in sys
    # Stance instruction present (the GPT-suggested operational sentence)
    assert "解决方案" in sys or "自然出现" in sys
    # JSON shape switched to wrapper object with hotspot + plans
    assert "hotspot" in sys and "plans" in sys
    # External context appears in user message under the dedicated header
    assert "外部热点" in usr
    assert "DeepSeek v4 发布说明" in usr
    # Sentinel-prepended hotspot survived parse and is the first element
    assert plans[0].get("__hotspot__") == {"event": "x"}


def test_rank_knowledge_for_external_context_noop_when_empty():
    """Empty / missing context should not perturb the input — caller
    can blindly invoke the ranker without branching."""
    from app.adapters.prompt_builder import rank_knowledge_for_external_context

    items = [{"title": "a", "content_raw": "x"}, {"title": "b", "content_raw": "y"}]
    assert rank_knowledge_for_external_context(items, "") == items
    assert rank_knowledge_for_external_context(items, "   ") == items
    assert rank_knowledge_for_external_context([], "anything") == []


def test_rank_knowledge_for_external_context_short_list_passes_through():
    """Lists already under the cap get returned without scoring overhead."""
    from app.adapters.prompt_builder import rank_knowledge_for_external_context

    items = [{"title": f"item-{i}", "content_raw": "AI"} for i in range(5)]
    out = rank_knowledge_for_external_context(items, "AI is great", max_items=18)
    # Same items, original order — no shuffling for short lists.
    assert out == items


def test_rank_knowledge_for_external_context_caps_and_prefers_overlap():
    """Long lists get capped to ``max_items`` AND items that share tokens
    with the trend text rank above items that don't."""
    from app.adapters.prompt_builder import rank_knowledge_for_external_context

    # Build 30 items: half mention "DeepSeek", half are about unrelated topics.
    items = []
    for i in range(15):
        items.append({"title": f"deepseek-fact-{i}", "content_raw": "uses DeepSeek for inference"})
    for i in range(15):
        items.append({"title": f"random-{i}", "content_raw": "yoga and tea on a sunday"})

    out = rank_knowledge_for_external_context(
        items, "DeepSeek launched V4 with new inference capability", max_items=10
    )
    assert len(out) == 10
    # All of the top 10 should be from the relevant half.
    assert all("deepseek" in (item.get("title") or "").lower() for item in out)


def test_scan_complete_objects_finds_back_to_back_objects():
    """Streaming relies on this scanner — without it, plans don't get
    emitted until the entire JSON has arrived (defeats the purpose)."""
    from app.adapters.ai import _scan_complete_objects

    buf = '{"_kind":"hotspot","event":"x"}{"_kind":"plan","title":"a"}'
    out = list(_scan_complete_objects(buf, 0))
    assert len(out) == 2
    assert buf[out[0][0]:out[0][1]] == '{"_kind":"hotspot","event":"x"}'
    assert buf[out[1][0]:out[1][1]] == '{"_kind":"plan","title":"a"}'


def test_scan_complete_objects_handles_braces_inside_strings():
    """Quoted braces or quoted quotes must not confuse the depth counter
    — otherwise an LLM emitting a hook like '"Spoiler: {wow}"' would
    split a single object into two malformed chunks."""
    from app.adapters.ai import _scan_complete_objects

    buf = '{"title":"a {nested} b","hook":"He said \\"hi\\""}{"title":"second"}'
    out = list(_scan_complete_objects(buf, 0))
    assert len(out) == 2
    import json
    first = json.loads(buf[out[0][0]:out[0][1]])
    assert first["title"] == "a {nested} b"
    assert first["hook"] == 'He said "hi"'


def test_scan_complete_objects_skips_unclosed_trailing():
    """Half-arrived object must not be yielded — the parser would crash."""
    from app.adapters.ai import _scan_complete_objects

    buf = '{"title":"done"}{"title":"still arr'
    out = list(_scan_complete_objects(buf, 0))
    assert len(out) == 1


def test_streaming_adapter_emits_hotspot_then_plans():
    """Smoke test the streaming adapter end-to-end with a fake
    ``_chat_stream`` that drips back-to-back JSON objects. Validates:
    - hotspot event arrives first
    - each plan event carries the dict the LLM emitted
    - thinking blocks are stripped (don't end up in plan dicts)
    - done event arrives exactly once
    - obeying ``count`` cap"""
    import asyncio

    from app.adapters.ai import OpenAICompatibleAdapter

    async def _fake_chat_stream(self, system, user, **kw):
        # LLM emits reasoning, then hotspot, then 3 plans (drip-style).
        yield "<think>let me think</think>"
        yield '{"_kind":"hotspot","event":"X"'
        yield ',"keywords":["a","b"]}'
        yield '{"_kind":"plan","title":"first","angle":"x","hook":"h1"}'
        yield '{"_kind":"plan","title":"second","angle":"y","hook":"h2"}'
        yield '{"_kind":"plan","title":"third","angle":"z","hook":"h3"}'

    OpenAICompatibleAdapter._chat_stream = _fake_chat_stream  # type: ignore[assignment]
    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.provider = "test"
    adapter.model = "test"
    adapter.last_thinking = None

    async def _run():
        events: list = []
        async for ev in adapter.generate_topic_plans_stream(
            offer_context={"offer": {"name": "x"}, "selling_points": [], "knowledge_items": []},
            count=5,
            language="zh-CN",
            external_context_text="some trend",
        ):
            events.append(ev)
        return events

    events = asyncio.run(_run())

    types = [e[0] for e in events]
    assert "hotspot" in types
    plan_events = [e for e in events if e[0] == "plan"]
    assert len(plan_events) == 3
    titles = [e[1]["title"] for e in plan_events]
    assert titles == ["first", "second", "third"]
    # done is the last event
    assert events[-1][0] == "done"
    # _kind discriminator must be popped before yielding
    for _, plan in plan_events:
        assert "_kind" not in plan
    # thinking captured via last_thinking parity
    assert adapter.last_thinking and "let me think" in adapter.last_thinking


def test_trend_system_block_pins_temporal_anchor_zh():
    """The script-gen trend prompt MUST forbid retrospective framing.

    Why: LLMs interpret "practitioner voice" as "I've been using this for
    months/years", which leads them to fabricate a past where the user
    was already using a just-released trend. Symptom: a script generated
    around DeepSeek V4 (released today) that opens with "去年冬天 V4 刚
    发布的时候我已经用上了" — temporally impossible.

    Without this guard, the bug recurs for every freshly-released
    product trend (GPT-5, iPhone N, etc.).
    """
    from app.adapters.prompt_builder import format_trend_system_block

    block = format_trend_system_block(mode="script_gen", language="zh-CN")
    # The "now, not memory" directive must be present in unambiguous form.
    assert "时间锚点" in block
    assert "不是回忆" in block
    # Common offending phrases must be explicitly listed as anti-patterns
    # so the model treats them as concrete don'ts, not vague guidance.
    assert "去年冬天" in block or "三个月" in block
    # Stance redefinition: practitioner = current reactor, not retro veteran.
    assert "刚看到这条新闻" in block or "在做出反应" in block


def test_trend_system_block_pins_temporal_anchor_en():
    """English mirror of the temporal anchor guard."""
    from app.adapters.prompt_builder import format_trend_system_block

    block = format_trend_system_block(mode="script_gen", language="en-US")
    assert "TEMPORAL ANCHOR" in block or "happening RIGHT NOW" in block
    # Negative examples (forbidden phrasings) must be cited so the model
    # has concrete anti-patterns to avoid.
    assert "back when" in block.lower() or "back in those days" in block.lower()


def test_resolve_trend_context_inactive_when_all_inputs_empty():
    """No topic_plan_id + no external_context_text → ``is_active`` False
    so callers can branch on a single property instead of three checks."""
    import asyncio

    from app.adapters.prompt_builder import resolve_trend_context

    async def _run():
        return await resolve_trend_context(
            session=None,  # never touched when topic_plan_id is None
            topic_plan_id=None,
            external_context_text=None,
            external_context_url=None,
        )

    trend = asyncio.run(_run())
    assert trend.is_active is False
    assert trend.external_context_text is None
    assert trend.hotspot is None
    assert trend.do_not_associate is None
    assert trend.relevance_tier is None


def test_resolve_trend_context_direct_input_only():
    """User pastes external_context_text directly (the new path that
    lets script-writer / content-studio ride a trend without going
    through topic_studio first)."""
    import asyncio

    from app.adapters.prompt_builder import resolve_trend_context

    async def _run():
        return await resolve_trend_context(
            session=None,
            topic_plan_id=None,
            external_context_text="DeepSeek V4 just launched...",
            external_context_url="https://example.com/v4",
        )

    trend = asyncio.run(_run())
    assert trend.is_active is True
    assert trend.external_context_text == "DeepSeek V4 just launched..."
    assert trend.external_context_url == "https://example.com/v4"
    # Direct-only path can't know hotspot — script-writer doesn't
    # re-extract on its own, leaving these None is the contract.
    assert trend.hotspot is None
    assert trend.do_not_associate is None
    assert trend.relevance_tier is None


def test_resolve_trend_context_strips_whitespace_only_input():
    """Trim/empty inputs collapse to None so ``is_active`` doesn't
    fire on accidental whitespace pastes."""
    import asyncio

    from app.adapters.prompt_builder import resolve_trend_context

    async def _run():
        return await resolve_trend_context(
            session=None,
            topic_plan_id=None,
            external_context_text="   \n   ",
            external_context_url="  ",
        )

    trend = asyncio.run(_run())
    assert trend.is_active is False
    assert trend.external_context_text is None
    assert trend.external_context_url is None


def test_format_trend_user_block_inactive_returns_empty():
    """Empty trend = empty block. Callers concatenate without branching."""
    from app.adapters.prompt_builder import ResolvedTrendContext, format_trend_user_block

    trend = ResolvedTrendContext()
    assert trend.is_active is False
    assert format_trend_user_block(trend, language="zh-CN") == ""
    assert format_trend_user_block(trend, language="en-US") == ""


def test_format_trend_user_block_renders_inherited_hotspot():
    """The inherited hotspot from a topic plan should render into
    labeled bullets the LLM can read directly."""
    from app.adapters.prompt_builder import ResolvedTrendContext, format_trend_user_block

    trend = ResolvedTrendContext(
        hotspot={
            "event": "DeepSeek V4 launches",
            "keywords": ["V4", "Agent", "1M context"],
            "public_attention": "how this lowers content production cost",
            "risk_zones": ["don't claim our product uses V4"],
        }
    )
    block_zh = format_trend_user_block(trend, language="zh-CN")
    assert "外部热点" in block_zh
    assert "DeepSeek V4 launches" in block_zh
    assert "Agent" in block_zh
    assert "硬性回避" in block_zh
    assert "don't claim our product uses V4" in block_zh

    block_en = format_trend_user_block(trend, language="en-US")
    assert "External Trend" in block_en
    assert "audience really cares about" in block_en
    assert "HARD avoid" in block_en


def test_format_trend_user_block_combines_direct_and_inherited():
    """Direct external_context_text + inherited hotspot both flow into
    the prompt — direct as raw text, inherited as structured bullets.
    This is what makes Flow 3 (continue-from-topic + supplement) work."""
    from app.adapters.prompt_builder import ResolvedTrendContext, format_trend_user_block

    trend = ResolvedTrendContext(
        external_context_text="DeepSeek released a v4.1 patch this morning.",
        hotspot={"event": "DeepSeek V4 launches"},
        do_not_associate=["don't claim integration"],
        relevance_tier="medium",
    )
    block = format_trend_user_block(trend, language="zh-CN")
    # Inherited hotspot bullet
    assert "DeepSeek V4 launches" in block
    # Direct paste shown as raw text under its own subheader
    assert "DeepSeek released a v4.1 patch this morning." in block
    assert "外部原文" in block
    # Per-plan guardrails surface as HARD constraints
    assert "don't claim integration" in block
    # Stance hint surfaces with the plan's tier
    assert "medium" in block


def test_trend_system_block_topic_gen_also_pins_temporal_anchor():
    """Topic-gen mode is also susceptible (a topic title like 'V4 来了，
    我已经用了三个月' would slip through). Apply the same guard there."""
    from app.adapters.prompt_builder import format_trend_system_block

    zh = format_trend_system_block(mode="topic_gen", language="zh-CN")
    en = format_trend_system_block(mode="topic_gen", language="en-US")
    assert "时间锚点" in zh
    assert "TEMPORAL ANCHOR" in en or "RIGHT NOW" in en


def test_streaming_adapter_count_cap_stops_early():
    """Even if the LLM keeps emitting plans, ``count`` should hard-cap
    so we don't persist 8 plans when the user asked for 3."""
    import asyncio

    from app.adapters.ai import OpenAICompatibleAdapter

    async def _fake_chat_stream(self, system, user, **kw):
        for i in range(8):
            yield f'{{"_kind":"plan","title":"p{i}","angle":"x","hook":"h"}}'

    OpenAICompatibleAdapter._chat_stream = _fake_chat_stream  # type: ignore[assignment]
    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.provider = "test"
    adapter.model = "test"
    adapter.last_thinking = None

    async def _run():
        events = []
        async for ev in adapter.generate_topic_plans_stream(
            offer_context={"offer": {"name": "x"}, "selling_points": [], "knowledge_items": []},
            count=3,
            language="zh-CN",
            external_context_text=None,
        ):
            events.append(ev)
        return events

    events = asyncio.run(_run())
    plan_events = [e for e in events if e[0] == "plan"]
    assert len(plan_events) == 3


def test_topic_studio_prompt_trend_mode_english():
    """English prompt path mirrors the Chinese one."""
    from app.adapters.ai import OpenAICompatibleAdapter

    import asyncio

    captured: dict = {}

    class _Captor(OpenAICompatibleAdapter):
        async def _chat(self, system, user, *a, **kw):
            captured["system"] = system
            captured["user"] = user
            return '{"hotspot": null, "plans": []}'

    adapter = _Captor.__new__(_Captor)
    adapter.provider = "test"
    adapter.model = "test"
    adapter.last_thinking = None

    async def _run():
        await adapter.generate_topic_plans(
            offer_context={"offer": {"name": "Chanjing"}, "selling_points": [], "knowledge_items": []},
            count=3,
            language="en-US",
            external_context_text="DeepSeek v4 released ...",
        )

    asyncio.run(_run())
    sys = captured["system"]
    usr = captured["user"]
    assert "Trend-Bridge Mode" in sys
    assert "strong" in sys and "medium" in sys and "weak" in sys
    assert "solution" in sys.lower()
    assert "External Trend" in usr
    assert "DeepSeek v4 released" in usr
