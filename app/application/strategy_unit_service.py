import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.infrastructure.offer_repo import OfferRepository
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.models.strategy_unit import StrategyUnit
from app.schemas.strategy_unit import StrategyUnitCreate, StrategyUnitUpdate

logger = logging.getLogger(__name__)


# Fallback when we can't / won't call an LLM (all inputs blank, or the
# adapter raises). Produces a concise, deterministic name from whatever
# signal the user did give us.
def _fallback_name(
    audience_segment: str | None,
    scenario: str | None,
    marketing_objective: str | None,
) -> str:
    parts = [p for p in (audience_segment, scenario, marketing_objective) if p and p.strip()]
    if not parts:
        return "未命名策略单元"
    # Chinese en-dash-ish separator. Truncate to keep it single-line friendly.
    joined = " · ".join(p.strip() for p in parts)
    return joined[:60]


async def _ai_summarize_name(
    session: AsyncSession,
    audience_segment: str | None,
    scenario: str | None,
    marketing_objective: str | None,
) -> str:
    """Ask the configured LLM for a short strategy-unit name built from
    (audience + scenario + objective). Returns a deterministic fallback
    if no LLM is configured or the call fails — the caller never sees
    an exception, because a missing name shouldn't block creation."""
    from app.adapters.ai import OpenAICompatibleAdapter, get_ai_adapter

    fallback = _fallback_name(audience_segment, scenario, marketing_objective)

    # All three empty → nothing meaningful for the LLM to compress.
    if not any((audience_segment, scenario, marketing_objective)):
        return fallback

    try:
        # No scene key: this is a lightweight internal name generator,
        # not a user-surfaced scene. Uses the active default LLM.
        adapter = await get_ai_adapter(session)
    except Exception as e:
        logger.warning("AI name summarize: adapter acquisition failed (%s) — using fallback", e)
        return fallback
    if not isinstance(adapter, OpenAICompatibleAdapter):
        return fallback

    system = (
        "你是营销策略助手。用户会给你一个策略单元的三个维度："
        "目标人群（audience）、使用场景（scenario）、营销目标（objective）。"
        "请生成一个简洁、具体、自然的中文名称（8-16 字），能够一眼识别这个策略单元是做什么的。"
        "只输出名称本身，不要引号、不要解释、不要前后缀、不要 emoji。"
        "If the inputs are in English, respond in English with a 3-6 word name."
    )
    user = "\n".join([
        f"audience: {audience_segment or '（未填写）'}",
        f"scenario: {scenario or '（未填写）'}",
        f"objective: {marketing_objective or '（未填写）'}",
    ])
    try:
        raw = await adapter._chat(system, user, temperature=0.4, max_tokens=64)
    except Exception as e:
        logger.warning("AI name summarize: _chat failed (%s) — using fallback", e)
        return fallback

    # Defensive parse — LLMs occasionally return multi-line answers,
    # quote-wrapped names, or a "名称：X" label despite instructions.
    if not raw:
        return fallback
    lines = raw.strip().splitlines()
    if not lines:
        return fallback
    name = lines[0].strip()
    for prefix in ("名称：", "名称:", "Name:", "name:"):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    name = name.strip('"“”\'「」『』 ')
    if not name:
        return fallback
    return name[:60]


class StrategyUnitService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = StrategyUnitRepository(session)
        self.offer_repo = OfferRepository(session)

    async def create(self, data: StrategyUnitCreate) -> StrategyUnit:
        offer = await self.offer_repo.get_by_id(data.offer_id)
        if not offer:
            raise NotFoundError("Offer", str(data.offer_id))
        payload = data.model_dump()
        name = (payload.get("name") or "").strip()
        if not name:
            name = await _ai_summarize_name(
                self.session,
                payload.get("audience_segment"),
                payload.get("scenario"),
                payload.get("marketing_objective"),
            )
        payload["name"] = name
        return await self.repo.create(**payload)

    async def get(self, unit_id: uuid.UUID) -> StrategyUnit:
        unit = await self.repo.get_by_id(unit_id)
        if not unit:
            raise NotFoundError("StrategyUnit", str(unit_id))
        return unit

    async def list(
        self,
        offer_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[StrategyUnit], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(offer_id=offer_id, merchant_id=merchant_id, offset=offset, limit=page_size)

    async def update(self, unit_id: uuid.UUID, data: StrategyUnitUpdate) -> StrategyUnit:
        unit = await self.get(unit_id)
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return unit
        return await self.repo.update(unit, **update_data)

    async def delete(self, unit_id: uuid.UUID) -> None:
        unit = await self.get(unit_id)
        await self.repo.delete(unit)
