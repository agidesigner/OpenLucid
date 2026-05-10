from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _Scalars(self._items)


@dataclass
class _StoredOverride:
    user_id: str
    preset_key: str
    title: str
    category: str
    lang: str
    content: str
    updated_at: datetime


class _FakePresetSession:
    def __init__(self):
        self.rows: dict[tuple[str, str], _StoredOverride] = {}

    async def execute(self, stmt):
        stmt_name = type(stmt).__name__
        where = {
            criterion.left.name: criterion.right.value
            for criterion in getattr(stmt, "_where_criteria", [])
        }

        if stmt_name == "Select":
            if where.get("preset_key"):
                row = self.rows.get((where["user_id"], where["preset_key"]))
                return _ScalarResult(row)
            user_id = where.get("user_id")
            rows = [row for (uid, _), row in self.rows.items() if uid == user_id]
            return _ExecuteResult(rows)

        if stmt_name == "Delete":
            user_id = where.get("user_id")
            preset_key = where.get("preset_key")
            if user_id is not None and preset_key is not None:
                deleted = 1 if self.rows.pop((user_id, preset_key), None) else 0
            else:
                targets = [key for key in self.rows if key[0] == user_id]
                deleted = len(targets)
                for key in targets:
                    self.rows.pop(key, None)
            return SimpleNamespace(rowcount=deleted)

        raise AssertionError(f"Unexpected statement type: {stmt_name}")

    def add(self, row):
        now = row.updated_at or datetime.now(timezone.utc)
        row.updated_at = now
        stored = _StoredOverride(
            user_id=str(row.user_id),
            preset_key=row.preset_key,
            title=row.title,
            category=row.category,
            lang=row.lang,
            content=row.content,
            updated_at=now,
        )
        self.rows[(stored.user_id, stored.preset_key)] = stored

    async def commit(self):
        return None

    async def refresh(self, row):
        stored = self.rows[(str(row.user_id), row.preset_key)]
        row.updated_at = stored.updated_at
        return None


class _FakeLookupSession:
    def __init__(self, rows: dict[tuple[str, str], str], should_fail: bool = False):
        self.rows = rows
        self.should_fail = should_fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def scalar(self, stmt):
        if self.should_fail:
            raise RuntimeError("db down")
        where = {
            criterion.left.name: criterion.right.value
            for criterion in getattr(stmt, "_where_criteria", [])
        }
        return self.rows.get((where["user_id"], where["preset_key"]))


class _FakeSessionFactory:
    def __init__(self, rows: dict[tuple[str, str], str], should_fail: bool = False):
        self.rows = rows
        self.should_fail = should_fail

    def __call__(self):
        return _FakeLookupSession(self.rows, should_fail=self.should_fail)


@pytest.mark.asyncio
async def test_get_effective_prompt_is_user_isolated_and_falls_back(monkeypatch):
    from app import context as ctx

    factory = _FakeSessionFactory({
        ("user-a", "script.base.zh"): "override-a",
        ("user-b", "script.base.zh"): "override-b",
    })

    import app.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    token = ctx.current_user_id.set("user-a")
    try:
        assert await ctx.get_effective_prompt("script.base.zh", lambda: "default") == "override-a"
        assert await ctx.get_effective_prompt("script.base.en", lambda: "default-en") == "default-en"
    finally:
        ctx.current_user_id.reset(token)

    token = ctx.current_user_id.set("user-b")
    try:
        assert await ctx.get_effective_prompt("script.base.zh", lambda: "default") == "override-b"
    finally:
        ctx.current_user_id.reset(token)

    assert await ctx.get_effective_prompt("script.base.zh", lambda: "anonymous-default") == "anonymous-default"


@pytest.mark.asyncio
async def test_get_effective_prompt_returns_default_when_lookup_fails(monkeypatch):
    from app import context as ctx

    import app.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", _FakeSessionFactory({}, should_fail=True))

    token = ctx.current_user_id.set("user-a")
    try:
        assert await ctx.get_effective_prompt("topic.viral_signals.zh", lambda: "fallback") == "fallback"
    finally:
        ctx.current_user_id.reset(token)


@pytest.mark.asyncio
async def test_save_and_reset_prompt_preset_persists_then_restores_default():
    from app.application.prompt_preset_service import (
        get_prompt_preset,
        reset_all_prompt_presets,
        reset_prompt_preset,
        save_prompt_preset,
    )

    db = _FakePresetSession()
    saved = await save_prompt_preset(db, "user-a", "topic.viral_signals.zh", "我的覆盖")
    assert saved.user_content == "我的覆盖"
    assert saved.is_modified is True

    refreshed = await get_prompt_preset(db, "user-a", "topic.viral_signals.zh")
    assert refreshed is not None
    assert refreshed.user_content == "我的覆盖"
    assert refreshed.is_modified is True

    other_user = await get_prompt_preset(db, "user-b", "topic.viral_signals.zh")
    assert other_user is not None
    assert other_user.user_content is None
    assert other_user.is_modified is False

    reset_one = await reset_prompt_preset(db, "user-a", "topic.viral_signals.zh")
    assert reset_one.user_content is None
    assert reset_one.is_modified is False

    await save_prompt_preset(db, "user-a", "topic.viral_signals.zh", "再次覆盖")
    await save_prompt_preset(db, "user-a", "script.base.zh", "脚本覆盖")
    cleared = await reset_all_prompt_presets(db, "user-a")
    assert cleared == 2

    after_clear = await get_prompt_preset(db, "user-a", "script.base.zh")
    assert after_clear is not None
    assert after_clear.user_content is None
    assert after_clear.is_modified is False


def test_runtime_wiring_uses_expected_preset_keys():
    import app.application.script_composer as script_composer
    import app.application.image_service as image_service
    import app.application.kb_qa_service as kb_qa_service
    import app.apps.kb_qa_styles as kb_qa_styles
    from app.adapters.ai import OpenAICompatibleAdapter

    compose_src = inspect.getsource(script_composer.compose_system_prompt)
    assert "script.base.zh" in compose_src
    assert "script.base.en" in compose_src
    assert "script.persuasion.zh" in compose_src
    assert "script.persuasion.en" in compose_src
    assert "script.shot_description.zh" in compose_src
    assert "script.shot_description.en" in compose_src
    assert "get_effective_prompt" in compose_src

    for fn_name, preset_key in (
        ("_build_brief_prompt", "image.brief_template"),
        ("_build_refine_prompt", "image.refine_template"),
        ("_build_cover_derive_prompt", "image.cover_derive"),
    ):
        src = inspect.getsource(getattr(image_service, fn_name))
        assert preset_key in src
        assert "get_effective_prompt" in src

    infer_src = inspect.getsource(OpenAICompatibleAdapter.infer_knowledge)
    assert "_get_infer_knowledge_system_prompt" in infer_src

    suggest_src = inspect.getsource(OpenAICompatibleAdapter.suggest_brand_voice)
    assert "brandkit.voice_suggest.en" in suggest_src
    assert "brandkit.voice_suggest.zh" in suggest_src
    assert "get_effective_prompt" in suggest_src

    style_helper_src = inspect.getsource(kb_qa_styles.get_style_system_prompt_prefix)
    assert "kb_qa.style." in style_helper_src
    assert "get_effective_prompt" in style_helper_src

    prepare_src = inspect.getsource(kb_qa_service.KBQAService._prepare)
    assert "get_style_system_prompt_prefix" in prepare_src