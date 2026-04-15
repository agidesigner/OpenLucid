"""Script writing goal definitions — strategic intent that shapes CTA strength and hook intensity."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScriptGoal:
    id: str
    name_zh: str
    name_en: str
    emoji: str
    prompt_fragment_zh: str   # injected into system prompt (ZH)
    prompt_fragment_en: str   # injected into system prompt (EN)

    def localized_name(self, lang: str) -> str:
        if lang == "en":
            return self.name_en
        return self.name_zh

    def prompt_fragment(self, lang: str) -> str:
        if lang.startswith("en"):
            return self.prompt_fragment_en
        return self.prompt_fragment_zh


_GOALS: dict[str, ScriptGoal] = {}


def _register(g: ScriptGoal) -> None:
    _GOALS[g.id] = g


_register(ScriptGoal(
    id="seeding",
    name_zh="种草",
    name_en="Seeding",
    emoji="🌱",
    prompt_fragment_zh=(
        "内容目标是种草（激发购买欲望，但不强迫成交）。"
        "策略：痛点先行，用场景和感受打动人心，让产品好处自然浮现。"
        "CTA语气要软——引导关注、收藏或了解，而不是立刻购买。"
        "成功标准：听完之后观众想要了解更多，或者有「这个说的就是我」的共鸣。"
    ),
    prompt_fragment_en=(
        "Goal: seeding (build desire, not pressure to buy immediately). "
        "Strategy: lead with the pain point, win through scenes and feelings, let the benefits emerge naturally. "
        "CTA should be soft — encourage follow, save, or learn more, not buy now. "
        "Success: the viewer thinks 'I need to know more about this' or 'this is exactly me'."
    ),
))

_register(ScriptGoal(
    id="conversion",
    name_zh="引导成交",
    name_en="Conversion",
    emoji="💳",
    prompt_fragment_zh=(
        "内容目标是引导成交（促成立即购买行动）。"
        "策略：利益前置，强调稀缺性或限时优惠，用社会证明建立信任，结尾CTA要明确具体。"
        "CTA要强——例如：点击链接立刻购买、评论区回复「买了」等直接引导购买的话术。"
        "成功标准：听完之后观众有强烈的'我现在就要买'的冲动。"
    ),
    prompt_fragment_en=(
        "Goal: drive immediate conversion (purchase now). "
        "Strategy: lead with benefits, emphasize scarcity or time-limited offer, use social proof to build trust, end with a strong specific CTA. "
        "CTA should be strong and direct — 'click the link to buy now', 'comment WANT and I'll DM you the link'. "
        "Success: the viewer feels a strong urge to buy right now."
    ),
))

_register(ScriptGoal(
    id="knowledge_sharing",
    name_zh="知识分享",
    name_en="Knowledge Sharing",
    emoji="📚",
    prompt_fragment_zh=(
        "内容目标是知识分享（传递有价值的信息，建立专业信任）。"
        "策略：给观众一个可以带走的洞见或方法，重视实用性和可操作性。"
        "CTA要轻——引导关注以获取更多干货，或者引导评论讨论。"
        "成功标准：听完之后观众觉得学到了有价值的东西，并且信任你的专业度。"
    ),
    prompt_fragment_en=(
        "Goal: knowledge sharing (deliver valuable information, build credibility). "
        "Strategy: give the viewer one clear takeaway they can use. Prioritize practical, actionable content. "
        "CTA should be light — follow for more useful content, or comment to discuss. "
        "Success: the viewer feels they learned something valuable and trusts your expertise."
    ),
))

_register(ScriptGoal(
    id="brand_awareness",
    name_zh="品牌传播",
    name_en="Brand Awareness",
    emoji="🏷️",
    prompt_fragment_zh=(
        "内容目标是品牌传播（让更多人记住品牌、建立品牌印象）。"
        "策略：聚焦品牌的独特价值主张和情感联结，而不是产品功能列表。让人记住一个词、一种感觉或一个故事。"
        "CTA要自然——关注品牌账号、转发分享。"
        "成功标准：听完之后观众记住了品牌的某个特点，或产生了正面的品牌联想。"
    ),
    prompt_fragment_en=(
        "Goal: brand awareness (make more people remember and form an impression of the brand). "
        "Strategy: focus on the brand's unique value proposition and emotional connection, not a feature list. Make them remember one word, one feeling, or one story. "
        "CTA should be organic — follow the brand account, share with friends. "
        "Success: the viewer remembers something specific about the brand, or forms a positive brand association."
    ),
))

_register(ScriptGoal(
    id="lead_generation",
    name_zh="获取线索",
    name_en="Lead Generation",
    emoji="📋",
    prompt_fragment_zh=(
        "内容目标是获取线索（让潜在客户主动留下联系方式或表达意向）。"
        "策略：展示专业能力，提供一个有价值的免费资源或咨询机会作为钩子。"
        "CTA要具体——例如：评论区留下你的问题、私信我领取免费报告、点击链接预约免费咨询。"
        "成功标准：观众感到「我需要这个服务」，并且愿意迈出第一步联系你。"
    ),
    prompt_fragment_en=(
        "Goal: lead generation (get potential customers to express interest or share contact info). "
        "Strategy: demonstrate expertise and offer a valuable free resource or consultation as a hook. "
        "CTA should be specific — 'comment your question below', 'DM me to get the free report', 'click the link to book a free consultation'. "
        "Success: the viewer feels 'I need this service' and is willing to take the first step to reach out."
    ),
))

_register(ScriptGoal(
    id="reach_growth",
    name_zh="涨粉",
    name_en="Audience Growth",
    emoji="📈",
    prompt_fragment_zh=(
        "内容目标是涨粉（增加关注者，扩大账号影响力）。"
        "策略：提供独特价值，让新观众有理由关注——独家视角、持续干货、特定垂直领域的专注。"
        "CTA要针对新关注，例如：点击关注，我每天分享XX干货；关注我，下一期讲XX。"
        "成功标准：新观众看完后觉得这个博主有干货、值得关注。"
    ),
    prompt_fragment_en=(
        "Goal: grow the audience (gain new followers, expand account reach). "
        "Strategy: provide unique value that gives new viewers a reason to follow — exclusive perspective, consistent useful content, clear niche focus. "
        "CTA should target new follows — 'follow me, I share [topic] every day', 'follow along, next video covers [topic]'. "
        "Success: new viewers feel 'this creator has value, worth following'."
    ),
))


def list_goals() -> list[ScriptGoal]:
    return list(_GOALS.values())


def get_goal(goal_id: str) -> ScriptGoal | None:
    return _GOALS.get(goal_id)


DEFAULT_GOAL_ID = "seeding"
