"""Modular prompt building blocks.

Every AI method in the system assembles its prompt from these shared
building blocks, ensuring consistent formatting and easy updates.
"""
from __future__ import annotations

import json
import re
from typing import Any

# ── Shared label maps ───────────────────────────────────────────────

KNOWLEDGE_TYPE_LABELS_ZH: dict[str, str] = {
    "selling_point": "核心卖点",
    "audience": "目标人群",
    "scenario": "适用场景",
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
    "faq": "FAQ",
    "objection": "Objection Handling",
    "proof": "Social Proof",
    "brand": "Brand Info",
    "general": "General",
}

OBJECTIVE_LABELS_ZH: dict[str, str] = {
    "awareness": "品牌曝光",
    "conversion": "促进转化",
    "lead_generation": "线索获取",
    "education": "产品教育",
    "trust_building": "建立信任",
    "retention": "用户留存",
    "launch": "新品上市",
    "branding": "品牌塑造",
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
_OBJECTIVE_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "awareness":      {"selling_point": 1.0, "brand": 0.9, "scenario": 0.7, "audience": 0.6, "proof": 0.3, "faq": 0.2, "objection": 0.1, "general": 0.1},
    "conversion":     {"proof": 1.0, "objection": 0.9, "selling_point": 0.8, "faq": 0.7, "scenario": 0.5, "audience": 0.4, "brand": 0.2, "general": 0.1},
    "lead_generation":{"selling_point": 0.9, "scenario": 0.8, "audience": 0.8, "proof": 0.7, "faq": 0.5, "objection": 0.4, "brand": 0.3, "general": 0.1},
    "education":      {"selling_point": 1.0, "faq": 0.9, "scenario": 0.7, "proof": 0.5, "audience": 0.4, "brand": 0.3, "objection": 0.3, "general": 0.2},
    "trust_building": {"proof": 1.0, "brand": 0.9, "faq": 0.7, "objection": 0.6, "selling_point": 0.4, "audience": 0.3, "scenario": 0.3, "general": 0.1},
    "retention":      {"scenario": 0.9, "faq": 0.8, "selling_point": 0.7, "audience": 0.6, "proof": 0.5, "brand": 0.4, "objection": 0.3, "general": 0.2},
    "launch":         {"selling_point": 1.0, "brand": 0.8, "scenario": 0.7, "audience": 0.6, "proof": 0.5, "faq": 0.4, "objection": 0.3, "general": 0.2},
    "branding":       {"brand": 1.0, "proof": 0.8, "selling_point": 0.6, "audience": 0.5, "scenario": 0.4, "faq": 0.2, "objection": 0.2, "general": 0.1},
}

# Default weights when objective is unknown or missing
_DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    "selling_point": 0.8, "audience": 0.6, "scenario": 0.6,
    "proof": 0.5, "faq": 0.5, "objection": 0.4,
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


# ── Strategy unit focus block ────────────────────────────────────────

OBJECTIVE_LABELS_EN: dict[str, str] = {
    "awareness": "Brand Awareness",
    "conversion": "Conversion",
    "lead_generation": "Lead Generation",
    "education": "Product Education",
    "trust_building": "Trust Building",
    "retention": "User Retention",
    "launch": "Product Launch",
    "branding": "Branding",
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


# ── Existing-knowledge dedup block ───────────────────────────────────

def format_existing_knowledge(
    knowledge_items: list[dict[str, Any]],
    *,
    max_items: int = 15,
) -> str:
    """Format existing knowledge for dedup in the infer-knowledge prompt.
    Uses English header since infer_knowledge prompt is English."""
    if not knowledge_items:
        return ""
    lines = [
        f"- [{k.get('knowledge_type')}] {k.get('title')}: {k.get('content_raw', '')}"
        for k in knowledge_items[:max_items]
    ]
    return "\n\nExisting entries (do NOT repeat):\n" + "\n".join(lines)
