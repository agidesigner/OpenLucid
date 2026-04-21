from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.adapters.prompt_builder import (
    KNOWLEDGE_TYPE_LABELS_ZH,
    OBJECTIVE_LABELS_ZH,
    format_asset_context,
    format_closed_vocab_for_tagging,
    format_existing_knowledge,
    format_knowledge_flat,
    format_knowledge_grouped,
    format_offer_for_tagging,
    format_offer_summary,
    format_strategy_focus,
    rank_knowledge_for_strategy,
)

logger = logging.getLogger(__name__)


def _extract_thinking(text: str) -> tuple[str, str]:
    """Extract <think>...</think> content from LLM output.
    Returns (thinking_text, remaining_text). thinking_text is empty if no think block found.
    """
    import re
    match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    if not match:
        return "", text
    thinking = match.group(1).strip()
    remaining = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return thinking, remaining


def _build_infer_knowledge_system_prompt(language: str) -> str:
    is_en = language.startswith("en")
    if is_en:
        return """You are an expert marketing analyst. Based on the product/service information provided, generate a comprehensive knowledge base.

First, write a cleaned-up product description in the "description" field: remove navigation menus, footers, ads, boilerplate, and irrelevant UI text, but KEEP all product-related details — features, specs, pricing, case studies, customer quotes, competitive advantages. Aim for comprehensive coverage within 2000 characters.

Then generate 2-4 entries for each of the following 7 categories:
1. selling_point: differentiators, technical highlights, user value — MUST be written as a Before-FABE causal chain (see below)
2. audience: user personas, traits, purchase motivations
3. scenario: use cases, discovery moments (objective contexts — the concrete "when/where")
4. pain_point: usage pain + migration trigger (subjective suffering in those scenarios + why change now; distinct from purchase objection)
5. faq: factual/informational questions customers ask (e.g. warranty, compatibility, pricing specifics) — answer with facts
6. objection: emotional purchase hesitations (too expensive, untrusted, not-for-me) — answer with reframes + evidence; distinct from pain_point (usage pain) and from faq (factual questions)
7. proof: verifiable trust endorsements reusable across all content — case studies, user data, awards, press mentions, certifications, celebrity/expert endorsements. Distinct from a selling_point's own "Evidence" line (which is scoped to supporting that single selling_point); proof entries are standalone assets any content piece can cite.

Before-FABE STRUCTURE FOR selling_point (IMPORTANT):
Each selling_point's `content_raw` MUST follow this 5-line causal chain so downstream content generators can reason about the "before → after" transformation:

  Before: <the old solution / status quo being replaced; write "—" if none applies>
  Feature: <HOW the product achieves it — specific mechanism, component, or system>
  Advantage: <WHAT capability this mechanism enables under real use>
  Benefit: <WHAT concrete outcome the user actually gets>
  Evidence: <validation — usage data, case, testimonial; write "—" if none available>

Feature must describe the mechanism, NOT just restate what the product is. For example:
  BAD:  "Feature: AI-powered app"  ← just restates the product
  GOOD: "Feature: on-device neural engine + dynamic power scheduling + vapor-chamber cooling"
         ← explains HOW

Advantage is the capability unlocked by the Feature, still product-side.
Benefit is the outcome from the USER's perspective — what changes in their life or workflow.
Before describes the typical prior state (competitor behavior / old workflow / status quo); it gives downstream content generators an explicit contrast anchor.
Evidence should link a concrete proof point; if none, write "—".

STRUCTURE FOR pain_point:
Each pain_point's `content_raw` SHOULD be written as two labeled paragraphs:

  Pain: <the concrete, current suffering in the usage context — 2-4 bullet items>
  Trigger: <why they must change now, not tomorrow — the emotional / risk accumulation that makes inaction no longer tolerable>

Example:
  Pain:
    - Phone throttles under heavy load during business calls
    - Battery drains before a 2-hour flight ends
    - Device gets uncomfortably hot to hold
  Trigger: Dropping a client call at a critical moment is unacceptable in a B2B context; the cost of one embarrassing failure exceeds a phone upgrade price.

Return strictly valid JSON:
{
  "description": "Cleaned product description with all relevant details (up to 2000 chars)...",
  "selling_point": [{"title": "...", "content_raw": "Before: ...\\nFeature: ...\\nAdvantage: ...\\nBenefit: ...\\nEvidence: ...", "confidence": 0.9}, ...],
  "audience": [...],
  "scenario": [...],
  "pain_point": [{"title": "...", "content_raw": "Pain:\\n  - ...\\nTrigger: ...", "confidence": 0.9}, ...],
  "faq": [...],
  "objection": [...],
  "proof": [{"title": "...", "content_raw": "...", "confidence": 0.9}, ...]
}

Rules:
- Each entry must be specific and actionable, not generic
- confidence: your certainty about the inference (0-1)
- CRITICAL: If existing knowledge entries are provided below, do NOT generate entries that cover the same topic, even with different wording. Only generate entries for genuinely NEW information not already covered. If all dimensions are well covered, return empty arrays.
- IMPORTANT: Write all description, title, and content_raw values in English.
- Return JSON only, no other text
- Do NOT include any thinking or reasoning process in your response. Output the JSON directly."""

    return """你是一名资深营销分析师。请根据提供的商品/服务信息，生成一份完整的营销知识库。

首先，在 "description" 字段中写一段清洗后的商品描述：去掉导航、页脚、广告、模板化文案和无关界面文字，但要保留所有与商品有关的关键信息，例如功能、规格、价格、案例、用户评价、竞争优势等。尽量完整，控制在 2000 字符以内。

然后为以下 7 类知识分别生成 2-4 条候选：
1. selling_point：差异化卖点、技术亮点、用户价值 —— **必须写成 Before-FABE 因果链结构**（见下文）
2. audience：目标人群画像、特征、购买动机
3. scenario：使用场景、发现时刻（客观语境 —— 具体"何时何地"）
4. pain_point：使用痛点 + 变革动机（在该场景下的主观痛苦 + 为什么现在必须改；**和 objection 不同**，objection 是购买决策异议，pain_point 是使用本身的痛）
5. faq：事实型常见问题（保修、兼容性、具体价格、服务范围等）—— 用事实作答
6. objection：情绪型购买异议（"太贵了"、"怕上当"、"不适合我"）—— 用重构 + 证据作答；**和 pain_point 区分**（pain_point 是使用痛），**也和 faq 区分**（faq 是找信息）
7. proof：可跨内容复用的信任背书 —— 案例、用户数据、奖项、权威媒体报道、资质认证、专家/明星代言等。**与 selling_point 内部的 Evidence 行不同**：Evidence 只支撑某一个具体卖点，proof 是独立资产，任何文案都可引用。

**selling_point 的 Before-FABE 结构（重要）**：
每条 selling_point 的 `content_raw` 字段**必须**按下面 5 行因果链写，以便下游内容生成器能做"之前 → 之后"对比叙事：

  Before：<被替代的旧方案 / 现状；若无明显参照，写 "—">
  Feature：<产品是怎么做到的 —— 具体机制、核心组件、系统协作>
  Advantage：<这个机制能在真实使用中带来什么能力>
  Benefit：<用户最终得到的具体结果/感知>
  Evidence：<验证依据 —— 用户数据、案例、背书；如暂无请写 "—">

**Feature 必须描述"机制"，不是复述"产品是什么"**。例如：
  ❌ 错："Feature：AI 数字人生成平台" ← 只是在复述产品是什么
  ✅ 对："Feature：自研 3D 面捕引擎 + 多模态 TTS + 跨平台唇形对齐算法"
         ← 解释"怎么做到"

Advantage 是 Feature 解锁的能力（仍是产品侧语言）。
Benefit 是用户视角的结果 —— 他的工作/生活/心情发生了什么变化。
Before 描述替代对象（竞品行为 / 旧工作流 / 现状），给下游生成器一个明确的对比锚点。
Evidence 尽量指向一个具体证据点；若无，写 "—"。

**pain_point 的结构**：
每条 pain_point 的 `content_raw` **建议**写成两段带标签的文字：

  Pain：<在使用语境下当前具体的痛苦 —— 2-4 条列点>
  Trigger：<为什么现在必须改而不是继续忍 —— 情感积累 / 风险临界 / 成本对比>

示例：
  Pain：
    - 手机高负载下严重发热
    - 2 小时视频会议撑不过电量
    - 拿在手里烫手
  Trigger：在 B2B 商务场合掉线一次客户会议，代价远超换一部手机；不能继续忍。

严格返回合法 JSON：
{
  "description": "清洗后的商品描述（最多 2000 字符）",
  "selling_point": [{"title": "...", "content_raw": "Before：...\\nFeature：...\\nAdvantage：...\\nBenefit：...\\nEvidence：...", "confidence": 0.9}, ...],
  "audience": [...],
  "scenario": [...],
  "pain_point": [{"title": "...", "content_raw": "Pain：\\n  - ...\\nTrigger：...", "confidence": 0.9}, ...],
  "faq": [...],
  "objection": [...],
  "proof": [{"title": "...", "content_raw": "...", "confidence": 0.9}, ...]
}

规则：
- 每条内容都要具体、可执行，避免空泛
- confidence 表示你对该推断的把握程度（0-1）
- 如果下面已经提供了已有知识，请不要生成语义重复的内容；只有在确实补充了新信息时才生成新条目；如果各维度都已覆盖，可以返回空数组
- IMPORTANT: Write all description, title, and content_raw values in Chinese.
- 只返回 JSON，不要输出其他文字
- 不要输出思考过程，直接输出 JSON。"""


class AIAdapter(ABC):
    last_thinking: str | None = None  # populated after calls that produce <think> blocks

    @abstractmethod
    async def summarize_offer_context(self, offer_data: dict[str, Any]) -> dict[str, Any]:
        """Summarize offer context for topic generation."""

    @abstractmethod
    async def generate_topic_plans(
        self,
        offer_context: dict[str, Any],
        count: int = 5,
        channel: str | None = None,
        language: str = "zh-CN",
        strategy_unit_context: dict[str, Any] | None = None,
        existing_titles: list[str] | None = None,
        liked_titles: list[dict[str, str]] | None = None,
        disliked_titles: list[dict[str, str]] | None = None,
        user_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate topic plan candidates. Each dict should contain:
        title, angle, hook, key_points, target_audience, target_scenario,
        channel, source_mode, score_relevance, score_conversion, score_asset_readiness,
        recommended_asset_ids.
        """

    @abstractmethod
    async def extract_asset_tags(
        self,
        asset_metadata: dict[str, Any],
        image_path: str | None = None,
        offer_context: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        """Extract structured tags from asset metadata, optionally with a visual thumbnail and offer context."""

    @abstractmethod
    async def extract_knowledge_from_text(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        """Extract structured knowledge from raw text."""

    @abstractmethod
    async def infer_knowledge(
        self, offer_data: dict[str, Any], language: str = "zh-CN", user_hint: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Infer knowledge suggestions grouped by category.
        Returns dict with keys: selling_point, audience, scenario, pain_point, faq, objection, proof.
        Each value is a list of {title, content_raw, confidence}."""

    @abstractmethod
    async def answer_from_knowledge(
        self,
        question: str,
        knowledge_items: list[dict[str, Any]],
        style_prompt: str,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        """Answer a question strictly based on provided knowledge items.
        Returns {answer, referenced_titles: [str], has_relevant_knowledge: bool}."""

    @abstractmethod
    async def extract_brandkit_profiles(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        """Extract brand specification profiles from text (website/document content).
        Returns dict with 7 profile fields, each value is a JSON-serializable object or null."""

    @abstractmethod
    async def infer_offer_model(self, name: str, description: str, offer_type: str) -> str:
        """Infer the offer_model (delivery sub-type) from offer name, description and type.
        Returns one of: physical_product, digital_product, local_service, professional_service, package, solution."""


class StubAIAdapter(AIAdapter):
    """Context-aware stub that generates topic plans from offer context data."""

    async def summarize_offer_context(self, offer_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": f"Context for offer: {offer_data.get('offer', {}).get('name', 'unknown')}",
            "selling_points_count": len(offer_data.get("selling_points", [])),
            "knowledge_count": len(offer_data.get("knowledge_items", [])),
            "asset_count": len(offer_data.get("assets", [])),
        }

    async def generate_topic_plans(
        self,
        offer_context: dict[str, Any],
        count: int = 5,
        channel: str | None = None,
        language: str = "zh-CN",
        strategy_unit_context: dict[str, Any] | None = None,
        existing_titles: list[str] | None = None,
        liked_titles: list[dict[str, str]] | None = None,
        disliked_titles: list[dict[str, str]] | None = None,
        user_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        offer = offer_context.get("offer", {})
        offer_name = offer.get("name", "Product")
        selling_points = offer_context.get("selling_points", [])
        # Use strategy unit's focused audience/scenario if available
        su = strategy_unit_context or {}
        audiences = ([su["audience_segment"]] if su.get("audience_segment") else None) or offer_context.get("target_audiences", [])
        scenarios = ([su["scenario"]] if su.get("scenario") else None) or offer_context.get("target_scenarios", [])
        assets = offer_context.get("assets", [])
        asset_ids = [a.get("id") or str(a.get("id", "")) for a in assets[:5]]

        # Generate diverse topic angles based on available context
        templates = [
            {
                "angle": "selling_point",
                "title_prefix": "Why",
                "hook_template": "Did you know {offer} can {point}?",
            },
            {
                "angle": "scenario",
                "title_prefix": "How to use",
                "hook_template": "Transform your {scenario} with {offer}",
            },
            {
                "angle": "audience",
                "title_prefix": "For",
                "hook_template": "Attention {audience}: {offer} is here",
            },
            {
                "angle": "comparison",
                "title_prefix": "Why choose",
                "hook_template": "3 reasons {offer} beats the competition",
            },
            {
                "angle": "testimonial",
                "title_prefix": "Real results with",
                "hook_template": "See what happened when they tried {offer}",
            },
        ]

        plans = []
        for i in range(count):
            tmpl = templates[i % len(templates)]
            point = selling_points[i % len(selling_points)] if selling_points else "save time"
            audience = audiences[i % len(audiences)] if audiences else "everyone"
            scenario = scenarios[i % len(scenarios)] if scenarios else "daily life"

            title = f"{tmpl['title_prefix']} {offer_name}: {point}" if tmpl["angle"] == "selling_point" else \
                    f"{tmpl['title_prefix']} {offer_name} in {scenario}" if tmpl["angle"] == "scenario" else \
                    f"{tmpl['title_prefix']} {audience}: {offer_name}" if tmpl["angle"] == "audience" else \
                    f"{tmpl['title_prefix']} {offer_name} over alternatives"

            hook = tmpl["hook_template"].format(
                offer=offer_name, point=point, audience=audience, scenario=scenario
            )

            plans.append({
                "title": title,
                "angle": tmpl["angle"],
                "hook": hook,
                "key_points": [point] + selling_points[:2] if selling_points else [point],
                "target_audience": [audience],
                "target_scenario": [scenario],
                "channel": channel or "general",
                "source_mode": "kb",
                "recommended_asset_ids": asset_ids[:3],
                "score_relevance": round(0.7 + (i % 3) * 0.1, 2),
                "score_conversion": round(0.6 + (i % 4) * 0.1, 2),
                "score_asset_readiness": round(min(len(assets) / max(count, 1), 1.0), 2),
            })

        return plans

    async def extract_asset_tags(
        self,
        asset_metadata: dict[str, Any],
        image_path: str | None = None,
        offer_context: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        return {"subject": [], "usage": [], "confidence": 0.0}

    async def extract_knowledge_from_text(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        return {"title": "Extracted knowledge", "content_structured": {}, "confidence": 0.0}

    async def answer_from_knowledge(
        self,
        question: str,
        knowledge_items: list[dict[str, Any]],
        style_prompt: str,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        is_en = language.startswith("en")
        if not knowledge_items:
            return {
                "answer": "No relevant content found in the knowledge base. Please add more entries and try again." if is_en else "知识库中暂无相关内容，建议补充知识后重试。",
                "referenced_titles": [],
                "has_relevant_knowledge": False,
            }
        titles = [k.get("title", "") for k in knowledge_items[:2]]
        content = knowledge_items[0].get('content_raw', '')
        return {
            "answer": f"Based on the knowledge base, regarding \"{question}\": {content}" if is_en else f"根据知识库内容，关于「{question}」的回答：{content}",
            "referenced_titles": titles,
            "has_relevant_knowledge": True,
        }

    async def extract_brandkit_profiles(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        raise RuntimeError("NO_LLM_CONFIGURED")

    async def infer_offer_model(self, name: str, description: str, offer_type: str) -> str:
        mapping = {
            "product": "physical_product",
            "service": "professional_service",
            "bundle": "package",
            "solution": "solution",
        }
        return mapping.get(offer_type, "physical_product")

    async def infer_knowledge(
        self, offer_data: dict[str, Any], language: str = "zh-CN", user_hint: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        name = offer_data.get("offer", {}).get("name", "商品")
        return {
            "selling_point": [
                {"title": f"{name}核心卖点", "content_raw": "高品质、高性价比", "confidence": 0.8},
            ],
            "audience": [
                {"title": "目标用户", "content_raw": "追求品质的年轻消费者", "confidence": 0.75},
            ],
            "scenario": [
                {"title": "使用场景", "content_raw": "日常生活场景", "confidence": 0.7},
            ],
            "pain_point": [
                {"title": "现有方案痛点", "content_raw": "Pain:\n  - 使用不便\nTrigger: 需要改变", "confidence": 0.6},
            ],
            "faq": [
                {"title": "常见问题", "content_raw": "产品保修多久？", "confidence": 0.7},
            ],
            "objection": [
                {"title": "价格疑虑", "content_raw": "对比同类产品性价比更高", "confidence": 0.65},
            ],
            "proof": [
                {"title": "用户好评", "content_raw": "多数用户给出 4 星以上评价", "confidence": 0.6},
            ],
        }


class OpenAICompatibleAdapter(AIAdapter):
    """Real AI adapter using any OpenAI-compatible API (MiniMax, DeepSeek, OpenAI, etc.)."""

    def __init__(self, api_key: str, base_url: str, model: str, extra_headers: dict | None = None, provider: str | None = None):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=extra_headers or {})
        self.model = model
        self.provider = provider or "unknown"

    async def _chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.8, max_tokens: int = 16384) -> str:
        import asyncio
        last_err = None
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                status = getattr(e, "status_code", None) or getattr(e, "status", 0)
                # Only retry on transient errors (network, 429, 5xx)
                if status and 400 <= status < 500 and status != 429:
                    raise
                if attempt < 2:
                    wait = (attempt + 1) * 2  # 2s, 4s
                    logger.warning("LLM call failed (attempt %d/3), retrying in %ds: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
        raise last_err  # type: ignore[misc]

    async def _chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> Any:
        """Call the LLM requesting JSON output; parses and returns the JSON object.

        Tries response_format=json_object first (supported by most modern models).
        Falls back to plain _chat() + _parse_json_response() if the model rejects it.
        """
        import asyncio

        # First attempt: use response_format for strict JSON
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            return self._parse_json_response(raw)
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", 0)
            # If the model doesn't support response_format, fall through to prompt-constrained mode
            if status and 400 <= status < 500 and status != 429:
                logger.info("_chat_json: response_format not supported (%s), falling back to prompt mode", e)
            else:
                raise

        # Fallback: prompt-constrained mode (append reminder to output only JSON)
        fallback_system = system_prompt + "\n\nREMINDER: Output ONLY valid JSON — no markdown, no explanation, no text before or after the JSON object."
        raw = await self._chat(fallback_system, user_prompt, temperature=temperature)
        return self._parse_json_response(raw)

    async def _chat_stream(self, system_prompt: str, user_prompt: str, temperature: float = 0.8,
                           timeout: float = 180, max_tokens: int = 16384):
        """Async generator that yields token strings as they arrive."""
        import asyncio
        last_err = None
        for attempt in range(3):
            try:
                deadline = asyncio.get_running_loop().time() + timeout
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                thinking_open = False
                async for chunk in stream:
                    if asyncio.get_running_loop().time() > deadline:
                        raise TimeoutError(f"LLM stream exceeded {timeout}s total timeout")
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue
                    # Some thinking models (Qwen3, DeepSeek-R1) stream reasoning in
                    # reasoning_content rather than content. Wrap in synthetic <think> tags
                    # so downstream state machines and _extract_thinking work uniformly.
                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        if not thinking_open:
                            yield "<think>"
                            thinking_open = True
                        yield rc
                    if delta.content:
                        if thinking_open:
                            yield "</think>"
                            thinking_open = False
                        yield delta.content
                if thinking_open:
                    yield "</think>"
                return  # stream completed successfully
            except Exception as e:
                last_err = e
                status = getattr(e, "status_code", None) or getattr(e, "status", 0)
                if status and 400 <= status < 500 and status != 429:
                    raise
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    logger.warning("LLM stream failed (attempt %d/3), retrying in %ds: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
        raise last_err  # type: ignore[misc]

    def _parse_json_response(self, text: str) -> Any:
        """Extract JSON from model response, handling think tags and code blocks."""
        import re

        original = text.strip()

        # Remove <think>...</think> blocks
        text = re.sub(r"<think>.*?</think>", "", original, flags=re.DOTALL).strip()
        # Handle unclosed <think> tag (response truncated before </think>)
        if "<think>" in text:
            text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()

        # Remove ```json or ``` wrapper
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try truncating at the last balanced brace (strips trailing prose)
        def _truncate_at_balanced_end(s: str) -> str:
            depth = 0
            in_str = False
            esc = False
            last_close = -1
            for i, ch in enumerate(s):
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch in "{[":
                    depth += 1
                elif ch in "}]":
                    depth -= 1
                    if depth == 0:
                        last_close = i
            return s[: last_close + 1] if last_close > 0 else s

        truncated = _truncate_at_balanced_end(text)
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

        # Fix trailing commas (common LLM error: {"a": 1,})
        trailing_comma_fixed = re.sub(r",(\s*[}\]])", r"\1", truncated)
        try:
            return json.loads(trailing_comma_fixed)
        except json.JSONDecodeError:
            pass

        # Try to find JSON array or object in the text
        match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Last resort: if text was empty after stripping think tags,
        # the model may have put JSON inside the think block or the
        # response was truncated. Try to find JSON in the original text.
        if not text and original:
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", original)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        raise ValueError(f"No valid JSON found in response ({len(original)} chars)")

    async def summarize_offer_context(self, offer_data: dict[str, Any]) -> dict[str, Any]:
        system = "You are an expert content marketing analyst. Analyze the given product/service information and extract key selling points and marketing opportunities. Respond in the same language as the input data."
        user = f"Analyze and summarize the following product information:\n{json.dumps(offer_data, ensure_ascii=False, indent=2)}"
        result = await self._chat(system, user)
        try:
            return self._parse_json_response(result)
        except (json.JSONDecodeError, ValueError):
            return {"summary": result}

    async def generate_topic_plans(
        self,
        offer_context: dict[str, Any],
        count: int = 5,
        channel: str | None = None,
        language: str = "zh-CN",
        strategy_unit_context: dict[str, Any] | None = None,
        existing_titles: list[str] | None = None,
        liked_titles: list[dict[str, str]] | None = None,
        disliked_titles: list[dict[str, str]] | None = None,
        user_instruction: str | None = None,
    ) -> list[dict[str, Any]]:
        offer = offer_context.get("offer", {})
        offer_name = offer.get("name", "商品")
        selling_points = offer_context.get("selling_points", [])
        knowledge_items = offer_context.get("knowledge_items", [])

        su = strategy_unit_context or {}
        focused_audience = su.get("audience_segment")
        focused_scenario = su.get("scenario")
        effective_channel = su.get("channel") or channel

        audiences = [focused_audience] if focused_audience else offer_context.get("target_audiences", [])
        scenarios = [focused_scenario] if focused_scenario else offer_context.get("target_scenarios", [])

        is_en = language.startswith("en")
        channel_desc = (f"Target platform: {effective_channel}" if is_en else f"目标平台：{effective_channel}") if effective_channel and effective_channel != "general" else ("General platform" if is_en else "通用平台")
        strategy_focus = format_strategy_focus(su, language=language)

        lang_instruction = "All output text (title, hook, key_points, etc.) MUST be in English." if is_en else "所有输出文本（title、hook、key_points 等）必须使用中文。"

        if is_en:
            viral_signals_block = """

## Viral Signals (Always Apply)
- Titles should NOT read like instructional copy ("How to X", "Tips for X") — write like a viral creator post
- Hooks must grab attention in the first 3 seconds — never neutral statements
- Prefer: contrast, suspense, emotion, numbers, comparison, first-person mistakes
- Avoid: standard marketing speak, official tone, adjective stacking
- Each title should contain a concrete visual or emotional cue"""
        else:
            viral_signals_block = """

## 网感要求（默认开启）
- title 不要写「教你 X」「分享 X」这种说明文风——要写成像朋友圈/小红书爆款标题
- hook 必须是前 3 秒能勾住的话，不能是中性陈述
- 优先使用：反差、悬念、情绪、数字、对比、第一人称踩坑
- 避免：标准营销话术、官腔、形容词堆砌
- 每个标题至少含 1 个具象画面或情绪词"""

        system = f"""You are a senior short-video content director skilled at planning viral content topics for products/services.
Generate highly relevant content topic plans based on the product info and strategy focus provided.

Requirements:
1. Each topic must have a unique angle — no duplicates
2. The hook must be attention-grabbing
3. key_points are production/shooting notes
4. Stay strictly aligned with the provided target audience and marketing objectives
5. Provide score_relevance (relevance to the product, 0-1) and score_conversion (estimated conversion potential, 0-1)
6. If existing topics are provided below, you MUST avoid repeating similar titles or angles — find fresh perspectives
7. If liked topics (👍) are provided, learn from their style, angle, and tone — generate more topics like them
8. If disliked topics (👎) are provided, avoid their style, angle, and approach
{viral_signals_block}

Return a strict JSON array. Each element:
{{
  "title": "topic title",
  "angle": "approach (e.g. selling point showcase / scenario seeding / pain point / comparison / real experience)",
  "hook": "opening hook",
  "key_points": ["point 1", "point 2", "point 3"],
  "target_audience": ["audience"],
  "target_scenario": ["scenario"],
  "channel": "channel",
  "source_mode": "kb",
  "score_relevance": 0.85,
  "score_conversion": 0.75,
  "score_asset_readiness": 0.5
}}

{lang_instruction}
Return JSON array only, no other text."""

        # Use strategy unit's linked knowledge items if provided, else all offer knowledge
        ki_list = su.get("knowledge_items") or knowledge_items
        # Rank and filter knowledge by relevance to strategy focus
        if su and ki_list:
            ki_list = rank_knowledge_for_strategy(
                ki_list,
                marketing_objective=su.get("marketing_objective"),
                audience_segment=focused_audience,
                scenario=focused_scenario,
            )
        knowledge_text = format_knowledge_flat(ki_list, language=language)

        # Asset context (supplementary)
        asset_items = offer_context.get("assets", [])
        asset_text = format_asset_context(asset_items, language=language)

        na = "N/A" if is_en else "暂无"

        # Build instruction intro/outro (only if user provided a creative brief).
        # Position: top + bottom of user message (sandwich the KB), so the brief
        # is the first and last thing the model sees. Works equally well across
        # strong (Claude/GPT-4) and weaker (Qwen, Llama) models because it relies
        # only on universal position-based attention, not model-specific phrasing.
        instruction_intro = ""
        instruction_outro = ""
        if user_instruction:
            if is_en:
                instruction_intro = f"""## Creative Brief
{user_instruction}

This brief is the primary intent of this request — it should shape the topics, not be treated as a side note.
- If the brief mentions external trends, platforms, tools, or events: interpret them in context and create authentic connections to the product
- If you're unfamiliar with a specific term, treat it as a current trending reference and find a semantic bridge — don't drop it, don't refuse

---

"""
                instruction_outro = f"""

Generate {count} topic plans that honor the Creative Brief above. The brief should be visible as the creative spine of the topics, not just a side mention."""
            else:
                instruction_intro = f"""## 创意指令
{user_instruction}

这条指令是本次请求的核心意图，应该塑造选题的主轴，而不是被当成附加说明。
- 如果指令提到外部热点、平台、工具或事件：先理解它的语境，再和商品建立真实可信的连接
- 如果你不熟悉某个具体名词，把它当成当下的热门话题，找到语义层面的桥梁——不要忽略，也不要拒绝

---

"""
                instruction_outro = f"""

请生成 {count} 个能体现上方「创意指令」的选题方案。指令应该作为选题的创作主轴可见，而不是顺带提一下。"""

        if user_instruction:
            tail = instruction_outro
        elif is_en:
            tail = f"\nGenerate {count} content topic plans that closely match the strategy focus above."
        else:
            tail = f"\n请生成 {count} 个高度契合以上策略聚焦的内容选题方案。"

        user = f"""{instruction_intro}{"Product: " if is_en else "商品名称："}{offer_name}
{"Core selling points: " if is_en else "核心卖点："}{', '.join(selling_points) if selling_points else na}
{"Target audience: " if is_en else "目标人群："}{', '.join(audiences) if audiences else na}
{"Scenarios: " if is_en else "适用场景："}{', '.join(scenarios) if scenarios else na}
{channel_desc}{strategy_focus}
{knowledge_text}
{asset_text}
{self._format_existing_titles(existing_titles, is_en)}{self._format_rated_titles(liked_titles, disliked_titles, is_en)}{tail}"""

        logger.info(
            "Generating %d topic plans for offer '%s'%s via %s",
            count, offer_name,
            f" (strategy_unit={su.get('name', su.get('id', ''))})" if su else "",
            self.provider,
        )

        result = await self._chat(system, user)
        thinking, clean_result = _extract_thinking(result)
        self.last_thinking = thinking or None
        try:
            plans = self._parse_json_response(clean_result)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse LLM topic plans response as JSON: %s", clean_result[:500])
            raise ValueError(f"LLM returned unparseable topic plans for offer '{offer_name}'")

        for plan in plans:
            if not plan.get("channel"):
                plan["channel"] = effective_channel or "general"

        return plans[:count]

    @staticmethod
    def _format_existing_titles(titles: list[str] | None, is_en: bool) -> str:
        if not titles:
            return ""
        capped = titles[:50]
        header = "\nExisting topics (DO NOT repeat these):" if is_en else "\n已有选题（不要重复以下主题）："
        items = "\n".join(f"- {t}" for t in capped)
        return f"{header}\n{items}"

    @staticmethod
    def _format_rated_titles(
        liked: list[dict[str, str]] | None,
        disliked: list[dict[str, str]] | None,
        is_en: bool,
    ) -> str:
        """Format liked/disliked topics with title + angle for richer signal."""
        parts: list[str] = []
        if liked:
            header = "\n👍 Liked topics (generate more like these):" if is_en else "\n👍 用户喜欢的选题风格（多生成类似的）："
            items = "\n".join(
                f"- {t['title']}" + (f" [{t['angle']}]" if t.get('angle') else "")
                for t in liked[:20]
            )
            parts.append(f"{header}\n{items}")
        if disliked:
            header = "\n👎 Disliked topics (avoid this style):" if is_en else "\n👎 用户不喜欢的选题风格（避免类似的）："
            items = "\n".join(
                f"- {t['title']}" + (f" [{t['angle']}]" if t.get('angle') else "")
                for t in disliked[:20]
            )
            parts.append(f"{header}\n{items}")
        return "".join(parts)

    def _build_kb_qa_prompt(
        self,
        knowledge_items: list[dict[str, Any]],
        style_prompt: str,
        language: str = "zh-CN",
    ) -> str:
        """Build the system prompt for KB QA (shared by streaming and non-streaming)."""
        is_en = language.startswith("en")
        knowledge_text = format_knowledge_grouped(knowledge_items, language=language, max_items=len(knowledge_items))

        if is_en:
            return f"""{style_prompt}

## Strict Rules
1. Answer ONLY based on the Knowledge Base below — do NOT fabricate information
2. If the knowledge base has no relevant content, reply honestly: "No relevant content found in the knowledge base"
3. Put the titles of referenced entries in referenced_titles
4. Do NOT invent product features, prices, specs, or any factual claims
5. Be specific and informative — synthesize multiple relevant entries for a complete answer

## Knowledge Base
{knowledge_text if knowledge_text else '(empty)'}

## Output format (strict JSON, no other text)
{{"answer": "your answer", "referenced_titles": ["title1", "title2"], "has_relevant_knowledge": true/false}}"""
        else:
            return f"""{style_prompt}

## 严格约束规则
1. 只能基于下方【知识库】回答，不得编造知识库中不存在的信息
2. 知识库中无相关内容时，如实回答"知识库中暂无相关内容"
3. 回答中引用了哪些知识条目，把其标题放入 referenced_titles
4. 不得虚构产品功能、价格、参数等事实性信息
5. 回答要具体、有信息量，综合多条相关知识给出完整回答

## 知识库
{knowledge_text if knowledge_text else '（知识库为空）'}

## 输出格式（严格 JSON，不要输出其他文字）
{{"answer": "你的回答", "referenced_titles": ["引用的知识条目标题1", "标题2"], "has_relevant_knowledge": true/false}}"""

    async def answer_from_knowledge(
        self,
        question: str,
        knowledge_items: list[dict[str, Any]],
        style_prompt: str,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        import time as _time
        t0 = _time.monotonic()

        system = self._build_kb_qa_prompt(knowledge_items, style_prompt, language=language)

        logger.info("KB QA: system_prompt='%s…', knowledge=%d items",
                     system[:200], len(knowledge_items))

        result = await self._chat(system, question, temperature=0.3)
        elapsed = _time.monotonic() - t0
        thinking, clean_result = _extract_thinking(result)
        logger.info("KB QA: LLM responded in %.1fs, thinking=%d chars, raw output='%s…'",
                     elapsed, len(thinking), clean_result[:300])
        try:
            parsed = self._parse_json_response(clean_result)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse KB QA response: %s", clean_result[:500])
            return {
                "answer": clean_result,
                "referenced_titles": [],
                "has_relevant_knowledge": bool(knowledge_items),
                "thinking": thinking or None,
            }

        return {
            "answer": parsed.get("answer", clean_result),
            "referenced_titles": parsed.get("referenced_titles", []),
            "has_relevant_knowledge": parsed.get("has_relevant_knowledge", bool(knowledge_items)),
            "thinking": thinking or None,
        }

    async def extract_asset_tags(
        self,
        asset_metadata: dict[str, Any],
        image_path: str | None = None,
        offer_context: dict[str, Any] | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any]:
        is_en = language.startswith("en")
        existing_sample = asset_metadata.pop("existing_tags_sample", [])

        offer_section = format_offer_for_tagging(offer_context, language=language)
        closed_vocab_section = format_closed_vocab_for_tagging(language=language)

        existing_hint = ""
        if existing_sample:
            existing_hint = json.dumps(existing_sample[:30], ensure_ascii=False)

        if is_en:
            system = f"""You are an asset tag analyst. Extract structured marketing tags from asset information and product context.
{offer_section}{closed_vocab_section}
## Tag requirements
1. subject (content subject): specific objects/people/elements in the visual, 2-5 tags
2. usage (usage tags): marketing purpose of this asset, 1-3 tags
3. selling_point (selling point association): selling points this asset supports, **prefer exact phrases from core selling points above**, 1-3 tags (can be empty if asset is brand/generic and doesn't support a specific feature)
4. scenario (scenario association): scenarios this asset fits, **prefer exact phrases from target scenarios above**, 1-3 tags (can be empty)
5. channel_fit (channel fit): suitable platforms, 1-2 tags
6. content_form (production form): **pick 1-2 ids from the closed vocabulary above**, never invent new ids
7. campaign_type (promotional mechanic): **pick 0-2 ids from the closed vocabulary above, or empty** if no promotional mechanic is visibly featured

## Consistency requirements
- selling_point and scenario MUST reuse original text from the product context when applicable
- content_form / campaign_type MUST use ids from the closed vocabulary exactly — no paraphrasing, no new ids
- Reuse existing free-form tags when possible: {existing_hint or 'N/A'}
- Tag language (for free-form fields): English

Return JSON only:
{{"subject": [...], "usage": [...], "selling_point": [...], "scenario": [...], "channel_fit": [...], "content_form": [...], "campaign_type": [...], "hook_score": 0.8, "reuse_score": 0.7, "confidence": 0.9}}"""
        else:
            system = f"""你是素材标签分析师。根据素材信息和商品知识库，提取结构化营销标签。
{offer_section}{closed_vocab_section}
## 标签要求
1. subject（内容主体）：画面中的具体物体/人物/场景元素，2-5 个
2. usage（用途标签）：素材的营销用途，1-3 个
3. selling_point（卖点关联）：此素材能支持的卖点，**优先从上方核心卖点中选择**，1-3 个（如果素材是品牌/通用类，无明确卖点对应，可以留空）
4. scenario（场景关联）：此素材适配的场景，**优先从上方目标场景中选择**，1-3 个（可以留空）
5. channel_fit（渠道适配）：适合发布的平台，1-2 个
6. content_form（内容形态）：**从上方受控词典里选 1-2 个 id**，不允许发明新 id
7. campaign_type（促销机制）：**从上方受控词典里选 0-2 个 id，如画面无明显促销机制则留空**

## 一致性要求
- selling_point 和 scenario 必须优先复用商品知识库中的原文
- content_form / campaign_type 必须使用受控词典里的 id 原文——不允许近义替换、不允许发明新 id
- 其他自由标签尽量复用已有标签：{existing_hint or '无'}
- 自由标签语言：中文

仅返回 JSON：
{{"subject": [...], "usage": [...], "selling_point": [...], "scenario": [...], "channel_fit": [...], "content_form": [...], "campaign_type": [...], "hook_score": 0.8, "reuse_score": 0.7, "confidence": 0.9}}"""

        user_text = f"{'Asset metadata' if is_en else '素材元数据'}：\n{json.dumps(asset_metadata, ensure_ascii=False, indent=2)}"

        if image_path:
            try:
                result = await self._chat_vision(system, user_text, image_path, temperature=0.3)
            except Exception:
                logger.warning("Vision call failed, falling back to text-only tagging")
                result = await self._chat(system, user_text, temperature=0.3)
        else:
            result = await self._chat(system, user_text, temperature=0.3)

        try:
            return self._parse_json_response(result)
        except (json.JSONDecodeError, ValueError):
            return {"subject": [], "usage": [], "confidence": 0.0}

    async def _chat_vision(self, system_prompt: str, user_text: str, image_path: str, temperature: float = 0.8) -> str:
        """Send a chat request with an image (OpenAI vision API format)."""
        import base64
        import mimetypes

        mime, _ = mimetypes.guess_type(image_path)
        mime = mime or "image/jpeg"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text + "\n\nAnalyze tags based on the image content:"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def extract_knowledge_from_text(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        is_en = language.startswith("en")
        system = """You are a knowledge extraction expert. Extract structured knowledge from text.
Return JSON: {"title": "...", "content_structured": {"key": "value"}, "confidence": 0.9}
""" + ("Write title and values in English." if is_en else "标题和内容使用中文。")
        user = f"{'Extract knowledge from the following text' if is_en else '请从以下文本中提取知识'}:\n{text}"
        result = await self._chat(system, user, temperature=0.3)
        try:
            return self._parse_json_response(result)
        except (json.JSONDecodeError, ValueError):
            return {"title": "Extracted knowledge", "content_structured": {}, "confidence": 0.0}


    async def infer_knowledge(
        self, offer_data: dict[str, Any], language: str = "zh-CN", user_hint: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        offer = offer_data.get("offer", {})
        offer_name = offer.get("name", "Product")
        knowledge_items = offer_data.get("knowledge_items", [])

        existing_text = format_existing_knowledge(knowledge_items, language=language)
        system = _build_infer_knowledge_system_prompt(language)
        user = format_offer_summary(offer_data, language=language) + existing_text

        if user_hint:
            user += f"\nAdditional notes from user: {user_hint}"

        prompt_len = len(system) + len(user)
        logger.info("Inferring knowledge for offer '%s' via %s (prompt=%d chars)", offer_name, self.provider, prompt_len)

        result = await self._chat(system, user, temperature=0.7)
        try:
            parsed = self._parse_json_response(result)
        except (json.JSONDecodeError, ValueError):
            logger.error(
                "Failed to parse infer-knowledge response | offer=%s model=%s prompt_len=%d response_len=%d | response: %s",
                offer_name, self.model, prompt_len, len(result), result[:1000],
            )
            raise ValueError(f"LLM returned unparseable response for offer '{offer_name}'")

        # Ensure all expected keys exist
        for key in ("selling_point", "audience", "scenario", "pain_point", "faq", "objection", "proof"):
            if key not in parsed:
                parsed[key] = []

        return parsed

    async def infer_knowledge_stream(
        self, offer_data: dict[str, Any], language: str = "zh-CN",
    ):
        """Stream version of infer_knowledge. Yields (event_type, data) tuples:
        - ("thinking", "chunk of thinking text")
        - ("result", {parsed dict})
        """
        import re
        offer = offer_data.get("offer", {})
        offer_name = offer.get("name", "Product")
        knowledge_items = offer_data.get("knowledge_items", [])
        existing_text = format_existing_knowledge(knowledge_items, language=language)
        system = _build_infer_knowledge_system_prompt(language)
        user = format_offer_summary(offer_data, language=language) + existing_text
        logger.info("Streaming infer-knowledge for '%s' via %s", offer_name, self.provider)

        full_text = ""
        in_think = False
        async for token in self._chat_stream(system, user, temperature=0.7):
            full_text += token
            # Detect <think> blocks and yield thinking chunks
            if "<think>" in token:
                in_think = True
            if in_think:
                clean = token.replace("<think>", "").replace("</think>", "")
                if clean.strip():
                    yield ("thinking", clean)
            if "</think>" in token:
                in_think = False

        # Parse the final result
        try:
            parsed = self._parse_json_response(full_text)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse streamed infer-knowledge | offer=%s response_len=%d | %s",
                         offer_name, len(full_text), full_text[:1000])
            parsed = {}
            yield ("error", "AI 未能生成有效结果，请重试")

        for key in ("selling_point", "audience", "scenario", "pain_point", "faq", "objection", "proof"):
            if key not in parsed:
                parsed[key] = []

        yield ("result", parsed)

    async def extract_brandkit_profiles(self, text: str, language: str = "zh-CN") -> dict[str, Any]:
        # Truncate to ~8000 chars to fit context window
        text = text[:8000]
        is_en = language.startswith("en")

        lang_instruction = "Write all profile descriptions in English." if is_en else "所有描述内容使用中文。"

        system = f"""You are a brand visual specification expert. Based on the provided text (company website content or brand document), extract brand visual guidelines and populate the following 7 fields.

Each field should be a natural-language description (not nested JSON) — be specific and actionable:

1. style_profile_json — Brand identity & visual style
   Overall brand tone, primary/secondary colors, mood keywords, typography preferences, etc.

2. product_visual_profile_json — Product visual guidelines
   Product photography angles, backgrounds, lighting, composition, props, etc.

3. service_scene_profile_json — Service scene guidelines
   Environment, atmosphere, human interaction style for service scenarios.

4. persona_profile_json — Character/persona guidelines
   On-screen talent appearance, attire, expression, gestures, demographic traits.

5. visual_do_json — Recommended visual expressions
   Recommended visual techniques, compositions, color combinations.

6. visual_dont_json — Prohibited visual expressions
   Visual elements, styles, or expressions to avoid.

7. reference_prompt_json — Reference prompt templates
   Ready-to-use prompts for AI image/video generation.

Rules:
- Return a strict JSON object with the 7 keys above
- Each value is a plain text string (not a nested JSON object)
- Set fields to null if they cannot be inferred from the text
- {lang_instruction}
- Return JSON only, no other text"""

        user = f"{'Extract brand visual guidelines from the following text' if is_en else '请从以下文本中提取品牌视觉规范信息'}:\n\n{text}"

        logger.info("Extracting brandkit profiles via %s, text length=%d", self.provider, len(text))

        result = await self._chat(system, user, temperature=0.4)
        thinking, clean_result = _extract_thinking(result)
        try:
            parsed = self._parse_json_response(clean_result)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse brandkit extract response: %s", clean_result[:500])
            raise RuntimeError("Failed to parse AI response, please retry" if is_en else "AI 返回的内容无法解析，请重试")

        # Ensure all 7 keys exist
        profile_keys = [
            "style_profile_json", "product_visual_profile_json", "service_scene_profile_json",
            "persona_profile_json", "visual_do_json", "visual_dont_json", "reference_prompt_json",
        ]
        for key in profile_keys:
            if key not in parsed:
                parsed[key] = None

        return parsed

    async def infer_offer_model(self, name: str, description: str, offer_type: str) -> str:
        from app.domain.enums import OfferModel
        valid_values = [m.value for m in OfferModel]

        system = f"""You are a business analyst. Given an offer's name, description, and type, infer the most specific delivery model.
Return ONLY one of these values (no other text): {', '.join(valid_values)}

Guidelines:
- physical_product: tangible goods shipped or picked up
- digital_product: software, digital content, e-books, online courses
- local_service: on-site services tied to a location (restaurant, salon, gym)
- professional_service: expertise-driven services (consulting, legal, marketing, training)
- package: a bundle combining multiple products or services
- solution: an integrated solution addressing a specific business problem"""

        user = f"Name: {name}\nDescription: {description or 'N/A'}\nOffer type: {offer_type}"

        result = await self._chat(system, user, temperature=0.2, max_tokens=64)
        result = result.strip().lower().replace('"', '').replace("'", '')
        if result in valid_values:
            return result

        logger.warning("AI returned invalid offer_model '%s', falling back to stub", result)
        stub = StubAIAdapter()
        return await stub.infer_offer_model(name, description, offer_type)


class AnthropicMessagesAdapter(OpenAICompatibleAdapter):
    """Adapter for providers that only support Anthropic Messages API (/v1/messages).

    Subclasses OpenAICompatibleAdapter so all isinstance checks and high-level
    AI methods are inherited unchanged. Only _chat and _chat_stream are overridden.
    """

    def __init__(self, api_key: str, base_url: str, model: str, provider: str | None = None):
        # Do NOT call super().__init__() — we don't need an OpenAI client.
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider or "anthropic"
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def _chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.8, max_tokens: int = 16384) -> str:
        import asyncio
        import httpx
        last_err = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/messages",
                        headers=self._headers,
                        json={
                            "model": self.model,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "system": system_prompt,
                            "messages": [{"role": "user", "content": user_prompt}],
                        },
                        timeout=60,
                    )
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"]
            except Exception as e:
                last_err = e
                status = getattr(getattr(e, "response", None), "status_code", None) or 0
                if status and 400 <= status < 500 and status != 429:
                    raise
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    logger.warning("Anthropic call failed (attempt %d/3), retrying in %ds: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
        raise last_err  # type: ignore[misc]

    async def _chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> Any:
        """Anthropic Messages API does not support OpenAI's
        `response_format={"type":"json_object"}`. Go directly to prompt-
        constrained JSON mode — same as the OpenAI adapter's fallback path.

        Previously this inherited the OpenAI implementation, which tried to
        access `self.client` and raised AttributeError — causing every Script
        Writer JSON generation to silently fall through to plain text.
        """
        fallback_system = system_prompt + (
            "\n\nREMINDER: Output ONLY valid JSON — no markdown, no explanation, "
            "no text before or after the JSON object."
        )
        raw = await self._chat(fallback_system, user_prompt, temperature=temperature)
        return self._parse_json_response(raw)

    async def _chat_vision(self, system_prompt: str, user_text: str, image_path: str, temperature: float = 0.8) -> str:
        """Anthropic vision via Messages API — image is a content block with
        base64 data, not an OpenAI-style `image_url`.
        """
        import base64
        import mimetypes
        import httpx

        mime, _ = mimetypes.guess_type(image_path)
        mime = mime or "image/jpeg"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers,
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": mime, "data": b64,
                            }},
                            {"type": "text", "text": user_text},
                        ],
                    }],
                },
                timeout=120,
            )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    async def _chat_stream(self, system_prompt: str, user_prompt: str, temperature: float = 0.8, timeout: float = 180):
        """Stream tokens via Anthropic Messages SSE."""
        import asyncio
        import httpx
        import json as _json
        last_err = None
        for attempt in range(3):
            try:
                deadline = asyncio.get_running_loop().time() + timeout
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/v1/messages",
                        headers=self._headers,
                        json={
                            "model": self.model,
                            "max_tokens": 16384,
                            "temperature": temperature,
                            "system": system_prompt,
                            "messages": [{"role": "user", "content": user_prompt}],
                            "stream": True,
                        },
                        timeout=timeout,
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if asyncio.get_running_loop().time() > deadline:
                                raise TimeoutError(f"Anthropic stream exceeded {timeout}s")
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                event = _json.loads(data_str)
                            except _json.JSONDecodeError:
                                continue
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                return
            except Exception as e:
                last_err = e
                status = getattr(getattr(e, "response", None), "status_code", None) or 0
                if status and 400 <= status < 500 and status != 429:
                    raise
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    logger.warning("Anthropic stream failed (attempt %d/3), retrying in %ds: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
        raise last_err  # type: ignore[misc]


def _fix_docker_url(url: str) -> str:
    """Replace localhost with host.docker.internal when running inside Docker."""
    import os
    if os.path.exists("/.dockerenv") and "localhost" in url:
        return url.replace("localhost", "host.docker.internal")
    return url


async def get_ai_adapter(db=None, scene_key: str | None = None, model_type: str = "text_llm", config_id: str | None = None) -> AIAdapter:
    """Factory: explicit config_id → scene config → active config → StubAIAdapter (no AI configured)."""
    if db is not None:
        try:
            config = None
            # If caller specified a config_id, load it directly (skip scene/default)
            if config_id:
                import uuid as _uuid
                from app.models.llm_config import LLMConfig
                config = await db.get(LLMConfig, _uuid.UUID(config_id))
                if config:
                    logger.info("AI adapter: using explicit config_id=%s → %s/%s", config_id, config.provider, config.model_name)
            if not config and scene_key:
                from app.application.setting_service import get_llm_config_for_scene
                config = await get_llm_config_for_scene(db, scene_key, model_type)
                if config:
                    logger.info("AI adapter: scene=%s → %s/%s", scene_key, config.provider, config.model_name)
            if not config:
                from app.application.setting_service import get_active_llm_config
                config = await get_active_llm_config(db)
                if config:
                    logger.info("AI adapter: no scene config, using active default → %s/%s", config.provider, config.model_name)
            if config:
                fixed_url = _fix_docker_url(config.base_url)
                provider = getattr(config, "provider", None) or getattr(config, "label", "LLM")
                if provider == "anthropic":
                    return AnthropicMessagesAdapter(
                        api_key=config.api_key,
                        base_url=fixed_url,
                        model=config.model_name,
                        provider=provider,
                    )
                return OpenAICompatibleAdapter(
                    api_key=config.api_key,
                    base_url=fixed_url,
                    model=config.model_name,
                    provider=provider,
                )
        except Exception:
            pass

    logger.info("AI adapter: no LLM configured, using StubAIAdapter")
    return StubAIAdapter()
