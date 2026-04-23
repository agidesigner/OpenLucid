"""Regression: suggest_topic should follow KB content language, not UI locale.

The UI passes `language` based on the user's selected locale (zh-CN by
default). When the KB content is actually English, the topic generated
from that KB should be English too — mixed-language output feels broken
to users ("I asked for ideas about my all-English product and got a
Chinese sentence back").
"""
from __future__ import annotations


class TestDetectTextLanguage:
    """Direct tests of the _detect_text_language heuristic."""

    def _fn(self):
        from app.libs.lang_detect import detect_text_language
        return detect_text_language

    def test_all_english_kb(self):
        detect = self._fn()
        text = (
            "Jogg AI is an AI-powered avatar video generator. "
            "Upload a script or product URL, pick an avatar, and "
            "get a finished video in minutes. Marketers use it for "
            "social ads, product explainers, and training content."
        )
        assert detect(text) == "en"

    def test_all_chinese_kb(self):
        detect = self._fn()
        text = (
            "蝉镜 AI 是一款数字人视频生成工具，用户可以上传脚本或产品链接，"
            "选择一位数字人形象，几分钟内生成完整视频。营销人员常用它制作"
            "社交广告、产品讲解和培训内容。它支持多语言口播和本地化视觉。"
        )
        assert detect(text) == "zh-CN"

    def test_english_with_chinese_brand_name(self):
        """A couple of CJK product names in an English KB must NOT flip
        the whole detection to Chinese."""
        detect = self._fn()
        text = (
            "Jogg AI competes with Synthesia, HeyGen, and 蝉镜. "
            "Each platform offers avatar generation but differs in "
            "pricing, language support, and avatar customization. "
            "Our research covered 20+ tools across the US and Asia markets."
        )
        assert detect(text) == "en"

    def test_mixed_content_mostly_chinese(self):
        detect = self._fn()
        text = (
            "蝉镜 AI 的核心优势在于本地化数字人。对比 Synthesia、HeyGen，"
            "我们的中文数字人发音更自然，本土场景覆盖更广。价格约为海外"
            "产品的一半。适合国内营销团队、出海品牌和企业培训部门。"
        )
        assert detect(text) == "zh-CN"

    def test_too_little_content_returns_none(self):
        detect = self._fn()
        assert detect("") is None
        assert detect("短文本") is None  # 3 CJK chars, well below threshold
        assert detect("abc") is None

    def test_none_input_returns_none(self):
        detect = self._fn()
        assert detect(None) is None
