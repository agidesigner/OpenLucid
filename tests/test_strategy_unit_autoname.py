"""Tests for the optional-name auto-generation on StrategyUnit create.

Covers the pure-logic fallback (deterministic, no LLM), the schema change
allowing name=None, and the service-level behavior that substitutes a
generated name when the input is blank.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


class TestFallbackName:
    """Deterministic fallback used when LLM isn't available or all
    signal fields are empty."""

    def test_all_blank_returns_sentinel(self):
        from app.application.strategy_unit_service import _fallback_name
        assert _fallback_name(None, None, None) == "未命名策略单元"
        assert _fallback_name("", "", "") == "未命名策略单元"
        assert _fallback_name("   ", None, "") == "未命名策略单元"

    def test_single_field(self):
        from app.application.strategy_unit_service import _fallback_name
        assert _fallback_name("宝妈", None, None) == "宝妈"
        assert _fallback_name(None, "下班通勤", None) == "下班通勤"
        assert _fallback_name(None, None, "conversion") == "conversion"

    def test_joined_with_dot(self):
        from app.application.strategy_unit_service import _fallback_name
        # Separator is " · " (Unicode middle dot between spaces)
        assert _fallback_name("宝妈", "下班", "转化") == "宝妈 · 下班 · 转化"

    def test_clipped_to_sixty_chars(self):
        from app.application.strategy_unit_service import _fallback_name
        result = _fallback_name("x" * 200, None, None)
        assert len(result) == 60


class TestSchemaOptionalName:
    def test_create_without_name(self):
        from app.schemas.strategy_unit import StrategyUnitCreate
        payload = StrategyUnitCreate(
            merchant_id=uuid.uuid4(),
            offer_id=uuid.uuid4(),
            audience_segment="宝妈",
        )
        assert payload.name is None

    def test_create_with_explicit_name(self):
        from app.schemas.strategy_unit import StrategyUnitCreate
        payload = StrategyUnitCreate(
            merchant_id=uuid.uuid4(),
            offer_id=uuid.uuid4(),
            name="My unit",
        )
        assert payload.name == "My unit"

    def test_name_too_long_rejected(self):
        from app.schemas.strategy_unit import StrategyUnitCreate
        with pytest.raises(ValueError):
            StrategyUnitCreate(
                merchant_id=uuid.uuid4(),
                offer_id=uuid.uuid4(),
                name="x" * 300,
            )


class TestAiSummarizeName:
    """_ai_summarize_name must never raise — on any failure path it
    silently returns the deterministic fallback so the create endpoint
    stays 200-able even when the LLM is down."""

    def test_all_blank_skips_llm_and_returns_fallback(self):
        """When the three signal fields are all empty, the helper must
        short-circuit before even trying to fetch an adapter."""
        from app.application import strategy_unit_service as svc

        called = {"adapter": False}

        async def _spy(*a, **kw):
            called["adapter"] = True
            raise AssertionError("should not be called")

        # Patch the factory to verify no LLM call is made.
        import app.adapters.ai as ai_mod
        original = ai_mod.get_ai_adapter
        ai_mod.get_ai_adapter = _spy
        try:
            result = asyncio.run(svc._ai_summarize_name(None, None, None, None))
        finally:
            ai_mod.get_ai_adapter = original

        assert result == "未命名策略单元"
        assert called["adapter"] is False

    def test_adapter_exception_returns_fallback(self):
        from app.application import strategy_unit_service as svc
        import app.adapters.ai as ai_mod

        async def _boom(*a, **kw):
            raise RuntimeError("no LLM configured")

        original = ai_mod.get_ai_adapter
        ai_mod.get_ai_adapter = _boom
        try:
            result = asyncio.run(
                svc._ai_summarize_name(None, "宝妈", "下班通勤", "conversion")
            )
        finally:
            ai_mod.get_ai_adapter = original

        # Fallback built from the three signals, order preserved.
        assert result == "宝妈 · 下班通勤 · conversion"

    def test_llm_result_trimmed(self):
        """Strip surrounding quotes and whitespace; drop a 'Name:' prefix
        if the model ignored the 'no prefix' instruction."""
        from app.application import strategy_unit_service as svc
        import app.adapters.ai as ai_mod

        class _FakeAdapter(ai_mod.OpenAICompatibleAdapter):
            def __init__(self):
                pass

            async def _chat(self, system, user, **kw):
                return '  名称：「宝妈通勤转化」  \n  extra line that must be ignored  '

        async def _factory(*a, **kw):
            return _FakeAdapter()

        original = ai_mod.get_ai_adapter
        ai_mod.get_ai_adapter = _factory
        try:
            result = asyncio.run(
                svc._ai_summarize_name(None, "宝妈", "下班通勤", "conversion")
            )
        finally:
            ai_mod.get_ai_adapter = original

        assert result == "宝妈通勤转化"

    def test_empty_llm_response_falls_back(self):
        from app.application import strategy_unit_service as svc
        import app.adapters.ai as ai_mod

        class _FakeAdapter(ai_mod.OpenAICompatibleAdapter):
            def __init__(self):
                pass

            async def _chat(self, system, user, **kw):
                return "   "  # whitespace only

        async def _factory(*a, **kw):
            return _FakeAdapter()

        original = ai_mod.get_ai_adapter
        ai_mod.get_ai_adapter = _factory
        try:
            result = asyncio.run(
                svc._ai_summarize_name(None, "宝妈", None, None)
            )
        finally:
            ai_mod.get_ai_adapter = original

        assert result == "宝妈"
