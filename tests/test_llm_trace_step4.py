import asyncio
import copy
import uuid

from app.adapters.ai import AnthropicMessagesAdapter, OpenAICompatibleAdapter
from app.application.llm_trace_service import prune_llm_traces


class _FakeScalarListResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.commits = 0

    async def execute(self, stmt):
        self.calls.append(stmt)
        if self._results:
            return _FakeScalarListResult(self._results.pop(0))
        return _FakeScalarListResult([])

    async def commit(self):
        self.commits += 1


class _FakeResponse:
    def __init__(self, content, usage=None, reasoning_content=None, reasoning=None):
        message_attrs = {"content": content}
        if reasoning_content is not None:
            message_attrs["reasoning_content"] = reasoning_content
        if reasoning is not None:
            message_attrs["reasoning"] = reasoning
        self.choices = [type("Choice", (), {"message": type("Message", (), message_attrs)()})()]
        self.usage = usage or {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


class _UnsupportedResponseFormatError(Exception):
    def __init__(self):
        self.status_code = 400
        super().__init__("response_format not supported")


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise _UnsupportedResponseFormatError()
        return _FakeResponse('{"ok": true}')


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


class _ModelNotFound503(Exception):
    status_code = 503

    def __str__(self):
        return "Error code: 503 - {'error': {'code': 'model_not_found', 'message': '模型 claude-opus-4-7_0.2 不存在'}}"


class _FailingCompletions:
    def __init__(self, error):
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


class _FailingClient:
    def __init__(self, error):
        self.chat = type("Chat", (), {"completions": _FailingCompletions(error)})()


class _StaticCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _StaticClient:
    def __init__(self, response):
        self.chat = type("Chat", (), {"completions": _StaticCompletions(response)})()


class _FakeAnthropicPostResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAnthropicPostClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *args, **kwargs):
        return _FakeAnthropicPostResponse(self._payload)


class _FakeStreamResponse:
    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield 'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"plan"}}'
        yield "data: [DONE]"


class _FakeStreamContext:
    async def __aenter__(self):
        return _FakeStreamResponse()

    async def __aexit__(self, *_exc):
        return False


class _FakeAnthropicClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def stream(self, *args, **kwargs):
        return _FakeStreamContext()


def test_prune_llm_traces_unions_expired_and_overflow_ids():
    expired = [uuid.uuid4(), uuid.uuid4()]
    overflow = [expired[1], uuid.uuid4()]
    session = _FakeSession([expired, overflow, []])

    deleted = asyncio.run(prune_llm_traces(session, retention_days=7, max_count=1))

    assert deleted == 3
    assert session.commits == 1
    assert len(session.calls) == 3
    assert session.calls[-1].__class__.__name__ == "Delete"


def test_chat_json_fallback_merges_attempts_into_one_trace(monkeypatch):
    import app.adapters.ai as ai_mod

    snapshots = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-1"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        adapter._trace_defer_finish = False
        return adapter._trace_id

    async def _fake_finish(adapter, response_text, usage=None):
        if getattr(adapter, "_trace_defer_finish", False):
            adapter._trace_deferred_response_text = response_text
            adapter._trace_deferred_usage = usage
            return
        snapshots.append(
            {
                "response_text": response_text,
                "attempt_log": copy.deepcopy(getattr(adapter, "_trace_attempt_log", [])),
                "fallbacks": copy.deepcopy(getattr(adapter, "_trace_fallbacks", [])),
            }
        )

    async def _fake_fail(_adapter, error):
        raise AssertionError(f"unexpected trace failure: {error}")

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_finish_trace", _fake_finish)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)

    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.model = "test-model"
    adapter.provider = "test"
    adapter.client = _FakeClient()

    result = asyncio.run(adapter._chat_json("system", "user", temperature=0.2))

    assert result == {"ok": True}
    assert len(snapshots) == 1
    assert [item["mode"] for item in snapshots[0]["attempt_log"]] == [
        "chat_json_response_format",
        "chat",
    ]
    assert snapshots[0]["fallbacks"] == [
        {
            "from": "json_object",
            "to": "prompt_only_json",
            "reason": "response_format_unsupported",
        }
    ]


def test_chat_trace_captures_openai_reasoning_content(monkeypatch):
    import app.adapters.ai as ai_mod

    snapshots = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-openai"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        return adapter._trace_id

    async def _fake_finish(_adapter, response_text, usage=None):
        snapshots.append((response_text, usage))

    async def _fake_fail(_adapter, error):
        raise AssertionError(f"unexpected trace failure: {error}")

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_finish_trace", _fake_finish)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)

    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.model = "test-model"
    adapter.provider = "test"
    adapter.client = _StaticClient(_FakeResponse("final answer", reasoning_content="plan first"))

    result = asyncio.run(adapter._chat("system", "user", temperature=0.2))

    assert result == "final answer"
    assert snapshots == [("<think>plan first</think>final answer", {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8})]


def test_openai_model_not_found_503_is_not_retried_for_chat(monkeypatch):
    """one-api may wrap permanent model config errors in HTTP 503.

    These must fail immediately instead of spending two more retry rounds on
    a model that will never exist.
    """
    import app.adapters.ai as ai_mod

    failed = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-model-missing"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        return adapter._trace_id

    async def _fake_fail(_adapter, error):
        failed.append(str(error))

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)

    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.model = "claude-opus-4-7_0.2"
    adapter.provider = "openai"
    adapter.client = _FailingClient(_ModelNotFound503())

    try:
        asyncio.run(adapter._chat("system", "user"))
    except _ModelNotFound503:
        pass
    else:
        raise AssertionError("expected model_not_found error")

    assert len(adapter.client.chat.completions.calls) == 1
    assert failed and "model_not_found" in failed[0]


def test_openai_model_not_found_503_is_not_retried_for_stream(monkeypatch):
    import app.adapters.ai as ai_mod

    failed = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-model-missing-stream"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        return adapter._trace_id

    async def _fake_fail(_adapter, error):
        failed.append(str(error))

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)

    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.model = "claude-opus-4-7_0.2"
    adapter.provider = "openai"
    adapter.client = _FailingClient(_ModelNotFound503())

    async def _collect():
        async for _chunk in adapter._chat_stream("system", "user"):
            pass

    try:
        asyncio.run(_collect())
    except _ModelNotFound503:
        pass
    else:
        raise AssertionError("expected model_not_found error")

    assert len(adapter.client.chat.completions.calls) == 1
    assert failed and "model_not_found" in failed[0]


def test_anthropic_chat_trace_captures_thinking_blocks(monkeypatch):
    import app.adapters.ai as ai_mod
    import httpx

    snapshots = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-anthropic"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        return adapter._trace_id

    async def _fake_finish(_adapter, response_text, usage=None):
        snapshots.append((response_text, usage))

    async def _fake_fail(_adapter, error):
        raise AssertionError(f"unexpected trace failure: {error}")

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_finish_trace", _fake_finish)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAnthropicPostClient(
            {
                "content": [
                    {"type": "thinking", "thinking": "plan"},
                    {"type": "text", "text": "done"},
                ],
                "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
            }
        ),
    )

    adapter = AnthropicMessagesAdapter.__new__(AnthropicMessagesAdapter)
    adapter.model = "claude-test"
    adapter.provider = "anthropic"
    adapter.base_url = "http://example.test"
    adapter._headers = {}

    result = asyncio.run(adapter._chat("sys", "user"))

    assert result == "done"
    assert snapshots == [("<think>plan</think>done", {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18})]


def test_anthropic_stream_closes_think_block(monkeypatch):
    import app.adapters.ai as ai_mod
    import httpx

    snapshots = []

    async def _fake_start(adapter, *_args, **_kwargs):
        adapter._trace_id = "trace-2"
        adapter._trace_attempt_count = 0
        adapter._trace_attempt_log = []
        adapter._trace_fallbacks = []
        return adapter._trace_id

    async def _fake_finish(_adapter, response_text, usage=None):
        snapshots.append((response_text, usage))

    async def _fake_fail(_adapter, error):
        raise AssertionError(f"unexpected trace failure: {error}")

    monkeypatch.setattr(ai_mod, "_start_trace", _fake_start)
    monkeypatch.setattr(ai_mod, "_finish_trace", _fake_finish)
    monkeypatch.setattr(ai_mod, "_fail_trace", _fake_fail)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAnthropicClient)

    adapter = AnthropicMessagesAdapter.__new__(AnthropicMessagesAdapter)
    adapter.model = "claude-test"
    adapter.provider = "anthropic"
    adapter.base_url = "http://example.test"
    adapter._headers = {}

    chunks = []

    async def _collect():
        async for chunk in adapter._chat_stream("sys", "user"):
            chunks.append(chunk)

    asyncio.run(_collect())

    assert chunks == ["<think>", "plan", "</think>"]
    assert snapshots == [("<think>plan</think>", None)]
