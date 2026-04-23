"""Tests for app.libs.lang_detect — the single source of truth for
KB-language-centric output resolution.

Covers:
  - cjk_ratio arithmetic
  - detect_text_language on realistic inputs
  - matches() family comparison
  - resolve_output_language policy: follows KB by default, honors
    manual override when the user explicitly picked in UI
"""
from __future__ import annotations


class TestCjkRatio:
    def test_empty(self):
        from app.libs.lang_detect import cjk_ratio
        assert cjk_ratio("") == 0.0
        assert cjk_ratio(None) == 0.0

    def test_pure_english(self):
        from app.libs.lang_detect import cjk_ratio
        assert cjk_ratio("Hello world") == 0.0

    def test_pure_chinese(self):
        from app.libs.lang_detect import cjk_ratio
        assert cjk_ratio("你好世界") == 1.0

    def test_mixed(self):
        from app.libs.lang_detect import cjk_ratio
        # 2 CJK chars, 6 ASCII = 2/8 non-whitespace = 0.25
        r = cjk_ratio("abc 你好 xyz")
        assert 0.2 < r < 0.3

    def test_whitespace_ignored(self):
        from app.libs.lang_detect import cjk_ratio
        # Same non-whitespace chars → same ratio regardless of spacing
        assert cjk_ratio("abc") == cjk_ratio("a b c")


class TestDetectTextLanguage:
    def test_all_english_kb(self):
        from app.libs.lang_detect import detect_text_language
        text = (
            "Jogg AI is an AI-powered avatar video generator. Upload a "
            "script or product URL, pick an avatar, and get a finished "
            "video in minutes."
        )
        assert detect_text_language(text) == "en"

    def test_all_chinese_kb(self):
        from app.libs.lang_detect import detect_text_language
        text = (
            "蝉镜 AI 是一款数字人视频生成工具，用户可以上传脚本或产品链接，"
            "选择一位数字人形象，几分钟内生成完整视频。"
        )
        assert detect_text_language(text) == "zh-CN"

    def test_english_with_chinese_brand_names(self):
        from app.libs.lang_detect import detect_text_language
        text = (
            "Jogg AI competes with Synthesia, HeyGen, and 蝉镜. "
            "Each differs in pricing, language coverage, and avatar "
            "customization across US and Asia markets."
        )
        assert detect_text_language(text) == "en"

    def test_too_short(self):
        from app.libs.lang_detect import detect_text_language
        assert detect_text_language("") is None
        assert detect_text_language("短文本") is None
        assert detect_text_language("abc") is None


class TestMatches:
    def test_same_family(self):
        from app.libs.lang_detect import matches
        assert matches("zh-CN", "zh-CN") is True
        assert matches("zh", "zh-CN") is True
        assert matches("zh-TW", "zh-CN") is True
        assert matches("en", "en") is True
        assert matches("en-US", "en") is True

    def test_different_family(self):
        from app.libs.lang_detect import matches
        assert matches("zh-CN", "en") is False
        assert matches("en", "zh-CN") is False

    def test_none_inputs(self):
        from app.libs.lang_detect import matches
        assert matches(None, "en") is False
        assert matches("en", None) is False
        assert matches(None, None) is False


class TestResolveOutputLanguage:
    """The system-wide policy that wires it all together."""

    EN_TEXT = (
        "Jogg AI is an AI-powered avatar video generator with support "
        "for multiple languages and product demo use cases."
    )
    ZH_TEXT = (
        "蝉镜 AI 是一款数字人视频生成工具，覆盖营销、教育、企业培训"
        "等多个场景，口播和视觉本地化能力是它的核心优势。"
    )

    def test_english_kb_overrides_chinese_request(self):
        from app.libs.lang_detect import resolve_output_language
        assert resolve_output_language("zh-CN", self.EN_TEXT) == "en"

    def test_chinese_kb_overrides_english_request(self):
        from app.libs.lang_detect import resolve_output_language
        assert resolve_output_language("en", self.ZH_TEXT) == "zh-CN"

    def test_matching_kb_passes_through(self):
        """When request and KB already agree, return requested as-is
        (don't rewrite zh → zh-CN cosmetically)."""
        from app.libs.lang_detect import resolve_output_language
        assert resolve_output_language("zh-CN", self.ZH_TEXT) == "zh-CN"
        assert resolve_output_language("en", self.EN_TEXT) == "en"

    def test_manual_override_wins_over_detection(self):
        """User picked en in the UI; KB is Chinese; honor user."""
        from app.libs.lang_detect import resolve_output_language
        assert (
            resolve_output_language("en", self.ZH_TEXT, manual_override=True)
            == "en"
        )
        assert (
            resolve_output_language("zh-CN", self.EN_TEXT, manual_override=True)
            == "zh-CN"
        )

    def test_empty_kb_falls_back_to_request(self):
        from app.libs.lang_detect import resolve_output_language
        assert resolve_output_language("zh-CN", "") == "zh-CN"
        assert resolve_output_language("en", None) == "en"

    def test_undetectable_kb_falls_back_to_request(self):
        """Below the 30-char threshold, detector returns None."""
        from app.libs.lang_detect import resolve_output_language
        assert resolve_output_language("zh-CN", "abc") == "zh-CN"


class TestInferKnowledgeLanguage:
    """The `infer_offer_knowledge` path (AI smart update / create-wizard
    KB generation) has no UI language picker. The brief text itself is
    the signal — a Chinese UI uploading an English brief must produce
    English knowledge items, not Chinese."""

    def test_english_brief_overrides_chinese_ui(self):
        from app.api.ai import _infer_language_from_body
        from app.schemas.ai import InferOfferKnowledgeRequest
        body = InferOfferKnowledgeRequest(
            name="Jogg AI",
            description=(
                "Jogg AI is an AI-powered avatar video generator. "
                "Upload a script or product URL, pick an avatar, and "
                "get a finished video in minutes. Marketers use it for "
                "social ads, product explainers, and training content."
            ),
            language="zh-CN",
        )
        assert _infer_language_from_body(body) == "en"

    def test_chinese_brief_overrides_english_ui(self):
        from app.api.ai import _infer_language_from_body
        from app.schemas.ai import InferOfferKnowledgeRequest
        body = InferOfferKnowledgeRequest(
            name="蝉镜AI",
            description=(
                "蝉镜 AI 是一款数字人视频生成工具，用户可以上传脚本或产品链接，"
                "选择一位数字人形象，几分钟内生成完整视频。"
            ),
            language="en",
        )
        assert _infer_language_from_body(body) == "zh-CN"

    def test_existing_kb_also_contributes(self):
        """Update flow: no brief text, but the offer already has English
        KB items. Must still pick English despite zh-CN request."""
        from app.api.ai import _infer_language_from_body
        from app.schemas.ai import ExistingKnowledgeItem, InferOfferKnowledgeRequest
        body = InferOfferKnowledgeRequest(
            name="Jogg AI",
            description="",
            language="zh-CN",
            existing_knowledge=[
                ExistingKnowledgeItem(
                    knowledge_type="selling_point",
                    title="Fast avatar generation",
                    content_raw=(
                        "Users report generating their first avatar video in "
                        "under 5 minutes, compared to 30+ minutes for tools "
                        "that require manual green-screen shoots or scripting."
                    ),
                ),
            ],
        )
        assert _infer_language_from_body(body) == "en"

    def test_sparse_signal_falls_back_to_requested(self):
        """Only a name, too short for detection → keep requested lang."""
        from app.api.ai import _infer_language_from_body
        from app.schemas.ai import InferOfferKnowledgeRequest
        body = InferOfferKnowledgeRequest(name="X", description="", language="zh-CN")
        assert _infer_language_from_body(body) == "zh-CN"


class TestSchemaLanguageOverrideFlag:
    def test_kbqa_request_defaults_false(self):
        from app.schemas.app import KBQAAskRequest
        import uuid
        r = KBQAAskRequest(offer_id=uuid.uuid4(), question="what is this?")
        assert r.language_override is False

    def test_scriptwriter_request_defaults_false(self):
        from app.schemas.app import ScriptWriterRequest
        import uuid
        r = ScriptWriterRequest(offer_id=uuid.uuid4())
        assert r.language_override is False

    def test_topic_plan_request_defaults_false(self):
        from app.schemas.topic_plan import TopicPlanGenerateRequest
        import uuid
        r = TopicPlanGenerateRequest(offer_id=uuid.uuid4())
        assert r.language_override is False

    def test_all_accept_true(self):
        from app.schemas.app import KBQAAskRequest, ScriptWriterRequest
        from app.schemas.topic_plan import TopicPlanGenerateRequest
        import uuid
        oid = uuid.uuid4()
        assert KBQAAskRequest(offer_id=oid, question="q", language_override=True).language_override is True
        assert ScriptWriterRequest(offer_id=oid, language_override=True).language_override is True
        assert TopicPlanGenerateRequest(offer_id=oid, language_override=True).language_override is True
