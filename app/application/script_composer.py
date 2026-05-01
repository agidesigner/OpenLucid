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
    "- **具体胜过泛化**：要写真实数字、真实案例、真实时间。写'增长很多'不如写'6 个月内从 500 到 8 200 条订单'\n"
    "- **第一人称叙事者**：用'我'或'我们'（创作者视角），不要'本品牌'、'您'、机构化语气\n"
    "- **先交付价值再谈推广**：读者给了你注意力，他们是选择加入内容，不是选择加入广告。正文 80%+ 必须是读者可带走的洞察、方法、故事。品牌/产品提及最多一次，且在最后\n"
    "- **数字化承诺标题好用**：'5 个误区'、'3 步拆解'这种清晰标题比'一些想法'强得多\n"
    "- **Hook 必须挣来下一行**：第一行的使命是让读者读第二行。铺垫、感慨、引用名言都不行 —— 直接上冲突、数字或具体问题\n"
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
    "- **Specific beats general**: real numbers, real company names, real timeframes. 'Grew fast' loses to '500 to 8,200 orders in 6 months'\n"
    "- **First-person narrator**: 'I' or 'we' from the creator's seat. No institutional 'this brand', no 'our company believes', no corporate third-person\n"
    "- **Value first, promotion last**: readers opted into content, not an ad. 80%+ of the body must be insight, method, or story they can take away. Brand/product mention once, at the end, or skip it\n"
    "- **Numbered-promise titles work**: '5 mistakes I made at $1M ARR' / '7 things I wish I knew' beat 'Some thoughts on X'\n"
    "- **The hook must earn the next line**: the first line's only job is getting the second line read. No warmup, no 'excited to share', no motivational quotes — lead with the surprising specific\n"
    "- **Cut corporate buzzwords**: 'synergy', 'leverage', 'disrupt', 'ecosystem', 'stakeholders' as filler. High-intensity voice (blunt, opinionated) is fine when earned by specifics\n"
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


def _persuasion_technique_spec(is_zh: bool) -> str:
    """Persuasion-technique spine constraint. Distilled from a pro content-marketing
    course (7 techniques) and tightened into two hard prompt rules:

    1. Pick ONE technique as the whole-script spine — don't ladder through several.
    2. Ban the "enumerate everything from the knowledge base" laziness that produced
       the original friend complaint (monotonous structure, >20% product description).

    Kept as prompt-layer constraints on purpose — no user-facing config, no DB.
    The LLM picks the best fit for the current KB; if we forced the user to choose,
    90% would pick wrong.
    """
    if is_zh:
        return (
            "## 说服手法（必选 1 种作全片骨架）\n"
            "从以下 7 种说服手法中选 1 种作为**全片的主脊**，整条脚本都围绕它展开。"
            "其余知识库内容只作为对主脊的具体支撑，不得自行另起一条叙事线：\n"
            "\n"
            "1. **USP 差异化** — 提出一个别人没有的独特卖点（工艺/设计/成分/理念/人群），"
            "讲述逻辑=1 个 USP + 多个辅助证明。\n"
            "2. **痛点解决** — 显性或隐性痛点 → 激发 → 展示方案 → 解决效果。"
            "大痛点=大消耗，痛点原理讲清最容易出爆款。\n"
            "3. **品类 PK** — 老品类（A）有缺陷 → 新品类（B）没这个缺陷 → B 的新优势。"
            "对象要够大、够痛、够相关。\n"
            "4. **场景代入** — 时节/需求/带入场景，在场景里用户购买欲望最强。\n"
            "5. **因果论证** — 因为 X（技术/设计/工艺/产地），所以 Y（用户的具体好处）。"
            "避免有果无因、有因无果、弱因弱果。\n"
            "6. **细节工艺** — 刻画细节以体现匠心。用数词、名词、事实，不用形容词。\n"
            "7. **权威背书** — 人（明星/专家/老板）、场（发布会/工厂/大景）、数据（检测报告/销量）。\n"
            "\n"
            "### 硬约束（违反即失败）\n"
            "- **禁止把知识库按条罗列**。每一句口播都必须服务于选定的主脊，不能是对 KB 的平铺复述。\n"
            "- **产品客观描述（参数/功能/配方）不得超过全文篇幅的 20%**。其余 80% 留给主脊的说服推进。\n"
            "- **禁止多手法并列**。不要既讲 USP 又讲痛点又讲权威——选 1 个深讲，其他作为辅证。\n"
            "- **主脊选择依据 KB 里最锋利的那个点**。如果 KB 里有强痛点数据，就走痛点；有独家工艺，就走 USP/细节；有达人/检测报告，就走权威。\n"
        )
    return (
        "## Persuasion Technique (choose exactly ONE as the spine)\n"
        "Pick ONE of the 7 techniques below as the spine for the entire script. "
        "Every line must serve that spine — other knowledge-base content is only "
        "supporting evidence, never a parallel narrative:\n"
        "\n"
        "1. **USP Differentiation** — one unique value point (craft/design/ingredient/philosophy/niche) "
        "that no competitor offers. Logic = 1 USP + several supporting proofs.\n"
        "2. **Pain-Point Resolution** — surface the pain (explicit or latent) → agitate → present solution → show the effect. "
        "Explaining the *mechanism* of the pain works best.\n"
        "3. **Category vs.** — old category (A) has flaw X → new category (B) doesn't have X → B's new value Y. "
        "The comparison target must be big, painful, and relevant.\n"
        "4. **Scenario Embedding** — place the product in a seasonal/need/aspirational scenario where buying intent is strongest.\n"
        "5. **Cause & Effect** — because of X (tech/design/craft/origin), you get specific benefit Y. "
        "Avoid effect-without-cause, cause-without-effect, and weak cause-effect links.\n"
        "6. **Craft Details** — depict concrete details to convey quality. Use numbers, nouns, facts. No adjectives.\n"
        "7. **Authority** — people (celebrities/experts/founders), places (launches/factories/grand settings), "
        "data (lab reports, sales figures).\n"
        "\n"
        "### Hard constraints (violations = failure)\n"
        "- **Never enumerate the knowledge base line-by-line**. Every spoken line must advance the chosen spine, "
        "not summarize KB entries in sequence.\n"
        "- **Objective product description (specs/features/ingredients) must not exceed 20%** of the total. "
        "The remaining 80% drives the persuasion spine forward.\n"
        "- **Do not mix multiple techniques**. Don't do USP + pain + authority in parallel — pick one, go deep, "
        "others are supporting evidence only.\n"
        "- **Choose the spine based on the sharpest angle in the KB**: strong pain data → pain-point spine; "
        "proprietary craft → USP/detail; testimonials or lab reports → authority; and so on.\n"
    )


def _shot_description_spec(is_zh: bool) -> str:
    """Spec for writing a detailed, production-grade shot description. Used
    both for per-section ``visual_direction`` and ``broll_plan[].prompt`` so
    the LLM produces cinematography-level depth, not "show product" bullet
    slop. Based on real ad-agency shot list conventions.
    """
    if is_zh:
        return (
            "\n## 镜头描述规范（visual_direction 与 broll_plan.prompt 都必须达到这个深度）\n"
            "\n每一段镜头描述必须依次覆盖以下 6 个维度，缺一不可：\n"
            "1. **景别 + 运镜**：中景 / 特写 / 近景 / 全景 / 微距，搭配 固定 / 推进 / 跟随 / 摇移 / 升降。\n"
            "2. **场景与光线**：具体环境（浴室 / 厨房 / 户外 / 办公桌前...）+ 时段（深夜 / 清晨 / 午后）+ 光质（明亮自然光 / 侧顶柔光 / 冷白镜光 / 逆光剪影）。\n"
            "3. **主体动作与表情**：谁、在做什么具体动作、手部姿态、微表情（眉头微蹙 / 自信微笑 / 惊讶睁眼...）。\n"
            "4. **产品/道具呈现**：品牌文字朝向镜头、手持方式、纹理质感（泡沫如云朵、液体挂壁、粉末扬起...）。\n"
            "5. **画面质感**：景深虚化 / 柔焦 / 色调（暖金 / 冷白 / 哑光）/ 后期质感（高保真度、商业级品质、4K）。\n"
            "6. **情绪锚点**：这段画面让观众感受到什么（挫败、期待、惊喜、松弛、自信）。\n"
            "\n### 好范例（套用任何品类，长度必须至少 80 字）\n"
            "> \"中景，深夜浴室，年轻女性站在镜前审视皮肤。镜中她的脸暗沉油腻，她眉头微蹙、指尖抚过脸颊，显露出困扰。"
            "背景冷白色瓷砖泛着镜光，侧顶柔光勾勒轮廓。景深虚化，色调略偏冷。"
            "高保真度、商业级品质、皮肤纹理清晰可见。\"\n"
            "\n### 禁止写法（LLM 常见偷懒）\n"
            "- \"展示产品的画面\" / \"一个人在使用产品\" / \"产品的特写\"（空泛）\n"
            "- 少于 60 字的描述（信息量不够）\n"
            "- 只写「什么」不写「怎么拍」（缺景别/光线）\n"
            "- 与口播内容无关（必须服务当前这句话的情绪或信息）\n"
            "\n### 视觉手法锚定（每段 visual_direction 必须采用 1 种并体现在描述里）\n"
            "下列 8 种视觉手法选 1 种作为本段画面的核心表达：\n"
            "- **同框** — 人/场/物与产品出现在同一画面，借他者属性赋能产品\n"
            "- **超级效果** — 一个「看了就想要」的镜头，是 wow 级而非 good 级\n"
            "- **符号动作** — 一个能放大卖点、制造停留的具象动作（合理的反常）\n"
            "- **对比** — 前后 / 左右 / 同屏 / 分屏；能一镜到底就不剪辑\n"
            "- **重复** — 关键视觉元素重复出现以构建记忆点\n"
            "- **实验** — 有说服力、可视觉化、可过审的实验（眼见为实）\n"
            "- **共情** — 洞察少被谈及的集体潜意识，引发情绪共鸣\n"
            "- **蒙太奇** — 多镜头拼接暗示效果或叙事故事\n"
        )
    return (
        "\n## Shot description spec (both visual_direction AND broll_plan.prompt must hit this depth)\n"
        "\nEvery shot description MUST cover these 6 dimensions in order — none may be skipped:\n"
        "1. **Framing + camera move**: wide / medium / close-up / extreme close-up / macro + static / push-in / pull-back / tracking / pan / tilt.\n"
        "2. **Setting + lighting**: concrete location (bathroom / kitchen / outdoor / desk...) + time of day (late night / dawn / afternoon) + light quality (bright natural / soft key + rim / cold overhead / backlit silhouette).\n"
        "3. **Subject action + micro-expression**: who is doing exactly what, hand gestures, face (furrowed brow / quiet smile / slight squint...).\n"
        "4. **Product / prop beat**: brand name facing camera, how it's held, texture cues (cloud-soft foam, liquid clinging to glass, powder plume...).\n"
        "5. **Visual quality**: depth of field, color palette (warm gold / cold white / matte), post treatment (high fidelity, commercial grade, 4K).\n"
        "6. **Emotional anchor**: what the viewer should feel from this beat (frustration, anticipation, relief, confidence).\n"
        "\n### Reference example (fits any category; minimum 50 words — aim for 3-4 sentences)\n"
        "> \"Medium shot, late-night bathroom. A young woman studies her reflection. Her skin looks dull and oily; "
        "she furrows her brow and runs her fingertip along her cheek, visibly bothered. Cold white tiles catch the mirror's reflection; "
        "a soft overhead key light rakes across her face. Shallow depth of field, slight cool tint. "
        "High fidelity, commercial grade, skin texture clearly visible.\"\n"
        "\n### Banned patterns (common LLM laziness in English)\n"
        "- Passive / distant voice: \"The scene shows...\", \"The camera captures...\", \"Various shots of...\", \"A shot of...\" — write it as a concrete director would on a shot list, not as a passive observer.\n"
        "- \"Someone using it\" / \"A person doing X\" / \"Close-up of the item\" — vague placeholders, no framing or light.\n"
        "- Fewer than 30 words, or a single sentence (not enough information for an AI image/video model to render).\n"
        "- Says WHAT but not HOW to shoot it (missing framing, lighting, or depth of field).\n"
        "- Not tied to the line of narration (every shot must serve the current beat's emotion or info).\n"
        "\n### Visual technique anchor (every visual_direction must pick ONE and show it)\n"
        "Choose exactly 1 of the 8 techniques below as the core visual move for each shot:\n"
        "- **Co-framing** — person/place/object sharing the frame with the product, transferring attributes\n"
        "- **Super effect** — a shot that makes the viewer want it immediately (wow, not good)\n"
        "- **Signature action** — a specific, slightly unexpected action that amplifies a selling point and holds attention\n"
        "- **Contrast** — before/after, left/right, split-screen, on-frame; prefer one uncut shot over edits when possible\n"
        "- **Repetition** — a key visual element recurring to build memory\n"
        "- **Experiment** — a convincing, platform-safe visual test (seeing is believing)\n"
        "- **Empathy** — tap a rarely-spoken collective subtext to trigger emotional resonance\n"
        "- **Montage** — sequential cuts that imply an effect or tell a story across time/place\n"
    )


def _build_json_schema(platform: ScriptPlatform, structure: ScriptStructure) -> str:
    """Build the JSON output schema instruction (video only)."""
    section_ids = structure.section_ids
    is_video = platform.is_video
    is_zh = platform.region == "zh"

    if is_zh:
        example_section = (
            '{"text": "口播文字，即说出口的内容", '
            '"visual_direction": "中景，明亮的现代浴室，年轻女性手持品牌洗面奶管，银色盖朝上，品牌文字清晰面向镜头。'
            '她面带自然微笑，产品举至胸前；背景柔和白色大理石瓷砖，侧方柔光。景深虚化，色调温暖。'
            '高保真度、商业级品质。", '
            '"duration_seconds": 5}'
        )
    else:
        example_section = (
            '{"text": "The spoken narration text", '
            '"visual_direction": "Medium shot, bright modern bathroom. A woman holds the brand cleanser tube with silver cap, '
            'brand name clearly facing camera. She smiles naturally, product lifted chest-high. Soft white marble tiles behind her, '
            'key light from the side. Shallow depth of field, warm tone. High fidelity, commercial grade.", '
            '"duration_seconds": 5}'
        )

    sections_example = {sid: json.loads(example_section) for sid in section_ids}

    # B-roll plan — AI director decides WHEN and WHY to cut
    if is_zh:
        broll_plan_example = [
            {"type": "retention", "insert_after_char": 0, "duration_seconds": 5,
             "prompt": (
                "特写推进镜头，画面从浴室镜面模糊的水雾中推入，逐渐聚焦到产品瓶身；"
                "银色瓶盖在顶光下反射出冷光斑，品牌文字从失焦到锐利。背景大理石纹路虚化成暖色景深。"
                "商业级 4K，慢动作 0.75 倍速，高保真度。"
             )},
            {"type": "illustrative", "insert_after_char": 42,
             "duration_seconds": 6, "prompt": (
                "微距特写，双手挤出产品到湿润手掌，质地如奶油绵密。镜头环绕 180° 跟随手掌搓揉，"
                "泡沫从少到多如云朵堆起；光线柔侧光，水珠挂在指尖，背景柔焦白雾。商业级画质，质感清晰。"
             )},
        ]
        broll_instruction = (
            "\n\nbroll_plan 编排规则（你是 AI 编导，根据文案内容智能编排视觉节奏）：\n"
            "\n核心原则：短视频每 8-12 秒**必须**有一次视觉变化，否则画面"
            "进入死寂期，观众划走。B-roll 是最强的视觉变化手段。\n\n"
            "编排策略（按 estimated_total_seconds 算数量，**严格按这个数量**，"
            "不要多也不要少 —— 多了总 B-roll 时长会超过口播时长，被服务端强制截断）：\n"
            "- **视频 30 秒以下：** 正好 2 个 B-roll（1 retention + 1 illustrative）\n"
            "- **视频 30-60 秒：** 正好 3 个 B-roll（1 retention + 2 illustrative）\n"
            "- **视频 60-90 秒：** 正好 4 个 B-roll（1 retention + 3 illustrative）\n"
            "- **视频 90 秒以上：** 正好 5 个 B-roll（1 retention + 4 illustrative）\n"
            "\n分布铁律（违反会让中段变成视觉死寂）：\n"
            "- retention 放视频开头：insert_after_char=0，5 秒，视觉冲击\n"
            "- illustrative 之间的**相邻时间间隔不得超过 12 秒**。按 chars_per_sec ≈ 5 估算，"
            "相邻 illustrative 的 insert_after_char 差不得超过 60 字\n"
            "- 例：60 秒视频 313 字口播，3 个 illustrative 合理分布在 char ≈ 60 / 150 / 240 附近"
            "（对应时间 ≈ 12s / 30s / 47s，gap 约 17s / 17s / 13s，每段 6s broll 后实际 gap 缩到 11s-11s-7s）\n"
            "- CTA 段（最后一段的 insert_after_char 区间）不放 B-roll，保持数字人面对面\n"
            "\n技术约束（⚠️ 违反会导致 B-roll 被服务端自动修正或丢弃）：\n"
            "- **duration_seconds 必须是 5-10 的整数**。retention 固定 5 秒，illustrative 5-7 秒\n"
            "- **insert_after_char 必须是纯整数**（0 到口播总字数之间）。"
            "**严禁**写成中文句子、口播原文、描述性短语、英文字母——服务端会尝试匹配口播找位置，"
            "匹配不到直接丢弃该条 B-roll。正确示例：`\"insert_after_char\": 14`；"
            "错误示例（会被拒绝或容错修正，质量不保证）：`\"insert_after_char\": \"脸上花了大几千\"`\n"
            "- 快速自检：你生成完 broll_plan 后，把每个 insert_after_char 想象成数到口播"
            "第几个字符的位置，0 到 len(所有 text 拼接) 之间的一个数字；不是任何文字。\n"
            "- prompt 要具体描述一个 AI 可生成的画面场景，retention 强调冲击力（extreme close-up,"
            "slow motion, visually striking），illustrative 服务口播内容（产品界面、数据图表、场景氛围、"
            "动作特写、物品展示等），避免含人脸\n"
        )
    else:
        broll_plan_example = [
            {"type": "retention", "insert_after_char": 0, "duration_seconds": 5,
             "prompt": (
                "Extreme close-up push-in: camera drifts from a fogged mirror surface into sharp focus on the product bottle. "
                "The silver cap catches an overhead rim-light, brand lettering sharpens from blur to crisp. "
                "Marble grain behind goes warm and out of focus. Commercial-grade 4K, 0.75x slow-motion, high fidelity."
             )},
            {"type": "illustrative", "insert_after_char": 42,
             "duration_seconds": 6, "prompt": (
                "Macro close-up. Hands squeeze the cream into a wet palm, texture rich like whipped cream. "
                "Camera arcs 180° around the hands as they lather; foam builds from sparse to cloud-like. "
                "Soft side-key, water droplets clinging to fingertips, background a soft white mist. Commercial grade, texture readable."
             )},
        ]
        broll_instruction = (
            "\n\nbroll_plan rules (you are the AI director — plan visual rhythm for the script):\n"
            "\nCore principle: short videos MUST have a visual change every 8-12 seconds. "
            "Longer gaps turn the frame into a dead zone and viewers scroll.\n\n"
            "Count the B-rolls based on ``estimated_total_seconds`` — produce **exactly** "
            "this many, no more, no less (going over makes total B-roll duration exceed the "
            "narration and the server will trim the trailing entries):\n"
            "- Video under 30s: exactly 2 (1 retention + 1 illustrative)\n"
            "- Video 30-60s: exactly 3 (1 retention + 2 illustrative)\n"
            "- Video 60-90s: exactly 4 (1 retention + 3 illustrative)\n"
            "- Video 90s+: exactly 5 (1 retention + 4 illustrative)\n"
            "\nDistribution rules (violating these creates visual dead zones):\n"
            "- retention goes at insert_after_char=0, duration 5s, visually striking opener\n"
            "- illustrative inserts must NEVER be more than 12 seconds apart. At the typical "
            "5 chars/sec speaking pace, that means adjacent illustrative ``insert_after_char`` values "
            "should differ by no more than 60 characters\n"
            "- Example: a 60s / 313-char script supports 3 illustratives at char ≈ 60 / 150 / 240 "
            "(times ≈ 12s / 30s / 47s → 6s cuts leave gaps ≈ 11s / 11s / 7s)\n"
            "- Never place B-roll inside the CTA (final) section — keep the avatar face-to-camera\n"
            "\nTechnical constraints (⚠️ violations will be auto-corrected or DROPPED on the server):\n"
            "- **duration_seconds must be an integer 5-10**. Retention fixed at 5; illustrative 5-7.\n"
            "- **insert_after_char MUST be a plain integer** (0 to total narration length). "
            "The server parses it as a number — **do NOT write a sentence, narration excerpt, "
            "or descriptive phrase**. The server will try substring-matching narration as a fallback, "
            "but if that fails, the B-roll is dropped entirely.\n"
            "- Correct: `\"insert_after_char\": 26`. "
            "Wrong (quality not guaranteed): `\"insert_after_char\": \"thousands on skincare\"`.\n"
            "- Sanity check: every insert_after_char is a number between 0 and len(all section texts joined).\n"
            "- prompts: retention → ``extreme close-up, slow motion, visually striking``; "
            "illustrative → serves the narration (product UI, data chart, scene, action close-up, "
            "object reveal). Avoid human faces.\n"
        )

    schema_obj = {
        "platform_id": platform.id,
        "structure_id": structure.id,
        "content_type": platform.content_type,
        "estimated_total_seconds": "（视频总秒数）" if is_zh else "(total video seconds)",
        "sections": sections_example,
        "broll_plan": broll_plan_example,
    }

    shot_spec = _shot_description_spec(is_zh)

    if is_zh:
        instruction = (
            "输出格式要求：必须输出合法的JSON，结构如下（不要输出任何JSON以外的内容）：\n"
            f"```json\n{json.dumps(schema_obj, ensure_ascii=False, indent=2)}\n```\n\n"
            f"sections中必须包含以下key：{section_ids}，每个key对应上面的字段结构。\n"
            f"{broll_instruction}"
            f"{shot_spec}"
        )
    else:
        instruction = (
            "Output format: you MUST output valid JSON only (no text outside the JSON). Structure:\n"
            f"```json\n{json.dumps(schema_obj, ensure_ascii=False, indent=2)}\n```\n\n"
            f"sections MUST contain these keys: {section_ids}, each matching the field structure above.\n"
            f"{broll_instruction}"
            f"{shot_spec}"
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

    # Layer 5b: PERSUASION (spine + anti-KB-dump constraint)
    # Distilled from a pro content-marketing course. Orthogonal to STRUCTURE:
    # structure = time-line (hook → problem → CTA), persuasion = logic of the spine
    # (USP / pain / category-vs / scenario / cause-effect / detail / authority).
    # Without this layer the LLM tends to flatly enumerate KB entries — the exact
    # "monotonous, KB-piled, >>20% product description" symptom reported in the wild.
    layers.append(_persuasion_technique_spec(is_zh))

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
