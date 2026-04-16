import json

import pytest

from app.adapters.ai import OpenAICompatibleAdapter


class DummyOpenAIAdapter(OpenAICompatibleAdapter):
    def __init__(self):
        self.provider = "test"
        self.model = "test-model"
        self.calls = []

    async def _chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.8, max_tokens: int = 16384) -> str:
        self.calls.append((system_prompt, user_prompt, temperature, max_tokens))
        return json.dumps(
            {
                "description": "中文描述",
                "selling_point": [],
                "audience": [],
                "scenario": [],
                "faq": [],
                "objection": [],
            },
            ensure_ascii=False,
        )

    async def _chat_stream(self, system_prompt: str, user_prompt: str, temperature: float = 0.8, timeout: float = 180):
        self.calls.append((system_prompt, user_prompt, temperature, timeout))
        yield json.dumps(
            {
                "description": "中文描述",
                "selling_point": [],
                "audience": [],
                "scenario": [],
                "faq": [],
                "objection": [],
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_infer_knowledge_uses_requested_zh_language_in_prompt():
    adapter = DummyOpenAIAdapter()

    await adapter.infer_knowledge(
        {
            "offer": {"name": "测试商品", "description": "这是一款防晒霜"},
            "selling_points": [],
            "target_audiences": [],
            "target_scenarios": [],
            "knowledge_items": [],
        },
        language="zh-CN",
    )

    _, user_prompt, _, _ = adapter.calls[0]
    assert "商品名称：测试商品" in user_prompt
    assert "商品描述：这是一款防晒霜" in user_prompt
    assert "Product name:" not in user_prompt


@pytest.mark.asyncio
async def test_infer_knowledge_stream_uses_requested_zh_language_in_prompt():
    adapter = DummyOpenAIAdapter()

    events = []
    async for event in adapter.infer_knowledge_stream(
        {
            "offer": {"name": "测试商品", "description": "这是一款防晒霜"},
            "selling_points": [],
            "target_audiences": [],
            "target_scenarios": [],
            "knowledge_items": [],
        },
        language="zh-CN",
    ):
        events.append(event)

    assert events[-1][0] == "result"
    _, user_prompt, _, _ = adapter.calls[0]
    assert "商品名称：测试商品" in user_prompt
    assert "商品描述：这是一款防晒霜" in user_prompt
    assert "Product name:" not in user_prompt
