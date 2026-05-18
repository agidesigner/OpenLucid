from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_prompt_preset import UserPromptPreset
from app.schemas.setting import PromptPresetItem

logger = logging.getLogger(__name__)


# ── System default prompt registry ──────────────────────────────
# This is the single source of truth for what presets exist and their defaults.

@dataclass(frozen=True)
class SystemPromptPreset:
    preset_key: str
    title_zh: str
    title_en: str
    category: str
    lang: str
    description_zh: str
    description_en: str
    content_getter: Callable[[], str]

    def localized_title(self, language: str = "zh-CN") -> str:
        return self.title_en if _is_en_ui(language) else self.title_zh

    def localized_description(self, language: str = "zh-CN") -> str:
        return self.description_en if _is_en_ui(language) else self.description_zh


def _is_en_ui(language: str | None) -> bool:
    return (language or "").lower().startswith("en")

def _get_kb_infer_zh() -> str:
    """Knowledge inference system prompt (Chinese)."""
    from app.adapters.ai import _build_infer_knowledge_system_prompt
    return _build_infer_knowledge_system_prompt("zh")


def _get_kb_infer_en() -> str:
    """Knowledge inference system prompt (English)."""
    from app.adapters.ai import _build_infer_knowledge_system_prompt
    return _build_infer_knowledge_system_prompt("en")


def _get_script_base_zh() -> str:
    """Script composer base prompt (Chinese)."""
    from app.application.script_composer import _BASE_ZH
    return _BASE_ZH


def _get_script_base_en() -> str:
    """Script composer base prompt (English)."""
    from app.application.script_composer import _BASE_EN
    return _BASE_EN


def _get_persuasion_technique_zh() -> str:
    """Persuasion technique spec (Chinese)."""
    from app.application.script_composer import _persuasion_technique_spec
    return _persuasion_technique_spec(is_zh=True)


def _get_persuasion_technique_en() -> str:
    """Persuasion technique spec (English)."""
    from app.application.script_composer import _persuasion_technique_spec
    return _persuasion_technique_spec(is_zh=False)


def _get_viral_topic_rewrite_zh() -> str:
    """Viral topic rewrite spec (Chinese)."""
    from app.application.script_composer import _viral_topic_rewrite_spec
    return _viral_topic_rewrite_spec(is_zh=True)


def _get_viral_topic_rewrite_en() -> str:
    """Viral topic rewrite spec (English)."""
    from app.application.script_composer import _viral_topic_rewrite_spec
    return _viral_topic_rewrite_spec(is_zh=False)


def _get_shot_description_zh() -> str:
    """Shot description spec (Chinese)."""
    from app.application.script_composer import _shot_description_spec
    return _shot_description_spec(is_zh=True)


def _get_shot_description_en() -> str:
    """Shot description spec (English)."""
    from app.application.script_composer import _shot_description_spec
    return _shot_description_spec(is_zh=False)


def _get_brand_voice_suggest_zh() -> str:
    """Brand voice suggestion prompt (Chinese)."""
    return (
        "你是资深品牌策略师。基于下方品牌资料，写一份 3-5 段的"
        "「品牌语气说明」(Brand Voice)，让文案或 AI 内容工具能直接套用。按顺序覆盖：\n"
        "1. 调性与语域（温暖 / 冷静 / 自嘲 / 权威——锁定 1 种主导调性）。\n"
        "2. 叙述视角——第一人称（我们/我）、第三人称、还是物为主语？用什么代称？\n"
        "3. 句子节奏——短促有力？长句铺陈？混合？\n"
        "4. 品牌常用签名词/短语；品牌拒绝使用的黑话/禁用词。\n"
        "5. 品牌偏好的开场套路与 CTA 话术。\n\n"
        "规则：\n"
        "- 要具体。「专业又亲切」等于没说；「第一人称复数，从不说 '赋能/闭环/抓手'，"
        "常以用户场景开头」才有用。\n"
        "- 不要用 bullet 列表；写成连贯的段落，像在给文案写作者口头交代。\n"
        "- 只输出品牌语气说明本身。不要加前言，不要写「品牌语气：」这种标签。\n"
        "- 用中文撰写。"
    )


def _get_brand_voice_suggest_en() -> str:
    """Brand voice suggestion prompt (English)."""
    return (
        "You are a senior brand strategist. From the brand document below, "
        "write a 3-5 paragraph Brand Voice specification that a copywriter "
        "or AI content tool can apply directly. Cover, in order:\n"
        "1. Tone and register (warm / clinical / self-deprecating / authoritative — pick ONE dominant).\n"
        "2. Narrator stance — first person (we / I), third person, or product-facing. Which pronouns.\n"
        "3. Sentence rhythm and structure — short punchy? Long flowing? Mix?\n"
        "4. Signature words or phrases this brand uses. Banned words / jargon this brand refuses.\n"
        "5. Opening moves and CTAs the brand tends to reach for.\n\n"
        "Rules:\n"
        "- Be specific. 'Professional yet approachable' is useless; "
        "'First-person plural, never says synergy or ecosystem, opens with a customer quote' is useful.\n"
        "- No bullet lists in your output — write in cohesive paragraphs that feel like briefing a writer.\n"
        "- Output the voice spec only. No preface, no labels like 'Brand Voice:'.\n"
        "- Write in English."
    )


def _get_kb_qa_style_professional() -> str:
    """KB QA style: professional advisor."""
    from app.apps.kb_qa_styles import STYLE_TEMPLATES
    return STYLE_TEMPLATES["professional"].system_prompt_prefix


def _get_kb_qa_style_friendly() -> str:
    """KB QA style: friendly support."""
    from app.apps.kb_qa_styles import STYLE_TEMPLATES
    return STYLE_TEMPLATES["friendly"].system_prompt_prefix


def _get_kb_qa_style_expert() -> str:
    """KB QA style: product expert."""
    from app.apps.kb_qa_styles import STYLE_TEMPLATES
    return STYLE_TEMPLATES["expert"].system_prompt_prefix


def _get_image_brief_template() -> str:
    """Image generation brief template (multi-language)."""
    return (
        "Create a marketing image based on this user brief: 「{brief}」.\n"
        "\n"
        "Format: {aspect_hint}.\n"
        "Brand context: {brand_name}. Use this as context, not as permission to create a new brand mark.\n"
        "{selling_points_line}"
        "{audience_line}"
        "{brand_tone_line}"
        "\n"
        "Use the provided reference image(s) according to the guidance below.\n"
        "{extra_reference_line}"
        "{logo_block}"
        "{qr_block}"
        "Render any required text crisply, with high-contrast typography that matches the "
        "reference style. Spell every Chinese character correctly — do not invent or "
        "abbreviate words from the brief."
    )


def _get_image_refine_template() -> str:
    """Image refinement prompt template (multi-language)."""
    return (
        "Refine the provided image based on this change request: 「{refinement}」.\n"
        "\n"
        "Original brief was: 「{parent_brief}」.\n"
        "{brand_line}"
        "\n"
        "Keep the overall composition, characters, brand identity, color palette, and major "
        "visual elements the same as the provided image. Apply only the change requested above. "
        "Preserve any existing text content unless the change explicitly asks otherwise; spell every "
        "Chinese character correctly.\n"
        "\n"
        "Format: {aspect_hint}."
    )


def _get_cover_derive_prompt() -> str:
    """Article cover suggestion prompt (multi-language)."""
    return (
        "You are an image-design assistant. Given the article below, produce a "
        "one-shot visual brief for a single cover image.\n\n"
        "CRITICAL: Output the `brief` and `tags` in the SAME LANGUAGE as the "
        "article content. If the article is Chinese, write Chinese. If "
        "English, write English. Do not translate or mix languages.\n\n"
        "- brief: ONE sentence describing subject, action, and mood. "
        "30-60 Chinese chars or 12-25 English words. No brand names, no "
        "product names, no text to be rendered in the image.\n"
        "- tags: 3-5 short visual keywords for asset-library retrieval "
        "(same language as the article).\n\n"
        "Article title: {title}\n"
        "Platform: {platform}\n"
        "Body excerpt: {body}\n\n"
        "Return ONLY a JSON object: "
        '{"brief": "...", "tags": ["...", "..."]}'
    )


def _get_topic_viral_signals_zh() -> str:
    """Topic studio viral signals (Chinese)."""
    return (
        "\n## 网感要求（默认开启）\n"
        "- title 不要写「教你 X」「分享 X」这种说明文风——要写成像朋友圈/小红书爆款标题\n"
        "- hook 必须是前 3 秒能勾住的话，不能是中性陈述\n"
        "- 优先使用：反差、悬念、情绪、数字、对比、第一人称踩坑\n"
        "- 避免：标准营销话术、官腔、形容词堆砌\n"
        "- 每个标题至少含 1 个具象画面或情绪词"
    )


def _get_topic_viral_signals_en() -> str:
    """Topic studio viral signals (English)."""
    return (
        "\n## Viral Signals (Always Apply)\n"
        "- Titles should NOT read like instructional copy (\"How to X\", \"Tips for X\") — write like a viral creator post\n"
        "- Hooks must grab attention in the first 3 seconds — never neutral statements\n"
        "- Prefer: contrast, suspense, emotion, numbers, comparison, first-person mistakes\n"
        "- Avoid: standard marketing speak, official tone, adjective stacking\n"
        "- Each title should contain a concrete visual or emotional cue"
    )


# Registry of all system default prompts
# UI metadata stays here with the prompt definitions so new presets do not
# need a second frontend mapping table.
_SYSTEM_DEFAULTS: list[SystemPromptPreset] = [
    # ── Script Writer ──────────────────────────────────────────
    SystemPromptPreset(
        preset_key="script.base.zh",
        title_zh="文案生成基础规范（中文）",
        title_en="Script Base Rules (Chinese)",
        category="script_writer",
        lang="zh",
        description_zh="社交媒体内容创作的通用规范与核心原则。",
        description_en="Universal rules and principles for Chinese social content generation.",
        content_getter=_get_script_base_zh,
    ),
    SystemPromptPreset(
        preset_key="script.base.en",
        title_zh="文案生成基础规范（英文）",
        title_en="Script Base Rules (English)",
        category="script_writer",
        lang="en",
        description_zh="英文社交媒体内容创作的通用规范与核心原则。",
        description_en="Universal rules and principles for English social content generation.",
        content_getter=_get_script_base_en,
    ),
    SystemPromptPreset(
        preset_key="script.persuasion.zh",
        title_zh="说服手法规范（中文）",
        title_en="Persuasion Technique Spec (Chinese)",
        category="script_writer",
        lang="zh",
        description_zh="7 种说服手法的选择与应用约束，防止知识库平铺复述。",
        description_en="Seven persuasion techniques for Chinese scripts; prevents flat knowledge-base enumeration.",
        content_getter=_get_persuasion_technique_zh,
    ),
    SystemPromptPreset(
        preset_key="script.persuasion.en",
        title_zh="说服手法规范（英文）",
        title_en="Persuasion Technique Spec (English)",
        category="script_writer",
        lang="en",
        description_zh="英文脚本的 7 种说服手法选择与应用约束。",
        description_en="Seven persuasion techniques for English scripts; prevents flat knowledge-base enumeration.",
        content_getter=_get_persuasion_technique_en,
    ),
    SystemPromptPreset(
        preset_key="script.viral_rewrite.zh",
        title_zh="爆款选题拆解与重写（中文）",
        title_en="Viral Topic Rewrite (Chinese)",
        category="script_writer",
        lang="zh",
        description_zh="导入爆款选题或参考文案时，复用流量机制而不是平铺复述。",
        description_en="Reuses the attention mechanism from viral Chinese topics without copying the payload.",
        content_getter=_get_viral_topic_rewrite_zh,
    ),
    SystemPromptPreset(
        preset_key="script.viral_rewrite.en",
        title_zh="爆款选题拆解与重写（英文）",
        title_en="Viral Topic Rewrite (English)",
        category="script_writer",
        lang="en",
        description_zh="英文爆款选题或参考文案的流量机制拆解与重写规则。",
        description_en="Reuses the attention mechanism from viral English topics without copying the payload.",
        content_getter=_get_viral_topic_rewrite_en,
    ),
    SystemPromptPreset(
        preset_key="script.shot_description.zh",
        title_zh="镜头描述规范（中文）",
        title_en="Shot Description Spec (Chinese)",
        category="script_writer",
        lang="zh",
        description_zh="6 维度镜头描述标准，用于 visual_direction 与 B-roll prompt。",
        description_en="Six-dimension shot standard for Chinese visual_direction and B-roll prompts.",
        content_getter=_get_shot_description_zh,
    ),
    SystemPromptPreset(
        preset_key="script.shot_description.en",
        title_zh="镜头描述规范（英文）",
        title_en="Shot Description Spec (English)",
        category="script_writer",
        lang="en",
        description_zh="英文 visual_direction 与 B-roll prompt 的 6 维度镜头描述标准。",
        description_en="Six-dimension shot standard for English visual_direction and B-roll prompts.",
        content_getter=_get_shot_description_en,
    ),
    
    # ── Topic Studio ───────────────────────────────────────────
    SystemPromptPreset(
        preset_key="topic.viral_signals.zh",
        title_zh="选题网感要求（中文）",
        title_en="Topic Viral Signals (Chinese)",
        category="topic_studio",
        lang="zh",
        description_zh="中文标题与 hook 的网感约束，避免说明文风与官腔。",
        description_en="Chinese title and hook constraints that avoid instructional or official tone.",
        content_getter=_get_topic_viral_signals_zh,
    ),
    SystemPromptPreset(
        preset_key="topic.viral_signals.en",
        title_zh="选题网感要求（英文）",
        title_en="Topic Viral Signals (English)",
        category="topic_studio",
        lang="en",
        description_zh="英文标题与 hook 的网感约束，避免说明文风与企业腔。",
        description_en="English title and hook constraints that avoid instructional or corporate tone.",
        content_getter=_get_topic_viral_signals_en,
    ),
    
    # ── Image Generation ───────────────────────────────────────
    SystemPromptPreset(
        preset_key="image.brief_template",
        title_zh="图片生成 Brief 模板",
        title_en="Image Brief Template",
        category="image",
        lang="multi",
        description_zh="从用户 brief 构建完整图片生成提示词，包含品牌上下文、logo 纪律与排版要求。",
        description_en="Builds image-generation prompts from a user brief, including brand context, logo discipline, and layout rules.",
        content_getter=_get_image_brief_template,
    ),
    SystemPromptPreset(
        preset_key="image.refine_template",
        title_zh="图片优化 Refine 模板",
        title_en="Image Refinement Template",
        category="image",
        lang="multi",
        description_zh="基于原图与用户修改请求构建图片优化提示词。",
        description_en="Builds refinement prompts from the original image and user change request.",
        content_getter=_get_image_refine_template,
    ),
    SystemPromptPreset(
        preset_key="image.cover_derive",
        title_zh="文章封面建议提示词",
        title_en="Article Cover Suggestion Prompt",
        category="image",
        lang="multi",
        description_zh="从文章标题与正文推导封面 brief 与视觉标签。",
        description_en="Derives a cover-image brief and visual tags from an article title and body.",
        content_getter=_get_cover_derive_prompt,
    ),
    
    # ── Knowledge Base ─────────────────────────────────────────
    SystemPromptPreset(
        preset_key="kb.infer.zh",
        title_zh="知识库推理（中文）",
        title_en="Knowledge Inference (Chinese)",
        category="knowledge",
        lang="zh",
        description_zh="从商品或服务信息中推理生成中文结构化知识库。",
        description_en="Infers a structured Chinese knowledge base from product or service information.",
        content_getter=_get_kb_infer_zh,
    ),
    SystemPromptPreset(
        preset_key="kb.infer.en",
        title_zh="知识库推理（英文）",
        title_en="Knowledge Inference (English)",
        category="knowledge",
        lang="en",
        description_zh="从商品或服务信息中推理生成英文结构化知识库。",
        description_en="Infers a structured English knowledge base from product or service information.",
        content_getter=_get_kb_infer_en,
    ),
    
    # ── Brand Kit ──────────────────────────────────────────────
    SystemPromptPreset(
        preset_key="brandkit.voice_suggest.zh",
        title_zh="品牌语气抽取（中文）",
        title_en="Brand Voice Suggestion (Chinese)",
        category="brandkit",
        lang="zh",
        description_zh="从品牌文档中抽取中文品牌语气说明。",
        description_en="Extracts a Chinese brand voice specification from brand documents.",
        content_getter=_get_brand_voice_suggest_zh,
    ),
    SystemPromptPreset(
        preset_key="brandkit.voice_suggest.en",
        title_zh="品牌语气抽取（英文）",
        title_en="Brand Voice Suggestion (English)",
        category="brandkit",
        lang="en",
        description_zh="从品牌文档中抽取英文品牌语气说明。",
        description_en="Extracts an English brand voice specification from brand documents.",
        content_getter=_get_brand_voice_suggest_en,
    ),
    
    # ── KB QA Styles ───────────────────────────────────────────
    SystemPromptPreset(
        preset_key="kb_qa.style.professional",
        title_zh="知识库问答风格：专业顾问",
        title_en="KB Q&A Style: Professional Advisor",
        category="kb_qa",
        lang="multi",
        description_zh="条理清晰、用语专业的知识库问答风格。",
        description_en="A structured, professional style for knowledge-base answers.",
        content_getter=_get_kb_qa_style_professional,
    ),
    SystemPromptPreset(
        preset_key="kb_qa.style.friendly",
        title_zh="知识库问答风格：亲切客服",
        title_en="KB Q&A Style: Friendly Support",
        category="kb_qa",
        lang="multi",
        description_zh="语气友善、通俗易懂的知识库问答风格。",
        description_en="A friendly, plain-language style for knowledge-base answers.",
        content_getter=_get_kb_qa_style_friendly,
    ),
    SystemPromptPreset(
        preset_key="kb_qa.style.expert",
        title_zh="知识库问答风格：产品专家",
        title_en="KB Q&A Style: Product Expert",
        category="kb_qa",
        lang="multi",
        description_zh="有深度、善于类比的知识库问答风格。",
        description_en="A deeper product-expert style that explains with useful analogies.",
        content_getter=_get_kb_qa_style_expert,
    ),
]


def _find_system_preset(preset_key: str) -> SystemPromptPreset | None:
    for preset in _SYSTEM_DEFAULTS:
        if preset.preset_key == preset_key:
            return preset
    return None


async def list_prompt_presets(
    db: AsyncSession,
    user_id: str,
    category: str | None = None,
    modified_only: bool = False,
    language: str = "zh-CN",
) -> list[PromptPresetItem]:
    """List all available prompt presets with user overrides merged.
    
    Args:
        db: Database session
        user_id: Current user ID
        category: Optional filter by category
        modified_only: If True, only return presets with user overrides
        language: UI language for localized title/description
    
    Returns:
        List of PromptPresetItem with system defaults + user overrides
    """
    # Load all user overrides for this user
    result = await db.execute(
        select(UserPromptPreset).where(UserPromptPreset.user_id == user_id)
    )
    user_overrides = {row.preset_key: row for row in result.scalars().all()}
    
    # Build response by merging system defaults with user overrides
    presets = []
    for preset in _SYSTEM_DEFAULTS:
        # Apply category filter
        if category and preset.category != category:
            continue
        
        # Get system default content
        try:
            default_content = preset.content_getter()
        except Exception as e:
            logger.error(f"Failed to load default content for {preset.preset_key}: {e}")
            default_content = f"[Error loading default: {e}]"
        
        # Check for user override
        override = user_overrides.get(preset.preset_key)
        is_modified = override is not None
        
        # Apply modified_only filter
        if modified_only and not is_modified:
            continue
        
        presets.append(PromptPresetItem(
            preset_key=preset.preset_key,
            title=preset.localized_title(language),
            category=preset.category,
            lang=preset.lang,
            description=preset.localized_description(language),
            default_content=default_content,
            user_content=override.content if override else None,
            is_modified=is_modified,
            updated_at=override.updated_at.isoformat() if override else None,
        ))
    
    return presets


async def get_prompt_preset(
    db: AsyncSession,
    user_id: str,
    preset_key: str,
    language: str = "zh-CN",
) -> PromptPresetItem | None:
    """Get a single prompt preset with user override merged.
    
    Returns None if preset_key doesn't exist in system defaults.
    """
    # Find in system defaults
    preset = _find_system_preset(preset_key)
    if not preset:
        return None
    
    # Get system default content
    try:
        default_content = preset.content_getter()
    except Exception as e:
        logger.error(f"Failed to load default content for {preset_key}: {e}")
        default_content = f"[Error loading default: {e}]"
    
    # Check for user override
    result = await db.execute(
        select(UserPromptPreset).where(
            UserPromptPreset.user_id == user_id,
            UserPromptPreset.preset_key == preset_key,
        )
    )
    override = result.scalar_one_or_none()
    
    return PromptPresetItem(
        preset_key=preset_key,
        title=preset.localized_title(language),
        category=preset.category,
        lang=preset.lang,
        description=preset.localized_description(language),
        default_content=default_content,
        user_content=override.content if override else None,
        is_modified=override is not None,
        updated_at=override.updated_at.isoformat() if override else None,
    )


async def save_prompt_preset(
    db: AsyncSession,
    user_id: str,
    preset_key: str,
    content: str,
    language: str = "zh-CN",
) -> PromptPresetItem:
    """Save or update a user override for a prompt preset.
    
    Raises:
        ValueError: If preset_key doesn't exist in system defaults
    """
    # Verify preset_key exists
    system_preset = _find_system_preset(preset_key)
    preset = await get_prompt_preset(db, user_id, preset_key, language=language)
    if not preset:
        raise ValueError(f"Unknown preset_key: {preset_key}")
    
    # Find or create user override
    result = await db.execute(
        select(UserPromptPreset).where(
            UserPromptPreset.user_id == user_id,
            UserPromptPreset.preset_key == preset_key,
        )
    )
    override = result.scalar_one_or_none()
    
    if override:
        # Update existing
        override.content = content
        from datetime import datetime
        override.updated_at = datetime.utcnow()
    else:
        # Create new
        override = UserPromptPreset(
            user_id=user_id,
            preset_key=preset_key,
            title=system_preset.title_zh if system_preset else preset.title,
            category=system_preset.category if system_preset else preset.category,
            lang=system_preset.lang if system_preset else preset.lang,
            content=content,
        )
        db.add(override)
    
    await db.commit()
    await db.refresh(override)
    
    # Return updated preset
    return await get_prompt_preset(db, user_id, preset_key, language=language)


async def reset_prompt_preset(
    db: AsyncSession,
    user_id: str,
    preset_key: str,
    language: str = "zh-CN",
) -> PromptPresetItem:
    """Delete user override for a preset, restoring system default.
    
    Raises:
        ValueError: If preset_key doesn't exist in system defaults
    """
    # Verify preset_key exists
    preset = await get_prompt_preset(db, user_id, preset_key, language=language)
    if not preset:
        raise ValueError(f"Unknown preset_key: {preset_key}")
    
    # Delete user override if exists
    await db.execute(
        delete(UserPromptPreset).where(
            UserPromptPreset.user_id == user_id,
            UserPromptPreset.preset_key == preset_key,
        )
    )
    await db.commit()
    
    # Return preset with system default
    return await get_prompt_preset(db, user_id, preset_key, language=language)


async def reset_all_prompt_presets(
    db: AsyncSession,
    user_id: str,
) -> int:
    """Delete all user overrides, restoring all system defaults.
    
    Returns:
        Number of presets reset
    """
    result = await db.execute(
        delete(UserPromptPreset).where(UserPromptPreset.user_id == user_id)
    )
    await db.commit()
    return result.rowcount
