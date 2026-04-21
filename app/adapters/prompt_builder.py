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
    max_items: int = 15,
) -> str:
    """Format existing knowledge for dedup in the infer-knowledge prompt."""
    if not knowledge_items:
        return ""
    is_en = language.startswith("en")
    lines = [
        f"- [{k.get('knowledge_type')}] {k.get('title')}: {k.get('content_raw', '')}"
        for k in knowledge_items[:max_items]
    ]
    header = "\n\nExisting entries (do NOT repeat):\n" if is_en else "\n\n已有知识（不要重复生成）：\n"
    return header + "\n".join(lines)
