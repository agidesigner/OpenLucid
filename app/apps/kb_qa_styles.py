from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StyleTemplate:
    style_id: str
    name: str
    description: str
    icon: str
    system_prompt_prefix: str
    name_en: str = ""
    description_en: str = ""

    def localized(self, lang: str) -> StyleTemplate:
        """Return a copy with name/description resolved to the given language."""
        if lang == "en" and self.name_en:
            import copy
            out = copy.copy(self)
            out.name = self.name_en
            out.description = self.description_en or self.description
            return out
        return self


STYLE_TEMPLATES: dict[str, StyleTemplate] = {}


def _register(t: StyleTemplate) -> None:
    STYLE_TEMPLATES[t.style_id] = t


_register(StyleTemplate(
    style_id="professional",
    name="专业顾问",
    description="条理清晰、用语专业",
    icon="🎓",
    system_prompt_prefix="你是一位专业顾问，回答应条理清晰、用语专业、有理有据。请用编号列表组织要点。",
    name_en="Professional Advisor",
    description_en="Well-structured and professionally worded",
))

_register(StyleTemplate(
    style_id="friendly",
    name="亲切客服",
    description="语气友善、通俗易懂",
    icon="😊",
    system_prompt_prefix="你是一位亲切的客服，回答应语气友善、通俗易懂、有亲和力。用简单的语言解释，避免过多术语。",
    name_en="Friendly Support",
    description_en="Approachable and easy to understand",
))

_register(StyleTemplate(
    style_id="expert",
    name="产品专家",
    description="有深度、善于类比",
    icon="🔬",
    system_prompt_prefix="你是一位产品专家，回答应有深度、善于用类比和举例帮助用户理解。可以适当展开技术细节。",
    name_en="Product Expert",
    description_en="In-depth with great analogies",
))

DEFAULT_STYLE_ID = "professional"
