"""Modular prompt building blocks.

Every AI method in the system assembles its prompt from these shared
building blocks, ensuring consistent formatting and easy updates.
"""
from __future__ import annotations

import json
import re
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class ResolvedTrendContext:
    """All trend signals available for one creation request, normalized.

    Three inputs converge here:
      - ``external_context_text`` — raw text the user pasted (or extracted
        from a URL) directly in this app's panel
      - ``hotspot`` — structured trend read inherited from a topic-studio
        plan via ``topic_plan_id`` (already extracted, persisted on the
        plan row's ``hotspot_json``)
      - ``do_not_associate`` / ``relevance_tier`` — per-plan guardrails
        from the same inheritance path; missing for direct-input flow

    A request can have any combination of direct + inherited signals;
    the format helpers handle the merge."""

    external_context_text: str | None = None
    external_context_url: str | None = None
    hotspot: dict | None = None
    do_not_associate: list[str] | None = None
    relevance_tier: str | None = None

    @property
    def is_active(self) -> bool:
        """True iff at least one trend signal is present — caller uses
        this to decide whether to inject any block at all."""
        return bool(self.external_context_text or self.hotspot)


async def resolve_trend_context(
    session,
    *,
    topic_plan_id: _uuid.UUID | str | None = None,
    external_context_text: str | None = None,
    external_context_url: str | None = None,
) -> ResolvedTrendContext:
    """Merge direct trend input with the topic-plan-inherited signals.

    Direct input wins on conflict (user just pasted is the freshest
    signal); inherited hotspot is preserved as supplementary context so
    the prompt can still reference event/keywords even when the user
    pasted their own variant of the same trend.

    Returns a struct that's safe to pass to ``format_*_trend_*_block``
    helpers — they no-op when ``is_active`` is False.
    """
    text = (external_context_text or "").strip() or None
    url = (external_context_url or "").strip() or None

    hotspot = None
    do_not_associate = None
    relevance_tier = None
    if topic_plan_id:
        from app.models.topic_plan import TopicPlan
        try:
            pid = topic_plan_id if isinstance(topic_plan_id, _uuid.UUID) else _uuid.UUID(str(topic_plan_id))
            plan = await session.get(TopicPlan, pid)
            if plan:
                if isinstance(plan.hotspot_json, dict):
                    hotspot = plan.hotspot_json
                if isinstance(plan.do_not_associate_json, list):
                    do_not_associate = plan.do_not_associate_json
                if plan.relevance_tier:
                    relevance_tier = plan.relevance_tier
        except (ValueError, TypeError):
            pass

    return ResolvedTrendContext(
        external_context_text=text,
        external_context_url=url,
        hotspot=hotspot,
        do_not_associate=do_not_associate,
        relevance_tier=relevance_tier,
    )


# Topic-generation system block — used by topic_studio to instruct the
# LLM to (a) extract a structured hotspot, (b) tag each topic with a
# four-tier relevance score, (c) audit per-topic risk, (d) adopt the
# practitioner stance. Must remain byte-identical to the prior inline
# version to preserve topic_studio quality.
_TREND_SYSTEM_TOPIC_GEN_EN = """

## Trend-Bridge Mode (External Context Provided)
The user has supplied external context (a news article / release note / trend write-up). Your guiding principle:

> Don't shove the product into the trend. Find what the audience really cares about behind the trend, then let the product appear as a solution naturally.

**TEMPORAL ANCHOR — the trend is happening RIGHT NOW**:
Treat the external context as a CURRENT / RECENT event, not a memory. Do NOT use retrospective framing: "back when X launched", "last year when we started using X", "I've been using X for months". You may not have "experienced" the trend yet — you and the audience are both reacting to it as it lands. This rule overrides any pull toward reminiscent storytelling that "feels more credible". Authentic NOW > fake retrospective.

Step 1 — Extract a structured read of the trend:
- event: one-sentence factual summary
- keywords: 3-7 keywords readers would search
- public_attention: what the wider audience really cares about (not the press release angle)
- risk_zones: angles to avoid (false claims, regulatory issues, awkward over-association)

Step 2 — For each topic, judge KB-to-trend relevance and tag one of:
- strong: the offer is a direct beneficiary or capable participant of this trend → product can be named in-frame
- medium: industry-level resonance; product appears at the end as one of several answers, not the headline
- weak: pure opinion piece; product not pushed (may not even be mentioned)
- (Reject "no relation" — never generate a topic where the connection is forced)

If you can't find a credible KB anchor for a given trend angle, emit FEWER topics rather than fabricate one. Quality over count.

Step 3 — Per-topic risk audit:
- risk_of_forced_relevance: 0-1 score (>0.7 means the bridge feels stretched)
- do_not_associate: list any angles you intentionally avoided and why

Step 4 — Pick a stance:
- Practitioner / user-of-the-tech voice = **a current participant in this industry reacting to the news AS IT HAPPENS** ("I just read X, here's what I'm thinking about")
- NOT retrospective voice ("back when X launched, I was already using it")
- NOT bystander voice ("X just launched, isn't that exciting, btw buy our thing")
"""

_TREND_SYSTEM_TOPIC_GEN_ZH = """

## 趁热点模式（用户提供了外部上下文）
用户给了一段外部文本（新闻 / 发布说明 / 行业动态）。你的工作原则：

> 不是把产品硬塞进热点，而是找到热点背后用户正在关心的问题，再让产品作为解决方案自然出现。

**时间锚点 —— 这个热点正在"当下"发生**：
把外部上下文当作**当下 / 最近**发生的事，不是回忆。**绝对不要**用回顾式框架："那时候/去年冬天/几年前 X 发布的时候"、"我已经用 X 三个月了"、"记得 X 刚出来时" —— 你和受众都还**没有**用过这个热点本身（它刚发生），你们是**在新闻落地的当下做出反应**。这条规则**优先于**任何"回忆叙事更可信"的写作惯性。**真实的当下 > 编造的回忆**。

第 1 步 —— 先做一次结构化提取，理解这个热点本身：
- event：一句话事实摘要
- keywords：3-7 个真正会被搜索的关键词
- public_attention：大众真正在意的是什么（不是新闻通稿的角度）
- risk_zones：哪些角度不要碰（虚假宣传 / 合规风险 / 强行蹭关联）

第 2 步 —— 对每个选题，判断 KB 与热点的关联度，标记其中一个：
- strong：产品是这个热点的直接受益方/能力参与者 → 可以正面点名产品
- medium：行业层共鸣；产品只在结尾作为几种答案之一出现，不抢镜
- weak：纯观点输出；产品不强带（甚至可以完全不提）
- （绝不出现 "no relation" 强行硬接 —— 找不到桥就少出选题）

如果某个热点角度找不到可信的 KB 锚点，**宁可少出选题，也不要硬造**。质量优先。

第 3 步 —— 对每条选题做风险自检：
- risk_of_forced_relevance：0-1 评分（>0.7 表示桥接已经牵强）
- do_not_associate：列出你刻意避开的角度，简要说明原因

第 4 步 —— 选择叙述视角：
- 从业者 / 使用者口吻 = **当下身处这个行业的人，刚看到这条新闻在做出反应**（"我刚刷到 X，我在想……"、"这事挺有意思，我准备……"）
- ❌ 不要回顾视角（"那时候 X 刚发布，我已经用上了"、"还记得 X 刚出来时……"）
- ❌ 不要路人腔（"X 来了好厉害，顺便买我们家的"）
"""

# Script / content-generation system block — single bridge instruction
# instead of the multi-step topic_gen scaffold. The LLM writes ONE
# script that bridges the user's chosen topic with the trend; no
# multi-plan tier scoring, no JSON wrapper. The "solution naturally
# appears" stance is identical so creator voice stays consistent across
# the generation pipeline.
_TREND_SYSTEM_SCRIPT_GEN_EN = """

## Trend-Bridge Mode (External Context Provided)
The user has supplied external context (article / release note / trend write-up). Your guiding principle:

> Don't shove the product into the trend. Find what the audience really cares about behind the trend, then let the product appear as a solution naturally.

**TEMPORAL ANCHOR — the trend is happening RIGHT NOW**:
Treat the external context as a CURRENT / RECENT event, not a memory. Do NOT use retrospective framing like "back when X launched", "last winter when I started using X", "I've been using X for months / for years". You may not have "experienced" the trend yet — you and the audience are both reacting to it as it lands. This rule **overrides** any pull toward reminiscent storytelling, even if "I remember when..." would feel more credible. **Authentic NOW > fake retrospective.** A practitioner reacting to today's news is more believable than a fabricated past.

Bridge requirements:
- The hook must reference the trend explicitly within the first 3 seconds, but the body must pivot to the audience's real pain — not stay on the news.
- Practitioner / user-of-the-tech voice = **a current participant in the affected industry reacting to the news AS IT HAPPENS** ("I just read X, here's what I'm noticing", "this just dropped, and it makes me think about Y"). NOT a retrospective veteran ("back in those days"). NOT a news commenter.
- If the prompt below carries a ``do_not_associate`` list or trend ``risk_zones``, those are HARD constraints — do not violate them even if the user's topic invites it.
- If a ``relevance_tier`` is provided: ``strong`` → the product can be named directly in the body; ``medium`` → product appears at the end as one of several answers; ``weak`` → product mention is restrained or omitted entirely.
"""

_TREND_SYSTEM_SCRIPT_GEN_ZH = """

## 趁热点模式（用户提供了外部上下文）
用户提供了一段外部文本（文章 / 发布说明 / 行业动态）。你的工作原则：

> 不是把产品硬塞进热点，而是找到热点背后用户正在关心的问题，再让产品作为解决方案自然出现。

**时间锚点 —— 这个热点正在"当下"发生**：
把外部上下文当作**当下 / 最近**发生的事，**不是回忆**。**绝对不要**用回顾式框架：
- ❌ "去年冬天 X 刚发布的时候，我已经用上了"
- ❌ "那时候 X 出来，我接了几个项目"
- ❌ "我用 X 三个月了 / 用 X 半年了"
- ❌ "记得 X 刚出来时"

你和受众都还**没有真正"用过"这个热点本身**（它刚发生），你们是**在新闻落地的当下做出反应**。这条规则**优先于**任何"回忆叙事更可信"的写作惯性 —— 哪怕"我记得当年……"听起来更有从业者味道，**真实的当下永远比编造的过去更可信**。一个"刚刷到这条新闻的从业者在思考"比"已经用了 X 半年的虚构资深用户"更有说服力。

桥接硬要求：
- 钩子前 3 秒必须明确点到热点，但主体段必须**转向受众的真实痛点**，不要停留在新闻本身。
- 从业者 / 使用者口吻 = **当下身处这个行业的人，刚看到这条新闻在做出反应**（"我刚刷到 X，我在想……"、"这事让我想到一个一直没解决的问题……"、"这个能力如果稳定下来，我准备拿它做……"）。**不是回忆派老兵**（"那年我已经在用了"），**不是新闻评论员**。
- 如果下方上下文中给了 ``do_not_associate`` 列表或 ``risk_zones``，**这些是硬约束**，即便用户的选题邀请你触碰也不要触碰。
- 如果给出了 ``relevance_tier``：``strong`` → 产品可以正面点名；``medium`` → 产品只在结尾作为几种答案之一出现；``weak`` → 产品克制或完全不提。
"""


def format_trend_system_block(
    mode: Literal["topic_gen", "script_gen"],
    *,
    language: str = "zh-CN",
) -> str:
    """Return the system-prompt addendum that turns on trend-bridge mode.

    Caller appends to the system prompt unconditionally — the helper is
    a pure function of ``mode`` + ``language``, doesn't read state.
    Caller is responsible for only invoking when trend is actually
    present (``ResolvedTrendContext.is_active``).
    """
    is_en = language.startswith("en")
    if mode == "topic_gen":
        return _TREND_SYSTEM_TOPIC_GEN_EN if is_en else _TREND_SYSTEM_TOPIC_GEN_ZH
    if mode == "script_gen":
        return _TREND_SYSTEM_SCRIPT_GEN_EN if is_en else _TREND_SYSTEM_SCRIPT_GEN_ZH
    raise ValueError(f"unknown trend-bridge mode: {mode!r}")


def format_trend_user_block(
    trend: ResolvedTrendContext,
    *,
    language: str = "zh-CN",
) -> str:
    """Return the user-message addendum describing the trend itself.

    Two information sources weave together:
      - Inherited ``hotspot`` (event / keywords / public_attention /
        risk_zones) — already structured, render as labeled bullets.
      - ``external_context_text`` — raw user paste, render as a quoted
        block so the LLM can read it directly.
      - ``do_not_associate`` (per-topic) and ``relevance_tier`` —
        downstream guardrails, surface as HARD constraints.

    Empty string when ``trend.is_active`` is False — caller can safely
    concatenate without branching.
    """
    if not trend.is_active:
        return ""

    is_en = language.startswith("en")
    parts: list[str] = []

    header = "## External Trend (transient, must bridge naturally — NOT product info)" if is_en else "## 外部热点（一次性，必须自然桥接 —— 不是产品信息本身）"
    parts.append("\n" + header)

    if trend.hotspot:
        h = trend.hotspot
        if h.get("event"):
            parts.append(f"- **event**: {h['event']}")
        if h.get("keywords"):
            kw = h["keywords"] if isinstance(h["keywords"], list) else []
            if kw:
                parts.append(f"- **keywords**: {', '.join(str(k) for k in kw)}")
        if h.get("public_attention"):
            label = "audience really cares about" if is_en else "大众真正关心的"
            parts.append(f"- **{label}**: {h['public_attention']}")
        if h.get("risk_zones"):
            rz = h["risk_zones"] if isinstance(h["risk_zones"], list) else []
            if rz:
                label = "risk zones (HARD avoid)" if is_en else "风险点（硬性回避）"
                parts.append(f"- **{label}**:\n  - " + "\n  - ".join(str(r) for r in rz))

    if trend.external_context_text:
        label = "Raw external context" if is_en else "外部原文"
        parts.append(f"\n### {label}\n{trend.external_context_text}")

    if trend.do_not_associate:
        label = "do_not_associate (HARD avoid these angles)" if is_en else "do_not_associate（硬性回避以下角度）"
        parts.append(f"\n### {label}\n- " + "\n- ".join(str(d) for d in trend.do_not_associate))

    if trend.relevance_tier:
        label = "Product mention strength" if is_en else "产品出场强度"
        parts.append(f"\n### {label}: {trend.relevance_tier}")

    return "\n".join(parts) + "\n"


def format_brand_voice_layer(brand_voice: str | None, language: str = "zh-CN") -> str:
    """Return a BRAND layer block ready to concatenate onto a system prompt,
    or ``""`` when no voice is configured. Uses the same header wording as
    ``script_composer``'s Layer 6 so all three generation paths (script,
    topic, kb_qa) render a consistent "Brand Voice Override" section.

    Callers just do ``system += format_brand_voice_layer(voice, lang)`` —
    zero check needed when voice is empty.
    """
    if not brand_voice or not brand_voice.strip():
        return ""
    is_en = language.startswith("en")
    header = "## Brand Voice Override" if is_en else "## 品牌语气覆盖"
    return f"\n\n---\n\n{header}\n{brand_voice.strip()}"

# ── Shared label maps ───────────────────────────────────────────────

KNOWLEDGE_TYPE_LABELS_ZH: dict[str, str] = {
    "selling_point": "核心卖点",
    "audience": "目标人群",
    "scenario": "适用场景",
    "pain_point": "痛点与变革动机",
    "faq": "常见问答",
    "objection": "异议应对",
    "proof": "信任背书",
    "brand": "品牌信息",
    "general": "其他知识",
}

KNOWLEDGE_TYPE_LABELS_EN: dict[str, str] = {
    "selling_point": "Core Selling Points",
    "audience": "Target Audience",
    "scenario": "Usage Scenarios",
    "pain_point": "Pain & Trigger",
    "faq": "FAQ",
    "objection": "Objection Handling",
    "proof": "Social Proof",
    "brand": "Brand Info",
    "general": "General",
}

OBJECTIVE_LABELS_ZH: dict[str, str] = {
    "reach_growth": "涨粉",
    "lead_generation": "拿线索",
    "conversion": "卖货转化",
    "education": "知识分享",
    "traffic_redirect": "引流直播间",
    "other": "其他",
}

# ── Reusable formatting instructions ────────────────────────────────

JSON_ONLY_ZH = "只返回 JSON，不要有其他文字。"
JSON_ONLY_EN = "Return JSON only, no other text."


# ── Offer context block ─────────────────────────────────────────────

def format_offer_summary(
    offer_data: dict[str, Any],
    *,
    language: str = "zh-CN",
) -> str:
    """Build the standard 'offer name / selling points / audiences / scenarios'
    block consumed by topic generation, knowledge inference, etc.
    """
    offer = offer_data.get("offer", {})
    name = offer.get("name", "商品" if language.startswith("zh") else "Product")
    desc = offer.get("description", "")
    selling_points = offer_data.get("selling_points", [])
    audiences = offer_data.get("target_audiences", [])
    scenarios = offer_data.get("target_scenarios", [])

    if language.startswith("zh"):
        lines = [
            f"商品名称：{name}",
            f"商品描述：{desc or '暂无'}",
            f"核心卖点：{', '.join(selling_points) if selling_points else '暂无'}",
            f"目标人群：{', '.join(audiences) if audiences else '暂无'}",
            f"适用场景：{', '.join(scenarios) if scenarios else '暂无'}",
        ]
    else:
        lines = [
            f"Product name: {name}",
            f"Description: {desc or 'N/A'}",
            f"Core selling points: {', '.join(selling_points) if selling_points else 'N/A'}",
            f"Target audience: {', '.join(audiences) if audiences else 'N/A'}",
            f"Scenarios: {', '.join(scenarios) if scenarios else 'N/A'}",
        ]
    return "\n".join(lines)


# ── Knowledge block (grouped by type) ───────────────────────────────

def format_knowledge_grouped(
    knowledge_items: list[dict[str, Any]],
    *,
    language: str = "zh-CN",
    max_items: int = 15,
) -> str:
    """Group knowledge items by type and format as markdown sections.

    Used by KB QA prompt, topic generation, and knowledge inference.
    """
    if not knowledge_items:
        return ""

    from collections import defaultdict

    labels = KNOWLEDGE_TYPE_LABELS_ZH if language.startswith("zh") else KNOWLEDGE_TYPE_LABELS_EN
    grouped: dict[str, list[dict]] = defaultdict(list)
    for k in knowledge_items[:max_items]:
        grouped[k.get("knowledge_type", "general")].append(k)

    sections: list[str] = []
    for ktype, items in grouped.items():
        label = labels.get(ktype, ktype)
        lines = [
            f"  - 【{k.get('title', '')}】{k.get('content_raw', '')}"
            for k in items
        ]
        sections.append(f"### {label}\n" + "\n".join(lines))

    return "\n\n".join(sections)


def format_knowledge_flat(
    knowledge_items: list[dict[str, Any]],
    *,
    language: str = "zh-CN",
    max_items: int = 15,
) -> str:
    """Format knowledge as a flat list (for topic generation context)."""
    if not knowledge_items:
        return ""
    is_en = language.startswith("en")
    lines = [
        f"- [{k.get('knowledge_type', 'general')}] {k.get('title', '')}: {k.get('content_raw', '')}"
        for k in knowledge_items[:max_items]
    ]
    header = "\nKnowledge base:" if is_en else "\n知识库："
    return header + "\n" + "\n".join(lines)


# ── Knowledge ranking for strategy focus ─────────────────────────────

# Weight map: marketing_objective → knowledge_type → weight (0.0–1.0)
# Higher weight = more relevant to the objective
# pain_point weighting rationale:
#   - lead_generation / conversion: pain is the emotional driver for action → high
#   - education: pain sets up the "problem" that content teaches around → high
#   - reach_growth: pain is secondary to aspirational framing → moderate
#   - traffic_redirect: pain hook matters for click-through → moderate
_OBJECTIVE_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "reach_growth":      {"selling_point": 1.0, "brand": 0.9, "scenario": 0.7, "audience": 0.6, "pain_point": 0.5, "proof": 0.4, "faq": 0.2, "objection": 0.1, "general": 0.1},
    "lead_generation":   {"selling_point": 0.9, "pain_point": 0.9, "scenario": 0.8, "audience": 0.8, "proof": 0.7, "faq": 0.5, "objection": 0.4, "brand": 0.3, "general": 0.1},
    "conversion":        {"proof": 1.0, "objection": 0.9, "pain_point": 0.9, "selling_point": 0.8, "faq": 0.7, "scenario": 0.5, "audience": 0.4, "brand": 0.2, "general": 0.1},
    "education":         {"selling_point": 1.0, "faq": 0.9, "pain_point": 0.8, "scenario": 0.7, "proof": 0.5, "audience": 0.4, "brand": 0.3, "objection": 0.3, "general": 0.2},
    "traffic_redirect":  {"scenario": 0.9, "selling_point": 0.8, "audience": 0.7, "pain_point": 0.7, "proof": 0.6, "faq": 0.4, "brand": 0.3, "objection": 0.2, "general": 0.1},
    "other":             {"selling_point": 0.8, "audience": 0.6, "scenario": 0.6, "pain_point": 0.6, "proof": 0.5, "faq": 0.5, "objection": 0.4, "brand": 0.4, "general": 0.2},
}

# Default weights when objective is unknown or missing
_DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    "selling_point": 0.8, "audience": 0.6, "scenario": 0.6,
    "pain_point": 0.6, "proof": 0.5, "faq": 0.5, "objection": 0.4,
    "brand": 0.4, "general": 0.2,
}

_CJK_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z]+")


def _tokenize(text: str) -> set[str]:
    """Simple character n-gram + word tokenizer for Chinese/English text."""
    if not text:
        return set()
    tokens: set[str] = set()
    # Extract CJK character bigrams and English words
    for m in _CJK_WORD_RE.finditer(text.lower()):
        word = m.group()
        tokens.add(word)
        # Add bigrams for CJK (poor-man's segmentation)
        if ord(word[0]) >= 0x4E00:
            for i in range(len(word) - 1):
                tokens.add(word[i : i + 2])
    return tokens


def rank_knowledge_for_strategy(
    knowledge_items: list[dict[str, Any]],
    *,
    marketing_objective: str | None = None,
    audience_segment: str | None = None,
    scenario: str | None = None,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    """Rank and filter knowledge items by relevance to a strategy unit.

    Scoring:
    - Base score (0-1): knowledge_type weight based on marketing_objective
    - Text bonus (0-0.5): token overlap between item content and
      audience_segment / scenario text
    """
    if not knowledge_items:
        return []
    if len(knowledge_items) <= max_items and not marketing_objective:
        return knowledge_items

    type_weights = _OBJECTIVE_TYPE_WEIGHTS.get(
        marketing_objective or "", _DEFAULT_TYPE_WEIGHTS
    )

    # Build context token set from audience + scenario
    context_tokens = _tokenize(audience_segment or "") | _tokenize(scenario or "")

    scored: list[tuple[float, int, dict]] = []
    for idx, ki in enumerate(knowledge_items):
        ktype = ki.get("knowledge_type", "general")
        base_score = type_weights.get(ktype, 0.2)

        # Text relevance bonus
        text_bonus = 0.0
        if context_tokens:
            item_text = f"{ki.get('title', '')} {ki.get('content_raw', '')}"
            item_tokens = _tokenize(item_text)
            if item_tokens:
                overlap = len(context_tokens & item_tokens)
                text_bonus = min(overlap / max(len(context_tokens), 1) * 0.5, 0.5)

        scored.append((base_score + text_bonus, -idx, ki))  # -idx for stable sort

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in scored[:max_items]]


def rank_knowledge_for_external_context(
    knowledge_items: list[dict[str, Any]],
    external_context_text: str,
    *,
    max_items: int = 18,
) -> list[dict[str, Any]]:
    """Rank KB items by token overlap with an external trend / hot-topic
    blob. Used by topic_studio's trend-bridge mode so we don't ship every
    KB row to the LLM when only a third of them connect to the trend.

    No-op (returns the input) when context is empty or the list is
    already short enough — the caller doesn't need to branch.

    The 0.4 floor on the overlap multiplier means even items that don't
    match a single trend keyword still rank by recency (preserves stable
    sort fallback) — important so the LLM still has anchor diversity if
    the keyword overlap is sparse.
    """
    ctx = (external_context_text or "").strip()
    if not ctx or not knowledge_items or len(knowledge_items) <= max_items:
        return knowledge_items

    ctx_tokens = _tokenize(ctx)
    if not ctx_tokens:
        return knowledge_items[:max_items]

    scored: list[tuple[float, int, dict]] = []
    for idx, ki in enumerate(knowledge_items):
        item_text = f"{ki.get('title', '')} {ki.get('content_raw', '')}"
        item_tokens = _tokenize(item_text)
        if not item_tokens:
            score = 0.4
        else:
            overlap = len(ctx_tokens & item_tokens)
            score = 0.4 + min(overlap / max(len(ctx_tokens), 1), 1.0) * 0.6
        scored.append((score, -idx, ki))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in scored[:max_items]]


# ── Strategy unit focus block ────────────────────────────────────────

OBJECTIVE_LABELS_EN: dict[str, str] = {
    "reach_growth": "Audience Growth",
    "lead_generation": "Lead Generation",
    "conversion": "Conversion",
    "education": "Education",
    "traffic_redirect": "Drive Traffic",
    "other": "Other",
}


def format_strategy_focus(
    su: dict[str, Any],
    *,
    language: str = "zh-CN",
) -> str:
    """Build the strategy focus block for topic generation."""
    if not su:
        return ""
    is_en = language.startswith("en")
    obj_labels = OBJECTIVE_LABELS_EN if is_en else OBJECTIVE_LABELS_ZH
    parts: list[str] = []
    if su.get("name"):
        parts.append(f"{'Strategy: ' if is_en else '策略名称：'}{su['name']}")
    objective = su.get("marketing_objective")
    if objective:
        parts.append(f"{'Objective: ' if is_en else '营销目标：'}{obj_labels.get(objective, objective)}")
    if su.get("notes"):
        parts.append(f"{'Notes: ' if is_en else '策略备注：'}{su['notes']}")
    if not parts:
        return ""
    header = "\n[Strategy Focus]" if is_en else "\n【本次策略聚焦】"
    return header + "\n" + "\n".join(parts)


# ── Asset context block ────────────────────────────────────────────────

_ASSET_TYPE_LABELS_ZH: dict[str, str] = {
    "image": "图片", "video": "视频", "audio": "音频",
    "document": "文档", "url": "链接", "copy": "文案",
}

_MAX_ASSET_CONTENT_CHARS = 200


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute from ORM object or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def format_asset_context(
    assets: list[Any],
    *,
    language: str = "zh-CN",
    max_items: int = 5,
) -> str:
    """Format asset content (tags, transcript, content_text) as supplementary context.

    Only includes assets that have meaningful extracted content (tags or text).
    Accepts both ORM objects and dicts.
    Budget: max_items assets × _MAX_ASSET_CONTENT_CHARS per item.
    """
    if not assets:
        return ""

    is_en = language.startswith("en")
    entries: list[str] = []

    for asset in assets:
        if len(entries) >= max_items:
            break

        parts: list[str] = []
        # Asset type + filename
        atype = _get(asset, "asset_type", "")
        fname = _get(asset, "file_name", "") or _get(asset, "title", "") or ""
        type_label = atype if is_en else _ASSET_TYPE_LABELS_ZH.get(atype, atype)

        # Tags (structured AI extraction)
        tags = _get(asset, "tags_json")
        if tags and isinstance(tags, dict):
            tag_parts = []
            for key in ("subject", "selling_point", "scenario", "usage"):
                val = tags.get(key)
                if val:
                    if isinstance(val, list):
                        tag_parts.append(f"{key}: {', '.join(str(v) for v in val)}")
                    else:
                        tag_parts.append(f"{key}: {val}")
            if tag_parts:
                parts.append("; ".join(tag_parts))

        # content_text (for copy-type assets)
        content = _get(asset, "content_text")
        if content:
            parts.append(content[:_MAX_ASSET_CONTENT_CHARS])

        # Slice transcripts (for video/audio)
        slices = _get(asset, "slices")
        if slices:
            for s in slices[:2]:  # max 2 slices per asset
                transcript = _get(s, "transcript")
                summary = _get(s, "summary")
                text = transcript or summary
                if text and not text.startswith("Image "):
                    parts.append(text[:_MAX_ASSET_CONTENT_CHARS])
                    break  # one meaningful slice is enough

        if parts:
            content_str = " | ".join(parts)
            entries.append(f"- [{type_label}] {fname}: {content_str}")

    if not entries:
        return ""

    header = "\nAsset references:" if is_en else "\n素材参考："
    return header + "\n" + "\n".join(entries)


# ── Offer context for asset tagging ──────────────────────────────────

def format_offer_for_tagging(
    offer_context: dict[str, Any],
    *,
    language: str = "zh-CN",
) -> str:
    """Build the product context section injected into asset tagging prompts."""
    if not offer_context:
        return ""
    is_en = language.startswith("en")
    if is_en:
        return f"""
## Product Context
- Name: {offer_context.get('name', 'N/A')}
- Positioning: {offer_context.get('positioning', 'N/A')}
- Core selling points: {json.dumps(offer_context.get('core_selling_points', []), ensure_ascii=False)}
- Target scenarios: {json.dumps(offer_context.get('target_scenarios', []), ensure_ascii=False)}
- Target audience: {json.dumps(offer_context.get('target_audience', []), ensure_ascii=False)}
"""
    return f"""
## 商品上下文
- 名称：{offer_context.get('name', '未知')}
- 定位：{offer_context.get('positioning', '未知')}
- 核心卖点：{json.dumps(offer_context.get('core_selling_points', []), ensure_ascii=False)}
- 目标场景：{json.dumps(offer_context.get('target_scenarios', []), ensure_ascii=False)}
- 目标人群：{json.dumps(offer_context.get('target_audience', []), ensure_ascii=False)}
"""


# ── Closed-vocabulary enums for asset tagging ───────────────────────

def format_closed_vocab_for_tagging(language: str = "zh-CN") -> str:
    """Emit the valid `content_form` + `campaign_type` id catalogues for the
    vision-LLM asset-tagging prompt. AI must pick ids from these lists; the
    service layer filters out any id that isn't in the catalogue.
    """
    from app.application.campaign_types import list_campaign_types
    from app.application.content_forms import list_content_forms

    is_en = language.startswith("en")
    lang_key = "en" if is_en else "zh"

    cf_lines = [f"  - `{cf.id}` — {cf.localized_name(lang_key)}: {cf.localized_description(lang_key)}"
                for cf in list_content_forms()]
    ct_lines = [f"  - `{ct.id}` — {ct.localized_name(lang_key)}: {ct.localized_description(lang_key)}"
                for ct in list_campaign_types()]

    if is_en:
        return (
            "\n## Closed vocabulary — use exact ids only\n"
            "\n### content_form (pick 1, occasionally 2 — describes the production form)\n"
            + "\n".join(cf_lines)
            + "\n\n### campaign_type (pick 0-2 — only if a promotional mechanic is visibly featured; "
              "leave empty for brand/lifestyle/product-demo assets without promo)\n"
            + "\n".join(ct_lines)
            + "\n"
        )
    return (
        "\n## 受控词典 —— 必须使用以下 id 原文\n"
        "\n### content_form（选 1，偶尔 2 —— 描述内容产出形态）\n"
        + "\n".join(cf_lines)
        + "\n\n### campaign_type（选 0-2 —— 只有画面里明显体现促销机制时才选；"
          "品牌/生活方式/纯产品展示类素材留空）\n"
        + "\n".join(ct_lines)
        + "\n"
    )


# ── Existing-knowledge dedup block ───────────────────────────────────

def format_existing_knowledge(
    knowledge_items: list[dict[str, Any]],
    *,
    language: str = "zh-CN",
    max_items: int = 50,
) -> str:
    """Format existing knowledge for dedup in the infer-knowledge prompt.

    Cap of 50 is intentional: after the how-to FAQ extraction upgrade a mature
    offer KB typically holds 25-45 items, and silently truncating at the old
    15 item limit caused the LLM to regenerate content it couldn't see,
    producing near-duplicates on ~20 items.
    """
    if not knowledge_items:
        return ""
    is_en = language.startswith("en")
    lines = [
        f"- [{k.get('knowledge_type')}] {k.get('title')}: {k.get('content_raw', '')}"
        for k in knowledge_items[:max_items]
    ]
    header = "\n\nExisting entries (do NOT repeat):\n" if is_en else "\n\n已有知识（不要重复生成）：\n"
    return header + "\n".join(lines)
