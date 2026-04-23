"""Script Composer — assembles a 5-layer system prompt from Platform × Persona × Goal × Structure.

Layer order (BASE → PLATFORM → PERSONA → GOAL → STRUCTURE → BRAND):
  - BASE:       Universal TTS/oral constraints, always first (least specific)
  - PLATFORM:   Output format, length, platform norms
  - PERSONA:    Language style, voice
  - GOAL:       Strategic intent, CTA strength
  - STRUCTURE:  Narrative template + JSON output schema (most specific, placed last for LLM recency bias)
  - BRAND:      Optional brand overlay

JSON output format depends on platform content_type:
  - video:      sections[id] = {text, visual_direction, duration_seconds}
  - text_post:  sections[id] = {text, image_hint?}
"""
from __future__ import annotations

import json
import logging

from app.application.script_platforms import ScriptPlatform, get_platform, DEFAULT_PLATFORM_ID
from app.application.script_personas import ScriptPersona, get_persona, DEFAULT_PERSONA_ID
from app.application.script_goals import ScriptGoal, get_goal, DEFAULT_GOAL_ID
from app.application.script_structures import ScriptStructure, get_structure, DEFAULT_STRUCTURE_ID

logger = logging.getLogger(__name__)

# ── Layer 1: BASE (hardcoded, language-agnostic) ──────────────────────────────

_BASE_ZH = (
    "你是一位专业的内容创作专家，专注于社交媒体内容创作。\n"
    "核心原则：为嘴巴写字（视频口播）或为手机屏幕读者写字（图文），不为眼睛看文稿写字。\n"
    "通用规范：\n"
    "- 禁止使用括号、星号、破折号、省略号等特殊符号\n"
    "- 禁止使用舞台指示、场景描述标签（不要在正文中写[背景音乐]、[镜头切换]等）\n"
    "- 数字和英文字母会被TTS直接念出，确保语义自然\n"
    "- 口语化，单句精简，用逗号和句号控制节奏\n"
    "- **非促销铁律**：每篇内容至少包含一段承认局限 / 讨论取舍 / 说明什么场景不适用的话。缺这一段内容就像广告，会让读者和平台算法双双失信任。\n"
)

_BASE_EN = (
    "You are a professional social media content creator.\n"
    "Core principle: write for the mouth (video narration) or mobile screen readers (text posts), not for printed documents.\n"
    "Universal rules:\n"
    "- No brackets, asterisks, em-dashes, or ellipses\n"
    "- No stage directions or scene annotation tags in the main text\n"
    "- Numbers and abbreviations will be spoken by TTS — ensure they read naturally aloud\n"
    "- Conversational, concise sentences — use commas and periods to control pacing\n"
    "- **Non-promotional requirement**: every piece must include at least one passage that admits a limitation, discusses a tradeoff, or names when the product is the wrong choice. Missing this line makes the content read like an ad and loses trust with both readers and ranking algorithms.\n"
)


def _build_output_instruction(platform: ScriptPlatform, structure: ScriptStructure, language: str) -> str:
    """Dispatch to the right output format instruction based on content type.

    - video: JSON schema (needed for B-roll planning, duration, visual_direction)
    - text_post / article / thread: plain markdown (no JSON overhead)
    """
    if platform.is_video:
        return _build_json_schema(platform, structure)
    return _build_markdown_instruction(platform, structure, language)


def _build_markdown_instruction(platform: ScriptPlatform, structure: ScriptStructure, language: str) -> str:
    """Markdown output format for non-video content (post / article / thread).

    Uses simple, parseable conventions:
      - First non-empty `# X` line is the title
      - Line(s) of `#tag1 #tag2 ...` (hashtag tokens, no space after #) = hashtags
      - Everything else = body (structured sections separated by `## section_name`)
      - For thread content_type: tweets separated by `---` on its own line
    """
    is_zh = language.startswith("zh") or language.startswith("ZH")
    ct = platform.content_type

    if ct == "thread":
        if is_zh:
            return (
                "\n\n输出格式（多条推文的线程）：\n"
                "- 每条推文之间用单独一行的 `---` 分隔\n"
                "- 第一条推文即 hook，必须能独立吸引人点击\n"
                "- 最后一条推文包含 1-3 个 hashtag（例如 #SEO #Marketing）\n"
                "- 不要输出 JSON，不要任何代码块，直接输出纯文本\n"
                "- 每条推文 200-280 字符\n"
            )
        return (
            "\n\nOutput format (multi-tweet thread):\n"
            "- Separate tweets with a line containing only `---`\n"
            "- Tweet 1 is the hook — must stand alone as a retweet-magnet\n"
            "- Final tweet includes 1-3 hashtags (e.g. #SEO #Marketing)\n"
            "- Do NOT output JSON or code blocks — plain text only\n"
            "- Each tweet 200-280 characters\n"
        )

    if ct == "article":
        if is_zh:
            return (
                "\n\n输出格式（长文章）：\n"
                "- 第一行 `# 标题` 是文章标题\n"
                "- 正文可以使用 `## 小标题` 分段\n"
                "- 段落之间空一行\n"
                "- 文章末尾可以列 3-5 个 hashtag，格式：单独一行 `#标签1 #标签2 #标签3`\n"
                "- 不要输出 JSON，不要代码块包裹，直接输出 markdown\n"
            )
        return (
            "\n\nOutput format (long-form article):\n"
            "- First line `# Title` is the article title\n"
            "- Body may use `## Subheading` to structure sections\n"
            "- Blank line between paragraphs\n"
            "- Optional last line: 3-5 hashtags as `#tag1 #tag2 #tag3`\n"
            "- Do NOT output JSON or wrap in code blocks — plain markdown\n"
        )

    # text_post (default for non-video non-thread non-article)
    if is_zh:
        return (
            "\n\n输出格式（图文帖子）：\n"
            "- 第一行 `# 标题` 是帖子标题（小红书等平台最关键）\n"
            "- 空一行后是正文（多段，每段 3-5 行，段间空行）\n"
            "- 最后一行是 hashtag：`#标签1 #标签2 #标签3 #标签4`（3-6 个，紧凑无空行）\n"
            "- 不要输出 JSON，不要代码块，直接输出 markdown\n"
        )
    return (
        "\n\nOutput format (short post):\n"
        "- First line `# Title` is the post title\n"
        "- Blank line, then body (short paragraphs, blank lines between them)\n"
        "- Last line: hashtags `#tag1 #tag2 #tag3` (3-6 tags)\n"
        "- Do NOT output JSON or code blocks — plain markdown\n"
    )


def _build_json_schema(platform: ScriptPlatform, structure: ScriptStructure) -> str:
    """Build the JSON output schema instruction (video only)."""
    section_ids = structure.section_ids
    is_video = platform.is_video
    is_zh = platform.region == "zh"

    example_section = '{"text": "口播文字，即说出口的内容", "visual_direction": "这个镜头应该呈现什么画面，用于B-roll素材匹配", "duration_seconds": 5}'
    if not is_zh:
        example_section = '{"text": "The spoken narration text", "visual_direction": "What should appear on screen, for B-roll matching", "duration_seconds": 5}'

    sections_example = {sid: json.loads(example_section) for sid in section_ids}

    # B-roll plan — AI director decides WHEN and WHY to cut
    if is_zh:
        broll_plan_example = [
            {"type": "retention", "insert_after_char": 0, "duration_seconds": 2,
             "prompt": "（在此描述能阻止用户划走的视觉冲击画面）"},
            {"type": "illustrative", "insert_after_char": "（口播文字的第N个字之后插入，精确到具体位置）",
             "duration_seconds": 4, "prompt": "（在此描述辅助说明当前口播内容的画面）"},
        ]
        broll_instruction = (
            "\n\nbroll_plan 编排规则（你是 AI 编导，根据文案内容智能编排视觉节奏）：\n"
            "\n核心原则：短视频每 8-12 秒需要一次视觉变化，否则观众划走。"
            "B-roll 是最强的视觉变化手段，系统会自动用模拟运镜（zoom）填充剩余间隔，"
            "你只需规划 B-roll 的创意点位。\n\n"
            "编排策略：\n"
            "- retention 类型：放在视频开头（insert_after_char=0），用视觉冲击力阻止划走，5秒\n"
            "- illustrative 类型：在口播提到具体事物/数据/场景时切入辅助画面，5-6秒\n"
            "- 根据视频总时长决定 B-roll 数量：30秒以下=1-2个，30-60秒=2-3个，60秒以上=3-4个\n"
            "- B-roll 之间尽量均匀分布，避免连续两段 B-roll 紧挨着\n"
            "- CTA 段（最后一段）不要放 B-roll，保持数字人面对面\n"
            "\n技术约束：\n"
            "- duration_seconds 必须在 5-10 秒范围内\n"
            "- insert_after_char 是口播全文（所有 section 的 text 拼接后）中第几个字之后插入\n"
            "- prompt 要具体描述一个 AI 可生成的画面场景，避免含人脸\n"
            "- 可以描述：产品界面、数据图表、场景氛围、动作特写、物品展示等\n"
        )
    else:
        broll_plan_example = [
            {"type": "retention", "insert_after_char": 0, "duration_seconds": 2,
             "prompt": "(Describe a visually striking shot that stops the scroll)"},
            {"type": "illustrative", "insert_after_char": "(character position in concatenated narration text)",
             "duration_seconds": 4, "prompt": "(Describe what should appear on screen to illustrate the narration)"},
        ]
        broll_instruction = (
            "\n\nbroll_plan rules (you are the AI director — plan visual rhythm based on script content):\n"
            "\nCore principle: short videos need a visual change every 8-12 seconds or viewers scroll away. "
            "B-roll is the strongest visual change. The system auto-inserts simulated camera moves (zoom) "
            "for remaining gaps — you only plan the creative B-roll insert points.\n\n"
            "Strategy:\n"
            "- retention: at video start (insert_after_char=0), visually striking to stop scrolling, 5s\n"
            "- illustrative: when narration mentions specific things/data/scenes, 5-6s\n"
            "- Scale B-roll count by duration: under 30s=1-2, 30-60s=2-3, over 60s=3-4\n"
            "- Distribute B-roll evenly — avoid clustering two inserts back-to-back\n"
            "- Never place B-roll in the CTA (final) section — keep avatar face-to-camera\n"
            "\nConstraints:\n"
            "- duration_seconds must be 5-10\n"
            "- insert_after_char = character position in concatenated narration (all section texts joined)\n"
            "- prompt must describe a concrete AI-generatable scene, no human faces\n"
        )

    schema_obj = {
        "platform_id": platform.id,
        "structure_id": structure.id,
        "content_type": platform.content_type,
        "estimated_total_seconds": "（视频总秒数）" if is_zh else "(total video seconds)",
        "sections": sections_example,
        "broll_plan": broll_plan_example,
    }

    if is_zh:
        instruction = (
            "输出格式要求：必须输出合法的JSON，结构如下（不要输出任何JSON以外的内容）：\n"
            f"```json\n{json.dumps(schema_obj, ensure_ascii=False, indent=2)}\n```\n\n"
            f"sections中必须包含以下key：{section_ids}，每个key对应上面的字段结构。\n"
            f"{broll_instruction}"
        )
    else:
        instruction = (
            "Output format: you MUST output valid JSON only (no text outside the JSON). Structure:\n"
            f"```json\n{json.dumps(schema_obj, ensure_ascii=False, indent=2)}\n```\n\n"
            f"sections MUST contain these keys: {section_ids}, each matching the field structure above.\n"
            f"{broll_instruction}"
        )

    return instruction


def compose_system_prompt(
    platform_id: str | None = None,
    persona_id: str | None = None,
    goal_id: str | None = None,
    structure_id: str | None = None,
    brand_tone: str | None = None,
    language: str = "zh-CN",
) -> tuple[str, ScriptPlatform, ScriptStructure]:
    """Assemble a 5-layer system prompt.

    Returns:
        (system_prompt, platform, structure) — caller needs platform and structure
        to know the JSON output schema and section_ids.
    """
    is_zh = language.startswith("zh") or language.startswith("ZH")

    platform = get_platform(platform_id or DEFAULT_PLATFORM_ID) or get_platform(DEFAULT_PLATFORM_ID)
    persona = get_persona(persona_id or DEFAULT_PERSONA_ID) or get_persona(DEFAULT_PERSONA_ID)
    goal = get_goal(goal_id or DEFAULT_GOAL_ID) or get_goal(DEFAULT_GOAL_ID)
    structure = get_structure(structure_id or DEFAULT_STRUCTURE_ID) or get_structure(DEFAULT_STRUCTURE_ID)

    # Should never be None after fallback, but guard anyway
    if not platform or not persona or not goal or not structure:
        raise RuntimeError("Failed to load script composer components")

    layers: list[str] = []

    # Layer 1: BASE
    layers.append(_BASE_ZH if is_zh else _BASE_EN)

    # Layer 2: PLATFORM
    if is_zh:
        platform_header = f"## 平台：{platform.emoji} {platform.name_zh}\n"
    else:
        platform_header = f"## Platform: {platform.emoji} {platform.name_en}\n"
    layers.append(platform_header + platform.body)

    # Layer 3: PERSONA
    if is_zh:
        persona_header = f"## 语言风格：{persona.emoji} {persona.name_zh}（{persona.description_zh}）\n"
    else:
        persona_header = f"## Voice Style: {persona.emoji} {persona.name_en} — {persona.description_en}\n"
    layers.append(persona_header + persona.body)

    # Layer 4: GOAL
    if is_zh:
        goal_header = f"## 内容目标：{goal.emoji} {goal.name_zh}\n"
    else:
        goal_header = f"## Content Goal: {goal.emoji} {goal.name_en}\n"
    layers.append(goal_header + goal.prompt_fragment(language))

    # Layer 5: STRUCTURE + JSON schema (placed last for LLM recency bias)
    if is_zh:
        structure_header = f"## 叙事结构：{structure.emoji} {structure.name_zh}（{structure.description_zh}）\n"
    else:
        structure_header = f"## Narrative Structure: {structure.emoji} {structure.name_en} — {structure.description_en}\n"
    structure_layer = structure_header + structure.body + "\n\n" + _build_output_instruction(platform, structure, language)
    layers.append(structure_layer)

    # Layer 6: BRAND OVERLAY (optional)
    if brand_tone and brand_tone.strip():
        if is_zh:
            layers.append(f"## 品牌语气覆盖\n{brand_tone.strip()}")
        else:
            layers.append(f"## Brand Voice Override\n{brand_tone.strip()}")

    # Layer 7: LANGUAGE OVERRIDE (last — recency bias)
    # Platform bodies are written in their native market language (e.g. douyin.md
    # is Chinese, tiktok.md is English). If the user picks a language that doesn't
    # match the platform region, the LLM can leak the platform body's language
    # into JSON section text. This final override forces output language explicitly.
    if is_zh:
        layers.append(
            "## 输出语言\n所有生成内容（sections 里每个 text 字段、title、body 等）必须使用**简体中文**。"
            "忽略上方平台规则中使用的任何其他语言示例——那只是写作风格参考，不是输出语言。"
        )
    else:
        layers.append(
            "## Output Language\nAll generated content (every `text` field in sections, titles, body, etc.) "
            "MUST be in **English**. Ignore any non-English examples in the platform rules above — "
            "those are writing-style references, not the output language."
        )

    system_prompt = "\n\n---\n\n".join(layers)
    return system_prompt, platform, structure
