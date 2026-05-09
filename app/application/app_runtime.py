from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError

SessionFactory = Callable[[], Any]
AppHandler = Callable[["AppRunRequest", SessionFactory], Awaitable[str]]
_APP_ACTIONS: dict[tuple[str, str], AppHandler] = {}


@dataclass(frozen=True)
class AppRunRequest:
    app_id: str
    action: str
    offer_id: str
    strategy_unit_id: str | None = None
    language: str = "zh-CN"
    config_id: str | None = None
    question: str = ""
    style_id: str = "professional"
    topic: str = ""
    goal: str = "reach_growth"
    tone: str = ""
    word_count: int | None = None
    cta: str = ""
    industry: str = ""
    reference: str = ""
    extra_req: str = ""
    platform_id: str | None = None
    persona_id: str | None = None
    structure_id: str | None = None
    goal_id: str | None = None
    topic_plan_id: str | None = None
    external_context_text: str | None = None
    external_context_url: str | None = None


def register_app_action(app_id: str, action: str) -> Callable[[AppHandler], AppHandler]:
    def _decorator(handler: AppHandler) -> AppHandler:
        _APP_ACTIONS[(app_id, action)] = handler
        return handler

    return _decorator


async def run_openlucid_app(req: AppRunRequest, session_factory: SessionFactory) -> str:
    handler = _APP_ACTIONS.get((req.app_id, req.action))
    if handler is None:
        actions = sorted(action for app_id, action in _APP_ACTIONS if app_id == req.app_id)
        if actions:
            raise AppError(
                "UNKNOWN_ACTION",
                f"Unknown action '{req.action}' for {req.app_id}. Available: {', '.join(actions)}",
                400,
            )
        available = sorted({app_id for app_id, _ in _APP_ACTIONS})
        raise AppError("UNKNOWN_APP", f"Unknown app_id '{req.app_id}'. Available: {available}", 400)
    return await handler(req, session_factory)


def _serialize(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")
    elif isinstance(obj, list):
        data = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in obj]
    else:
        data = obj
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _resolve_word_count(word_count: int | None, platform_id: str | None) -> int:
    if word_count is not None:
        return word_count
    if platform_id:
        from app.application.script_platforms import get_platform

        platform = get_platform(platform_id)
        if platform and platform.max_script_chars:
            raw = round(platform.max_script_chars * 0.6 / 50) * 50
            return max(50, min(5000, raw))
    return 150


def _offer_id(req: AppRunRequest) -> uuid.UUID:
    return uuid.UUID(req.offer_id)


def _strategy_unit_id(req: AppRunRequest) -> uuid.UUID | None:
    return uuid.UUID(req.strategy_unit_id) if req.strategy_unit_id else None


async def _run_script_generate(req: AppRunRequest, session: AsyncSession) -> str:
    from app.application.script_writer_service import (
        DEFAULT_SYSTEM_PROMPT_EN,
        DEFAULT_SYSTEM_PROMPT_ZH,
        ScriptWriterService,
    )
    from app.schemas.app import ScriptWriterRequest

    sys_prompt = DEFAULT_SYSTEM_PROMPT_EN if req.language.startswith("en") else DEFAULT_SYSTEM_PROMPT_ZH
    svc = ScriptWriterService(session)
    data = ScriptWriterRequest(
        offer_id=_offer_id(req),
        strategy_unit_id=_strategy_unit_id(req),
        system_prompt=sys_prompt,
        topic=req.topic,
        goal=req.goal,
        tone=req.tone or None,
        word_count=_resolve_word_count(req.word_count, req.platform_id),
        cta=req.cta or None,
        industry=req.industry or None,
        reference=req.reference or None,
        extra_req=req.extra_req or None,
        language=req.language,
        config_id=req.config_id,
        platform_id=req.platform_id,
        persona_id=req.persona_id,
        structure_id=req.structure_id,
        goal_id=req.goal_id,
        topic_plan_id=uuid.UUID(req.topic_plan_id) if req.topic_plan_id else None,
        external_context_text=req.external_context_text or None,
        external_context_url=req.external_context_url or None,
        source_app="mcp:external",
    )
    result = await svc.generate(data)
    return json.dumps(result, ensure_ascii=False, indent=2)


@register_app_action("kb_qa", "ask")
async def _run_kb_qa_ask(req: AppRunRequest, session_factory: SessionFactory) -> str:
    from app.application.kb_qa_service import KBQAService
    from app.schemas.app import KBQAAskRequest

    async with session_factory() as session:
        svc = KBQAService(session)
        result = await svc.ask(
            KBQAAskRequest(
                offer_id=_offer_id(req),
                question=req.question,
                style_id=req.style_id,
                language=req.language,
                config_id=req.config_id,
            )
        )
        return _serialize(result)


@register_app_action("script_writer", "suggest_topic")
async def _run_script_writer_suggest_topic(req: AppRunRequest, session_factory: SessionFactory) -> str:
    from app.application.script_writer_service import ScriptWriterService

    async with session_factory() as session:
        svc = ScriptWriterService(session)
        topic_text = await svc.suggest_topic(
            offer_id=req.offer_id,
            strategy_unit_id=req.strategy_unit_id,
            goal=req.goal,
            language=req.language,
            config_id=req.config_id,
        )
        return json.dumps({"topic": topic_text}, ensure_ascii=False)


@register_app_action("script_writer", "generate")
async def _run_script_writer_generate(req: AppRunRequest, session_factory: SessionFactory) -> str:
    async with session_factory() as session:
        return await _run_script_generate(req, session)


@register_app_action("content_studio", "generate")
async def _run_content_studio_generate(req: AppRunRequest, session_factory: SessionFactory) -> str:
    async with session_factory() as session:
        return await _run_script_generate(req, session)


@register_app_action("topic_studio", "generate")
async def _run_topic_studio_generate(req: AppRunRequest, session_factory: SessionFactory) -> str:
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanResponse

    async with session_factory() as session:
        svc = TopicPlanService(session)
        data = TopicPlanGenerateRequest(
            offer_id=_offer_id(req),
            strategy_unit_id=_strategy_unit_id(req),
            count=req.word_count if (req.word_count is not None and req.word_count <= 20) else 5,
            language=req.language,
            config_id=req.config_id,
        )
        plans, thinking, hotspot = await svc.generate(data)
        await session.commit()
        serialized = [
            TopicPlanResponse.model_validate(plan, from_attributes=True).model_dump(mode="json")
            for plan in plans
        ]
        result: dict[str, Any] = {"plans": serialized, "thinking": thinking}
        if hotspot is not None:
            result["hotspot"] = hotspot.model_dump(mode="json", exclude_none=True)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
