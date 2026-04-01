"""Modular prompt building blocks.

Every AI method in the system assembles its prompt from these shared
building blocks, ensuring consistent formatting and easy updates.
"""
from __future__ import annotations

import json
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
