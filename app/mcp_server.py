"""
OpenLucid MCP Server

Exposes core platform capabilities as MCP tools for AI agents.
Run with: python -m app.mcp_server
"""
from __future__ import annotations

import functools
import json
import logging
import time
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import VERSION, settings
from app.database import async_session_factory


def _build_transport_security() -> TransportSecuritySettings:
    """Build transport security from MCP_ALLOWED_HOSTS env var.
    Default: localhost only. Set MCP_ALLOWED_HOSTS=*.example.com to add domains."""
    import os
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]
    origins = [
        "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
        "http://127.0.0.1", "http://localhost", "http://[::1]",
    ]
    extra = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if extra:
        for domain in extra.split(","):
            d = domain.strip()
            if d:
                hosts.extend([f"{d}:*", d])
                origins.extend([f"https://{d}:*", f"https://{d}", f"http://{d}:*", f"http://{d}"])
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


mcp = FastMCP(
    "OpenLucid",
    transport_security=_build_transport_security(),
    instructions=(
        "OpenLucid — the user's marketing data and creation hub.\n"
        "Data model: Merchant → Offer → Knowledge / Assets / BrandKit / StrategyUnit.\n"
        "\n"
        "Read workflow: get_merchant_overview → get_offer_context_summary → "
        "list_apps + get_app_config(app_id) to discover run_app parameters → run_app "
        "(kb_qa / script_writer / topic_studio / ...).\n"
        "\n"
        "Capture workflow: whenever you produce a complete, deliverable piece of "
        "content using OpenLucid data — a post, a script, an email, a hook — call "
        "save_creation to store it back in the user's library. Don't wait for the "
        "user to say 'this works'; chat scrolls away, the library is what stays. "
        "Save every completed version (use tags like 'v1'/'v2' or a platform name "
        "to distinguish variants). Skip half-finished drafts, analysis notes, and "
        "tool call logs.\n"
        "\n"
        "Guided prompts (preferred over ad-hoc orchestration): "
        "'onboard_merchant', 'content_brief', 'blog_from_offer', "
        "'script_for_campaign', 'knowledge_gap_report'. "
        "Persistent context: attach merchant:// or offer:// resources.\n"
        "\n"
        "IMPORTANT: every *_id parameter (merchant_id, offer_id, "
        "strategy_unit_id, config_id, asset_id, ...) is a UUID, never a "
        "human-readable name. If you only know the name, discover the UUID "
        "first via the matching list_* tool (list_merchants, list_offers, "
        "list_strategy_units, ...) — do NOT pass the name directly.\n"
        "\n"
        "MERCHANT DEFAULTING (when creating an offer): do NOT call "
        "create_merchant unless the user explicitly asks for a new "
        "workspace/brand. Always call list_merchants first. If exactly "
        "one merchant exists, use it as merchant_id without asking. If "
        "multiple merchants exist, ASK the user which one — never guess "
        "or invent. If zero merchants exist, ask the user for the brand "
        "name and create one only after confirming. The same rule "
        "applies before any tool that needs a merchant_id.\n"
        "\n"
        "KNOWLEDGE INFERENCE — OpenLucid does NOT hold a preferred "
        "LLM. It holds the prompt (the 167-line marketing-knowledge "
        "discipline) and the data layer; the brain is meant to be YOU. "
        "Two equally legitimate paths to populate an offer's KB:\n"
        "  (a) PREFERRED — call get_knowledge_inference_prompt(offer_id) "
        "to fetch the system_prompt + user_message + output_schema, run "
        "it through your own LLM (whichever you're running on), then "
        "write each returned item back via add_knowledge_item with "
        "source_type=ai_inferred and source_ref=external-agent:<your-id>:"
        "<offer_id>. You stay the brain; OpenLucid contributes the "
        "discipline.\n"
        "  (b) CONVENIENCE — call create_offer(infer_knowledge=True) or "
        "infer_knowledge_for_offer(offer_id) to have OpenLucid's "
        "configured 'knowledge'-scene model run the prompt server-side. "
        "Useful when the user has no LLM credentials configured, or "
        "when they explicitly prefer the built-in model.\n"
        "Both paths produce KB rows with source_type=ai_inferred; the "
        "source_ref prefix (external-agent: vs auto-infer:) lets an "
        "audit tell them apart."
    ),
)

# Surface OpenLucid's own app version through MCP Initialize's
# serverInfo.version so clients can detect real backend upgrades (distinct from
# the MCP SDK version, which otherwise leaks as the "version" field). Clients
# should reconnect to observe tool/prompt/resource changes — FastMCP does not
# emit listChanged notifications for a static in-code catalog.
mcp._mcp_server.version = VERSION

# Module-level session factory reference; tests can monkey-patch this.
_session_factory = async_session_factory

# Strong references to fire-and-forget background tasks. asyncio only weakly
# references tasks, so without this set they may be GC'd mid-run.
_BACKGROUND_TASKS: set = set()


# Known-bad / placeholder APP_URL hosts: if Settings hasn't been configured
# with a real public URL, any preview_url we hand to an agent would be
# unreachable and confusing. We detect and omit those URLs instead.
_APP_URL_PLACEHOLDER_HOSTS = (
    "nihao.com",       # historical test value seen in dev envs
    "example.com",
    "change-me",
)


def _app_url_looks_valid() -> bool:
    """Return True if settings.APP_URL appears to be a reachable URL that an
    agent can actually open. Reject empty, placeholder domains, or bare localhost
    (which agents running on a different machine cannot reach).
    """
    url = (settings.APP_URL or "").strip().lower()
    if not url:
        return False
    for bad in _APP_URL_PLACEHOLDER_HOSTS:
        if bad in url:
            return False
    # localhost / 127.0.0.1 are fine during dev — agents on the same host can
    # reach them. Only flag them if clearly unconfigured (empty + fallback).
    return True


# ── Offer JSON-field unwrap ────────────────────────────────────────
#
# OfferResponse / OfferUpdate keep five legacy "_json" wrapper columns
# (``core_selling_points_json``, ``target_audience_json``, ...) where the
# value is shaped like ``{"points": [...]}`` or ``{"items": [...]}``. This
# was a DB compromise; the MCP-facing creation tools already accept the
# flat list form (``core_selling_points: list[str]``). The asymmetry —
# write a list, read back a wrapper-dict — broke read→edit→write loops
# for agents and was the root cause of multiple "missing fields" bugs.
#
# This helper post-processes any serialized offer payload so the MCP
# layer presents a consistent flat shape regardless of what the DB
# stores. Schema/REST/DB are unchanged.
_OFFER_JSON_UNWRAP = {
    "core_selling_points_json": ("core_selling_points", "points"),
    "target_audience_json": ("target_audiences", "items"),
    "target_scenarios_json": ("target_scenarios", "items"),
    "objections_json": ("objections", "items"),
    "proofs_json": ("proofs", "items"),
    "secondary_objectives_json": ("secondary_objectives", "items"),
}


def _unwrap_offer_json_fields(payload: Any) -> Any:
    """Replace ``foo_json: {wrapper_key: [...]}`` with ``foo: [...]``.

    Walks dicts and lists recursively. Idempotent — fields that are
    already in flat form are passed through unchanged. Any unrecognised
    wrapper shape is left as-is rather than silently dropping data.
    """
    if isinstance(payload, list):
        return [_unwrap_offer_json_fields(x) for x in payload]
    if not isinstance(payload, dict):
        return payload
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in _OFFER_JSON_UNWRAP:
            new_key, wrapper_key = _OFFER_JSON_UNWRAP[k]
            if v is None:
                out[new_key] = None
            elif isinstance(v, dict) and wrapper_key in v and isinstance(v[wrapper_key], list):
                out[new_key] = v[wrapper_key]
            else:
                # Unknown shape — preserve the original under the new
                # name so debugging is possible without losing data.
                out[new_key] = v
        else:
            out[k] = _unwrap_offer_json_fields(v) if isinstance(v, (dict, list)) else v
    return out


def _serialize(obj: Any, schema_cls: type | None = None, *, unwrap_offer: bool = False) -> str:
    """Serialize an object to JSON string.

    If schema_cls is provided, validates the object through a Pydantic schema
    with from_attributes=True (useful for SQLAlchemy models).

    ``unwrap_offer=True`` applies ``_unwrap_offer_json_fields`` so the MCP
    output uses the same flat shape that ``create_offer`` accepts as
    input. Use for any tool that returns OfferResponse-shaped payloads.
    """
    if schema_cls is not None:
        model = schema_cls.model_validate(obj, from_attributes=True)
        data = model.model_dump(mode="json")
    elif hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")
    elif isinstance(obj, list):
        items = []
        for item in obj:
            if hasattr(item, "model_dump"):
                items.append(item.model_dump(mode="json"))
            else:
                items.append(item)
        data = items
    else:
        data = obj
    if unwrap_offer:
        data = _unwrap_offer_json_fields(data)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


logger = logging.getLogger("mcp.audit")


def _patch_mcp_tools():
    """Wrap all registered MCP tools with audit logging."""
    original_tool = mcp.tool

    @functools.wraps(original_tool)
    def logged_tool(*deco_args, **deco_kwargs):
        decorator = original_tool(*deco_args, **deco_kwargs)

        def wrapper(fn):
            @functools.wraps(fn)
            async def instrumented(**kwargs):
                tool_name = fn.__name__
                # Build compact args summary (skip empty/default values)
                args_summary = {k: v for k, v in kwargs.items()
                                if v is not None and v != "" and k not in ("page", "page_size")}
                t0 = time.monotonic()
                try:
                    result = await fn(**kwargs)
                    elapsed = time.monotonic() - t0
                    # Extract result count if available. All error paths now raise
                    # AppError (Wave 3), so we only look for {"total": N} success shapes.
                    count = ""
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict) and "total" in parsed:
                            count = f" results={parsed['total']}"
                    except Exception:
                        pass
                    logger.info("tool=%s args=%s%s duration=%.2fs",
                                tool_name, args_summary, count, elapsed)
                    return result
                except Exception as e:
                    elapsed = time.monotonic() - t0
                    logger.warning("tool=%s args=%s error=%s duration=%.2fs",
                                   tool_name, args_summary, e, elapsed)
                    raise

            return decorator(instrumented)
        return wrapper

    mcp.tool = logged_tool


_patch_mcp_tools()


# ── Merchant Tools ──────────────────────────────────────────────


@mcp.tool()
async def create_merchant(
    name: str,
    merchant_type: str = "goods",
    default_locale: str = "zh-CN",
    confirm_intent: bool = False,
) -> str:
    """Create a marketing workspace (merchant) that owns brand knowledge,
    offers, assets, and brand kits. Use this as the top-level container
    when setting up a new brand knowledge base, marketing data hub, or
    RAG source for AI content generation. merchant_type: goods | service
    | hybrid.

    GUARDRAIL (v1.1.8) — when one or more merchants already exist,
    this tool REJECTS the call unless ``confirm_intent=True``. This is
    a hard server-side enforcement of the v1.1.7 defaulting rule:
    instructions and docstrings are TEXT, easy for an agent to
    rationalize around; this gate is binary.

    The error response includes the existing merchants so the agent
    can recover by either (a) calling ``create_offer`` under one of
    them, or (b) re-calling with ``confirm_intent=True`` after the
    user explicitly asks for a brand-new workspace.

    Pass ``confirm_intent=True`` ONLY when the user has clearly asked
    for a NEW workspace/brand. A name reference like "create an offer
    for PrivacyCrop" is NOT such a request — try ``create_offer``
    against the existing merchant first."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantCreate, MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)

        # Hard gate: refuse when others exist + intent not confirmed.
        # We list a small page only — we just need to know "any?".
        existing_items, existing_total = await svc.list(page=1, page_size=20)
        if existing_total > 0 and not confirm_intent:
            return json.dumps(
                {
                    "error": "merchants_exist",
                    "existing_total": existing_total,
                    "existing": [
                        {"id": str(m.id), "name": m.name}
                        for m in existing_items
                    ],
                    "hint": (
                        "One or more merchants already exist. If the user "
                        "named a brand, call list_merchants and reuse an "
                        "existing match. To override this guard pass "
                        "confirm_intent=true — only when the user "
                        "explicitly asks for a NEW workspace/brand."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )

        data = MerchantCreate(
            name=name,
            merchant_type=merchant_type,
            default_locale=default_locale,
        )
        merchant = await svc.create(data)
        await session.commit()
        return _serialize(merchant, MerchantResponse)


@mcp.tool()
async def delete_merchant(merchant_id: str) -> str:
    """Delete a merchant (brand/workspace) and EVERYTHING under it:
    every offer, every offer's strategy_units / topic_plans /
    creations / knowledge / brandkit / assets, plus the merchant's own
    merchant-scoped knowledge / brandkit / assets. Asset files on
    disk are not removed (orphan rows are wiped, bytes linger — same
    trade-off ``delete_offer`` makes).

    DESTRUCTIVE — confirm scope with the user before calling. Use
    only when the user explicitly asks to drop a brand / workspace,
    or to clean up demo / test data. There is no recovery short of
    a database restore.

    Returns ``{"deleted": true, "id": ...}`` on success or raises
    NotFoundError when the row doesn't exist."""
    from app.application.merchant_service import MerchantService

    async with _session_factory() as session:
        svc = MerchantService(session)
        await svc.delete(uuid.UUID(merchant_id))
        await session.commit()
        return json.dumps(
            {"deleted": True, "id": merchant_id},
            ensure_ascii=False, indent=2,
        )


@mcp.tool()
async def list_merchants(page: int = 1, page_size: int = 20) -> str:
    """List marketing workspaces (merchants) / brand knowledge bases available
    for AI content generation, RAG grounding, and campaign planning. Start here
    to discover which brands / clients are set up, then drill into list_offers
    for their product/service catalog."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        items, total = await svc.list(page=page, page_size=page_size)
        serialized_items = [MerchantResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Offer Tools ─────────────────────────────────────────────────


@mcp.tool()
async def create_offer(
    name: str,
    merchant_id: str | None = None,
    offer_type: str = "product",
    description: str = "",
    positioning: str = "",
    core_selling_points: list[str] | None = None,
    target_audiences: list[str] | None = None,
    target_scenarios: list[str] | None = None,
    locale: str = "zh-CN",
    infer_knowledge: bool = False,
) -> str:
    """Create a product / service / bundle as an offer under a merchant. An
    offer is the entity you'll attach marketing knowledge, pain points, selling
    points, proofs, FAQs, audiences, scenarios, brand kits and assets to — the
    unit of grounding for AI script writing, topic generation, and RAG. Pass
    positioning + core_selling_points + target_audiences when known; they
    bootstrap the knowledge base automatically. offer_type: product | service |
    bundle | solution.

    MERCHANT_ID DISCOVERY (server-enforced as of v1.1.8) — you do NOT
    have to pass ``merchant_id``. When omitted, the server applies the
    defaulting rule directly:

    1. Exactly ONE merchant exists → auto-fill, no prompt to the user.
    2. ZERO merchants → returns ``{"error": "no_merchants"}``. Ask the
       user for the brand/workspace name and create_merchant first.
    3. MULTIPLE merchants → returns
       ``{"error": "multiple_merchants_pick_one", "merchants": [...]}``.
       Ask the user which one (don't guess by name similarity / recency /
       alphabetical — those heuristics caused dogfood incidents where an
       offer landed under the wrong brand and silently grounded content
       with the wrong KB).

    Do NOT call ``create_merchant`` reflexively just because the user
    named a brand. "Create an offer for PrivacyCrop" is a NAME REFERENCE,
    not a "create new merchant" signal — try ``create_offer`` first
    (the auto-fill handles the single-merchant case), only escalate to
    ``create_merchant`` if the user explicitly asks for a new workspace.
    ``create_merchant`` itself rejects when others exist unless you pass
    ``confirm_intent=true``.

    KNOWLEDGE INFERENCE — three legitimate ways to populate this
    offer's KB; pick by who's the brain. OpenLucid is opinionated
    about the *prompt* (167 lines at app/adapters/ai.py:61-227) but
    NOT about which LLM runs it.

    1. PREFERRED for MCP-first usage — leave ``infer_knowledge=False``
       (the default). Create the offer. Then call
       ``get_knowledge_inference_prompt(offer_id)`` to fetch the
       system_prompt + user_message + output_schema. Run them through
       YOUR OWN LLM. Write each returned item back via
       ``add_knowledge_item`` with source_type=ai_inferred,
       source_ref=external-agent:<your-id>:<offer_id>. You stay the
       brain; OpenLucid contributes the prompt discipline + the
       storage. This is how an agent + MCP product is supposed to
       work.

    2. CONVENIENCE — pass ``infer_knowledge=True``. After offer
       creation, OpenLucid runs ITS configured 'knowledge'-scene
       model server-side and writes the rows for you with
       source_ref=auto-infer:create_offer:<offer_id>. Use when the
       user has no LLM configured at the agent layer, or explicitly
       prefers the built-in model. Failure (timeout/auth/parse) is
       reported in ``inference_status`` without rolling back the
       offer; you can retry via ``infer_knowledge_for_offer``.

    3. MANUAL — ``infer_knowledge=False`` and you write
       ``add_knowledge_item`` rows yourself from your own thinking,
       no LLM-prompt-ritual. Fine for small targeted KB additions
       but the discipline of (1) usually produces more complete
       coverage across the 7 KB types.

    Default ``infer_knowledge=False`` keeps the v1.1.5 minimal-write
    semantic — older callers and chatty agents (who'll either follow
    path 1 or path 3) don't silently incur server-side LLM cost."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferCreate, OfferResponse

    async with _session_factory() as session:
        svc = OfferService(session)

        # Server-enforced merchant defaulting (v1.1.8). Doesn't matter
        # whether the agent's system prompt has the v1.1.7 rule text —
        # the server fails closed on ambiguity.
        if merchant_id is None:
            from app.application.merchant_service import MerchantService

            mvc = MerchantService(session)
            merchants, total = await mvc.list(page=1, page_size=20)
            if total == 0:
                return json.dumps(
                    {
                        "error": "no_merchants",
                        "hint": (
                            "No merchants exist yet. Ask the user for the "
                            "brand/workspace name, then call create_merchant "
                            "(it requires confirm_intent=true when other "
                            "merchants already exist; for the empty-state "
                            "this guard auto-passes)."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            if total > 1:
                return json.dumps(
                    {
                        "error": "multiple_merchants_pick_one",
                        "existing_total": total,
                        "merchants": [
                            {"id": str(m.id), "name": m.name}
                            for m in merchants
                        ],
                        "hint": (
                            "Multiple merchants exist. Ask the user which "
                            "one to put this offer under and re-call with "
                            "the chosen merchant_id. Do NOT guess by name "
                            "similarity / recency / alphabetical."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            # Exactly one — auto-fill.
            merchant_id = str(merchants[0].id)

        data = OfferCreate(
            merchant_id=uuid.UUID(merchant_id),
            name=name,
            offer_type=offer_type,
            description=description or None,
            positioning=positioning or None,
            core_selling_points_json={"points": core_selling_points} if core_selling_points else None,
            target_audience_json={"items": target_audiences} if target_audiences else None,
            target_scenarios_json={"items": target_scenarios} if target_scenarios else None,
            locale=locale,
        )
        offer = await svc.create(data)
        await session.commit()
        # Build the response payload as a dict so we can attach
        # inference_report / inference_status without re-parsing JSON.
        offer_payload = OfferResponse.model_validate(offer, from_attributes=True).model_dump(mode="json")
        offer_payload = _unwrap_offer_json_fields(offer_payload)
        offer_id_str = str(offer.id)

    if infer_knowledge:
        # New session — offer creation already committed above. AI
        # failure here is best-effort: the offer stays, the report
        # tells the agent what to do next. Same fail-open contract
        # as the REST endpoint at POST /offers/{id}/infer-knowledge.
        from app.application.knowledge_inference_service import (
            KnowledgeInferenceService,
        )

        async with _session_factory() as session2:
            ksvc = KnowledgeInferenceService(session2)
            report = await ksvc.infer_and_persist_offer_knowledge(
                uuid.UUID(offer_id_str),
                language=locale,
                trigger="create_offer",
            )
            await session2.commit()
        offer_payload["inference_report"] = report.model_dump(mode="json")

    return json.dumps(offer_payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def update_offer(
    offer_id: str,
    name: str | None = None,
    offer_type: str | None = None,
    offer_model: str | None = None,
    description: str | None = None,
    positioning: str | None = None,
    core_selling_points: list[str] | None = None,
    target_audiences: list[str] | None = None,
    target_scenarios: list[str] | None = None,
    objections: list[str] | None = None,
    proofs: list[str] | None = None,
    locale: str | None = None,
    status: str | None = None,
    clear_fields: list[str] | None = None,
) -> str:
    """Update an offer. Pass only the fields you want to change; omitted
    fields stay as-is. Returns the full updated row in the same flat
    shape as ``create_offer`` / ``list_offers``.

    Filling the gap pre-v1.1.5 forced agents to fall back to raw REST
    PATCH (or wipe + recreate) just to fix a typo, change positioning,
    or merge in a new selling point.

    List-shaped fields (``core_selling_points`` / ``target_audiences`` /
    ``target_scenarios`` / ``objections`` / ``proofs``) take a flat
    ``list[str]`` — same shape as ``create_offer`` accepts and as
    ``list_offers`` returns. The MCP layer wraps them into the
    underlying ``foo_json: {points|items: [...]}`` columns.

    To CLEAR a field (set to NULL), include its name in
    ``clear_fields`` — e.g. ``clear_fields=["description","positioning"]``.
    Passing ``None`` to a field means "leave alone", not "clear", which
    is the same convention as ``update_knowledge_item``."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferUpdate, OfferResponse

    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if offer_type is not None:
        payload["offer_type"] = offer_type
    if offer_model is not None:
        payload["offer_model"] = offer_model
    if description is not None:
        payload["description"] = description
    if positioning is not None:
        payload["positioning"] = positioning
    if core_selling_points is not None:
        payload["core_selling_points_json"] = {"points": core_selling_points}
    if target_audiences is not None:
        payload["target_audience_json"] = {"items": target_audiences}
    if target_scenarios is not None:
        payload["target_scenarios_json"] = {"items": target_scenarios}
    if objections is not None:
        payload["objections_json"] = {"items": objections}
    if proofs is not None:
        payload["proofs_json"] = {"items": proofs}
    if locale is not None:
        payload["locale"] = locale
    if status is not None:
        payload["status"] = status

    # ``clear_fields`` is the explicit "set to NULL" channel. Map flat
    # MCP names back onto the underlying ``_json`` columns.
    _CLEAR_ALIAS = {
        "core_selling_points": "core_selling_points_json",
        "target_audiences": "target_audience_json",
        "target_scenarios": "target_scenarios_json",
        "objections": "objections_json",
        "proofs": "proofs_json",
    }
    for f in (clear_fields or []):
        col = _CLEAR_ALIAS.get(f, f)
        payload[col] = None

    if not payload:
        return json.dumps(
            {"error": "no fields provided",
             "hint": "pass at least one field to update, or use clear_fields=[...] to NULL columns"},
            ensure_ascii=False, indent=2,
        )

    async with _session_factory() as session:
        svc = OfferService(session)
        data = OfferUpdate(**payload)
        offer = await svc.update(uuid.UUID(offer_id), data)
        await session.flush()
        await session.refresh(offer)
        result = _serialize(offer, OfferResponse, unwrap_offer=True)
        await session.commit()
        return result


@mcp.tool()
async def delete_offer(offer_id: str) -> str:
    """Delete an offer by UUID. Cascades to attached strategy_units,
    creations, knowledge items, assets, and brandkits via the service-
    layer cleanup. Returns ``{"deleted": true, "id": ...}`` on success
    or raises NotFoundError when the row doesn't exist.

    Use cases: drop a product line, remove a misbuilt offer
    (e.g. wrong merchant), or clean up demo data."""
    from app.application.offer_service import OfferService

    async with _session_factory() as session:
        svc = OfferService(session)
        await svc.delete(uuid.UUID(offer_id))
        await session.commit()
        return json.dumps(
            {"deleted": True, "id": offer_id},
            ensure_ascii=False, indent=2,
        )


@mcp.tool()
async def infer_knowledge_for_offer(
    offer_id: str,
    language: str | None = None,
    user_hint: str | None = None,
) -> str:
    """Convenience: have OpenLucid's CONFIGURED knowledge-scene LLM
    run the prompt against this offer + persist the resulting rows.
    Use when the user prefers the built-in model or has no agent-
    side LLM credentials.

    PREFERRED ALTERNATIVE for external-agent-driven flows: call
    ``get_knowledge_inference_prompt(offer_id)`` instead — it returns
    the same prompt OpenLucid would run server-side, but lets YOU
    run it through your own LLM (Claude / GPT / wherever). Then
    write rows back via ``add_knowledge_item`` with
    source_ref=external-agent:<your-id>:<offer_id>. That preserves
    the MCP-first design intent: OpenLucid holds the prompt
    discipline + the data layer; the external agent holds the brain.

    Provenance:
    - ``source_type=ai_inferred`` so the WebUI can show AI-vs-manual
      badges
    - ``source_ref=auto-infer:infer_knowledge_for_offer:<offer_id>``
      so an audit can tell apart create-time inferences from manual
      re-runs
    - ``confidence`` from the LLM's per-item score

    Re-running over an offer that already has KB rows is safe — the
    ``(scope_type, scope_id, knowledge_type, title)`` unique
    constraint causes same-titled rows to be updated in place rather
    than duplicated. New rows get inserted.

    Returns a ``KnowledgeInferenceReport``:
    ``{success, written_count, updated_count, by_type, model_label}``,
    or ``{success: false, reason: ...}`` when the LLM call fails
    (timeout / auth / rate limit / parse error). Hard failures
    surface NotFoundError when ``offer_id`` doesn't exist."""
    from app.application.knowledge_inference_service import (
        KnowledgeInferenceService,
    )

    async with _session_factory() as session:
        svc = KnowledgeInferenceService(session)
        report = await svc.infer_and_persist_offer_knowledge(
            uuid.UUID(offer_id),
            language=language,
            user_hint=user_hint,
            trigger="infer_knowledge_for_offer",
        )
        await session.commit()
        return json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def get_knowledge_inference_prompt(
    offer_id: str,
    language: str | None = None,
    user_hint: str | None = None,
) -> str:
    """Return the offer-knowledge-inference prompt OpenLucid would
    feed its built-in LLM, so YOU (the external agent) can run it
    through your own model and write the resulting KB rows back via
    ``add_knowledge_item``.

    This is v1.2.1's answer to the v1.2.0 architectural concern: the
    167-line system prompt at ``app/adapters/ai.py:61-227`` is the
    product asset (the marketing-knowledge-extraction discipline);
    the LLM that runs it is implementation. Pre-v1.2.1 only OpenLucid's
    configured model could see the prompt — meaning external agents
    that wanted to use their own LLM (Claude, GPT, Gemini, …) had to
    either (a) blindly write KB rows from their own freeform thinking,
    or (b) call ``infer_knowledge_for_offer`` and accept whatever
    OpenLucid's "knowledge"-scene model produced. Neither is right
    for an MCP-first product where the external agent is supposed to
    be the brain.

    Use this tool when you (the agent) want the **discipline** of
    OpenLucid's prompt but the **reasoning** of your own LLM:

    1. Call this tool — get back ``system_prompt`` + ``user_message``
       + ``output_schema`` + ``write_back_instructions``.
    2. Run them through your own LLM (Claude / GPT / your fine-tuned
       small model — your choice; whatever's strongest at structured
       extraction).
    3. Parse your LLM's JSON output. For each ``{title, content_raw,
       confidence}`` per type, call ``add_knowledge_item(scope_type=
       "offer", scope_id=offer_id, knowledge_type=<type>, ...)`` with
       ``source_type="ai_inferred"`` and ``source_ref="external-agent:
       <your_agent_id>:<offer_id>"`` — the ``external-agent:`` prefix
       distinguishes your runs from server-internal
       ``auto-infer:create_offer:...`` rows in the audit trail.

    ``language`` / ``user_hint`` mirror the corresponding params on
    ``infer_knowledge_for_offer``. ``language`` defaults to the
    offer's locale; ``user_hint`` is forwarded into the user message
    when supplied (e.g. "focus on B2B angles").

    Returns ``{system_prompt, user_message, output_schema,
    recommended_temperature, language, offer, write_back_instructions}``
    — every field the agent needs to run the prompt + write the
    output back, no more."""
    from app.adapters.prompt_builder import (
        format_existing_knowledge,
        format_offer_summary,
    )
    from app.adapters.ai import _build_infer_knowledge_system_prompt
    from app.application.knowledge_inference_service import build_offer_data
    from app.exceptions import NotFoundError
    from app.infrastructure.knowledge_repo import KnowledgeItemRepository
    from app.infrastructure.offer_repo import OfferRepository

    async with _session_factory() as session:
        offer = await OfferRepository(session).get_by_id(uuid.UUID(offer_id))
        if not offer:
            raise NotFoundError("Offer", offer_id)

        lang = language or getattr(offer, "locale", None) or "zh-CN"

        existing_items, _ = await KnowledgeItemRepository(session).list(
            scope_type="offer", scope_id=uuid.UUID(offer_id), offset=0, limit=500,
        )
        existing_payload = [
            {
                "knowledge_type": ki.knowledge_type,
                "title": ki.title,
                "content_raw": ki.content_raw or "",
            }
            for ki in existing_items
        ]

        offer_data = build_offer_data(
            offer_name=offer.name,
            offer_type=offer.offer_type,
            description=offer.description,
            existing_knowledge=existing_payload,
        )

        # Same composition the built-in adapter uses at
        # app/adapters/ai.py:1148-1153 — kept identical so the prompt
        # an external agent runs is byte-for-byte the prompt the
        # internal model sees.
        system_prompt = _build_infer_knowledge_system_prompt(lang)
        user_message = format_offer_summary(offer_data, language=lang) + format_existing_knowledge(
            existing_payload, language=lang,
        )
        if user_hint:
            user_message += f"\nAdditional notes from user: {user_hint}"

    payload = {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "output_schema": {
            "_description": (
                "Your LLM should return a JSON object with these top-level "
                "keys. Each value is a list of items; each item has title, "
                "content_raw, confidence (0..1). Empty list is fine for "
                "categories the source page doesn't address — don't fabricate."
            ),
            "selling_point": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "audience": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "scenario": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "pain_point": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "faq": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "objection": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
            "proof": [{"title": "...", "content_raw": "...", "confidence": 0.85}],
        },
        "recommended_temperature": 0.7,
        "language": lang,
        "offer": {
            "id": offer_id,
            "name": offer.name,
            "offer_type": offer.offer_type,
        },
        "write_back_instructions": (
            "For each item your LLM returns, call add_knowledge_item("
            f"scope_type='offer', scope_id='{offer_id}', "
            "knowledge_type=<type>, title=<title>, content_raw=<content_raw>, "
            "source_type='ai_inferred', "
            f"source_ref='external-agent:<your_agent_id>:{offer_id}', "
            f"confidence=<0..1>, language='{lang}'). The "
            "(scope_type, scope_id, knowledge_type, title) UNIQUE "
            "constraint means re-running won't duplicate same-titled "
            "rows; existing rows are updated in place. Skip empty-title "
            "items the LLM occasionally emits."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def list_offers(
    merchant_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List products / services / bundles (offers) — the catalog of marketing
    entities under a merchant. Each offer carries a knowledge base (selling
    points, pain points, proofs, FAQs, audiences), a brand kit, assets, and
    generated creations. Use this to discover which offer to ground AI content
    generation / RAG / script writing against. Optionally filter by
    merchant_id."""
    from app.application.offer_service import OfferService
    from app.schemas.offer import OfferResponse

    async with _session_factory() as session:
        svc = OfferService(session)
        mid = uuid.UUID(merchant_id) if merchant_id else None
        items, total = await svc.list(merchant_id=mid, page=page, page_size=page_size)
        serialized_items = [OfferResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        # Strip full description from list — agent should call get_offer_context_summary for details
        for item in serialized_items:
            item.pop("description", None)
        # Flatten ``foo_json: {wrapper: [...]}`` to ``foo: [...]`` so the
        # output matches the input shape of create_offer (read↔write
        # symmetry — see _unwrap_offer_json_fields docstring).
        serialized_items = _unwrap_offer_json_fields(serialized_items)
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def get_brandkit(
    scope_type: str,
    scope_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List brand kits / brand guidelines / brand identity specs for a scope.
    Returns style profiles (tone of voice, visual style), persona, visual do's
    and don'ts, reference prompts — the ground truth for AI content to stay
    on-brand. Use this before generating scripts, social copy, images, or video
    to ensure brand consistency. scope_type: merchant | offer."""
    from app.application.brandkit_service import BrandKitService
    from app.schemas.brandkit import BrandKitResponse

    async with _session_factory() as session:
        svc = BrandKitService(session)
        items, total = await svc.list(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            page=page,
            page_size=page_size,
        )
        serialized_items = [BrandKitResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Knowledge Tools ─────────────────────────────────────────────


@mcp.tool()
async def add_knowledge_item(
    scope_type: str,
    scope_id: str,
    title: str,
    content_raw: str = "",
    knowledge_type: str = "general",
    language: str = "zh-CN",
    source_type: str = "manual",
    source_ref: str | None = None,
    confidence: float | None = None,
    tags: str | list[str] = "",
    content: str | None = None,
) -> str:
    """Add a brand / marketing knowledge entry to a merchant or offer KB — one
    atomic fact, selling point, pain point, proof, FAQ, audience persona, or
    usage scenario. This is the source-of-truth content that script writers,
    content generators, KB QA, and RAG pipelines pull from.

    scope_type: merchant | offer.
    knowledge_type: brand | audience | scenario | selling_point | pain_point |
      objection | proof | faq | general.
    source_type: manual | ai_inferred | web_extract — provenance, used to
      decide which entries are owner-vetted vs LLM-generated.
    source_ref: where this came from (URL, document name, infer-knowledge run id).
    confidence: 0.0-1.0 — populate when ``source_type=ai_inferred`` so the UI
      can show a "verified-vs-AI-suggested" badge.
    tags: free-form tags. Accepts a comma-separated string or a list[str].

    ``content`` is a deprecated alias for ``content_raw`` kept for
    backwards-compat with calls written against the pre-1.1.3 MCP shape;
    the rename closed a read/write asymmetry where ``list_knowledge``
    returned ``content_raw`` but ``add_knowledge_item`` accepted only
    ``content``."""
    from app.application.knowledge_service import KnowledgeService
    from app.schemas.knowledge import KnowledgeItemCreate, KnowledgeItemResponse

    body = content_raw or (content or "")
    if content and not content_raw:
        logger.warning(
            "MCP add_knowledge_item: 'content' is deprecated — use 'content_raw' "
            "(matches list_knowledge / KnowledgeItemResponse field name)."
        )

    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    else:
        tag_list = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
    tags_json = {"general": tag_list} if tag_list else None

    async with _session_factory() as session:
        svc = KnowledgeService(session)
        data = KnowledgeItemCreate(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            title=title,
            content_raw=body or None,
            knowledge_type=knowledge_type,
            language=language,
            source_type=source_type,
            source_ref=source_ref,
            tags_json=tags_json,
            confidence=(
                max(0.0, min(1.0, float(confidence)))
                if confidence is not None else None
            ),
        )
        item = await svc.create(data)
        # Serialize BEFORE commit so a failure here triggers async-with's
        # rollback. The earlier commit-then-serialize order left dirty
        # rows in the DB when serialization tripped MissingGreenlet
        # (observed once during v1.1.3 dogfood and required a manual
        # delete to clean up). flush() materialises server-defaults so
        # _serialize can see them without lazy-loading.
        await session.flush()
        await session.refresh(item)
        result = _serialize(item, KnowledgeItemResponse)
        await session.commit()
        return result


@mcp.tool()
async def update_knowledge_item(
    item_id: str,
    title: str | None = None,
    content_raw: str | None = None,
    knowledge_type: str | None = None,
    language: str | None = None,
    source_type: str | None = None,
    source_ref: str | None = None,
    confidence: float | None = None,
    tags: str | list[str] | None = None,
) -> str:
    """Update an existing knowledge item. Pass only the fields you want
    to change; omitted fields stay as-is. Returns the full updated row.

    Fills the gap that pre-v1.1.4 forced agents to fall back to raw REST
    PATCH (or wipe + re-add) just to fix a typo or bump a confidence
    score on an AI-inferred entry.

    item_id: UUID of the knowledge entry (from list_knowledge / add_*).
    Field semantics match ``add_knowledge_item``."""
    from app.application.knowledge_service import KnowledgeService
    from app.schemas.knowledge import KnowledgeItemUpdate, KnowledgeItemResponse

    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if content_raw is not None:
        payload["content_raw"] = content_raw
    if knowledge_type is not None:
        payload["knowledge_type"] = knowledge_type
    if language is not None:
        payload["language"] = language
    if source_type is not None:
        payload["source_type"] = source_type
    if source_ref is not None:
        payload["source_ref"] = source_ref
    if confidence is not None:
        payload["confidence"] = max(0.0, min(1.0, float(confidence)))
    if tags is not None:
        if isinstance(tags, list):
            tag_list = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        else:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        payload["tags_json"] = {"general": tag_list} if tag_list else None

    if not payload:
        # Empty patch — surface as no-op rather than silently 200ing.
        # Saves a confused agent round-trip wondering why nothing changed.
        return json.dumps(
            {"error": "no fields provided", "hint": "pass at least one field to update"},
            ensure_ascii=False, indent=2,
        )

    async with _session_factory() as session:
        svc = KnowledgeService(session)
        data = KnowledgeItemUpdate(**payload)
        item = await svc.update(uuid.UUID(item_id), data)
        await session.flush()
        await session.refresh(item)
        result = _serialize(item, KnowledgeItemResponse)
        await session.commit()
        return result


@mcp.tool()
async def delete_knowledge_item(item_id: str) -> str:
    """Delete a knowledge item by UUID. Returns ``{"deleted": true, "id": ...}``
    on success or raises NotFoundError when the row doesn't exist.

    Use cases: clean up an AI-inferred entry the owner rejected; remove
    a duplicate that slipped past the unique-title constraint via a
    rename; wipe demo / test rows."""
    from app.application.knowledge_service import KnowledgeService

    async with _session_factory() as session:
        svc = KnowledgeService(session)
        await svc.delete(uuid.UUID(item_id))
        await session.commit()
        return json.dumps(
            {"deleted": True, "id": item_id},
            ensure_ascii=False, indent=2,
        )


@mcp.tool()
async def list_knowledge(
    scope_type: str,
    scope_id: str,
    knowledge_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List brand/marketing knowledge items (selling points, pain points, FAQs,
    proofs, etc.) for a merchant or offer — the knowledge base that grounds AI
    content generation, RAG, and marketing workflows.

    scope_type: merchant | offer
    knowledge_type: optional comma-separated filter, one or more of:
      selling_point | pain_point | proof | faq | objection
      | audience | scenario | brand | general
      (e.g. knowledge_type='selling_point,proof' returns only those types).
      Leave empty to return all types.
    """
    from app.application.knowledge_service import KnowledgeService
    from app.schemas.knowledge import KnowledgeItemResponse

    types = [t.strip() for t in knowledge_type.split(",") if t.strip()] or None
    async with _session_factory() as session:
        svc = KnowledgeService(session)
        items, total = await svc.list(
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            knowledge_type=types,
            page=page,
            page_size=page_size,
        )
        serialized_items = [KnowledgeItemResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Asset Tools ─────────────────────────────────────────────────


@mcp.tool()
async def search_assets(
    scope_type: str,
    scope_id: str,
    q: str = "",
    tags: str = "",
    asset_type: str = "",
    content_form: str = "",
    campaign_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """Find brand assets — logos, product images, marketing videos, avatars,
    reference photos, case study screenshots, audio clips, marketing copy,
    and any other media the user has uploaded to this merchant or offer.
    Use this whenever an agent needs an image / video / audio / document /
    reference copy to ground AI content generation — do NOT hallucinate
    asset URLs. scope_type: merchant | offer.

    Filters:
      q:             fuzzy search across filename, title, and tag values
                     (e.g. q='logo' matches '品牌Logo').
      tags:          exact tag value match across any category, comma-separated
                     (e.g. tags='品牌标识,宣传物料').
      asset_type:    filter by type: 'image' | 'video' | 'audio' | 'document' | 'copy' | 'url'.
      content_form:  filter by content_form closed-vocab id, comma-separated
                     (e.g. content_form='unboxing,review'). Discover valid ids
                     via get_app_config('asset_tagging').
      campaign_type: filter by campaign_type closed-vocab id, comma-separated
                     (e.g. campaign_type='flash_sale,bundle_discount').

    Tip: use `q` for keyword search, `tags` for arbitrary tag values across
    categories, and `content_form` / `campaign_type` for structured filters on
    those specific tag categories. Each item includes a preview_url for
    viewing/downloading the file.
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        storage = LocalStorageAdapter()
        svc = AssetService(session, storage)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        cf_list = [t.strip() for t in content_form.split(",") if t.strip()] if content_form else None
        ct_list = [t.strip() for t in campaign_type.split(",") if t.strip()] if campaign_type else None
        items, total = await svc.search(
            q=q or None,
            tags=tag_list,
            asset_type=asset_type or None,
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id),
            content_form=cf_list,
            campaign_type=ct_list,
            page=page,
            page_size=page_size,
        )
        serialized_items = []
        for i in items:
            d = AssetResponse.model_validate(i, from_attributes=True).model_dump(mode="json")
            preview = storage.get_public_url(d["id"])
            if _app_url_looks_valid():
                d["preview_url"] = preview
            else:
                d["preview_url"] = None
                d["preview_url_note"] = "APP_URL not properly configured (Settings → MCP); agent-reachable URL unavailable."
            d.pop("preview_uri", None)  # remove ambiguous null field
            d.pop("storage_uri", None)  # internal path, not for agents
            serialized_items.append(d)
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Context & Topic Tools ──────────────────────────────────────


@mcp.tool()
async def get_offer_context_summary(offer_id: str) -> str:
    """Get the full aggregated marketing context for an offer — merchant info,
    brand knowledge (selling points, pain points, proofs, FAQs), audiences,
    usage scenarios, assets overview, strategy units. This is the single
    richest-grounding payload for AI content generation, script writing,
    topic/video idea generation, and RAG pipelines. Call this first when an
    agent needs to write any content about a specific product/service."""
    from app.application.context_service import ContextService

    async with _session_factory() as session:
        svc = ContextService(session)
        ctx = await svc.get_offer_context(uuid.UUID(offer_id))
        return _serialize(ctx, unwrap_offer=True)


# ── Strategy Unit Tools ───────────────────────────────────────


@mcp.tool()
async def create_strategy_unit(
    merchant_id: str,
    offer_id: str,
    name: str,
    audience_segment: str = "",
    scenario: str = "",
    marketing_objective: str = "",
    channel: str = "",
    language: str = "zh-CN",
) -> str:
    """Create a strategy unit under an offer.
    A strategy unit represents a specific audience × scenario × objective × channel combination.
    marketing_objective: awareness | conversion | lead_generation | education | trust_building | retention | launch | branding."""
    from app.application.strategy_unit_service import StrategyUnitService
    from app.schemas.strategy_unit import StrategyUnitCreate, StrategyUnitResponse

    async with _session_factory() as session:
        svc = StrategyUnitService(session)
        data = StrategyUnitCreate(
            merchant_id=uuid.UUID(merchant_id),
            offer_id=uuid.UUID(offer_id),
            name=name,
            audience_segment=audience_segment or None,
            scenario=scenario or None,
            marketing_objective=marketing_objective or None,
            channel=channel or None,
            language=language,
        )
        unit = await svc.create(data)
        await session.commit()
        return _serialize(unit, StrategyUnitResponse)


@mcp.tool()
async def list_strategy_units(
    offer_id: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List strategy units for an offer."""
    from app.application.strategy_unit_service import StrategyUnitService
    from app.schemas.strategy_unit import StrategyUnitResponse

    async with _session_factory() as session:
        svc = StrategyUnitService(session)
        items, total = await svc.list(offer_id=uuid.UUID(offer_id), page=page, page_size=page_size)
        serialized_items = [StrategyUnitResponse.model_validate(i, from_attributes=True).model_dump(mode="json") for i in items]
        return json.dumps({"total": total, "page": page, "items": serialized_items}, ensure_ascii=False, indent=2, default=str)


# ── Strategy Unit Link Tools ──────────────────────────────────


@mcp.tool()
async def link_knowledge_to_strategy_unit(
    strategy_unit_id: str,
    knowledge_item_id: str,
    role: str = "general",
    priority: int = 0,
    note: str = "",
) -> str:
    """Link a knowledge item to a strategy unit (many-to-many).
    role: core_message | proof | audience_insight | scenario_anchor | objection | compliance_note | general."""
    from app.application.strategy_unit_link_service import StrategyUnitKnowledgeLinkService
    from app.schemas.strategy_unit_link import KnowledgeLinkCreate, KnowledgeLinkResponse

    async with _session_factory() as session:
        svc = StrategyUnitKnowledgeLinkService(session)
        data = KnowledgeLinkCreate(
            knowledge_item_id=uuid.UUID(knowledge_item_id),
            role=role,
            priority=priority,
            note=note or None,
        )
        link = await svc.create(uuid.UUID(strategy_unit_id), data)
        await session.commit()
        return _serialize(link, KnowledgeLinkResponse)


@mcp.tool()
async def link_asset_to_strategy_unit(
    strategy_unit_id: str,
    asset_id: str,
    role: str = "general",
    priority: int = 0,
    note: str = "",
) -> str:
    """Link an asset to a strategy unit (many-to-many).
    role: hook_asset | proof_asset | trust_asset | explainer_asset | cta_asset | general."""
    from app.application.strategy_unit_link_service import StrategyUnitAssetLinkService
    from app.schemas.strategy_unit_link import AssetLinkCreate, AssetLinkResponse

    async with _session_factory() as session:
        svc = StrategyUnitAssetLinkService(session)
        data = AssetLinkCreate(
            asset_id=uuid.UUID(asset_id),
            role=role,
            priority=priority,
            note=note or None,
        )
        link = await svc.create(uuid.UUID(strategy_unit_id), data)
        await session.commit()
        return _serialize(link, AssetLinkResponse)


# ── App Tools ────────────────────────────────────────────────


@mcp.tool()
async def list_apps(language: str = "en") -> str:
    """Discover the AI apps available on this OpenLucid instance — script
    writer, content studio (social copy), topic/video idea studio, KB Q&A /
    RAG, etc. Each app bundles an LLM prompt pipeline that consumes brand
    knowledge and produces marketing outputs. Returns: app_id, name,
    description, category, task_type, required_entities, required_capabilities,
    entry_modes, status. Then call get_app_config(app_id) to inspect its
    parameters, and run_app(app_id, inputs) to execute it."""
    from app.apps.registry import AppRegistry

    apps = AppRegistry.list_apps()
    result = []
    for app in apps:
        a = app.localized(language[:2])
        result.append({
            "app_id": a.app_id,
            "name": a.name,
            "slug": a.slug,
            "description": a.description,
            "icon": a.icon,
            "category": a.category,
            "task_type": a.task_type,
            "required_entities": a.required_entities,
            "required_capabilities": a.required_capabilities,
            "entry_modes": a.entry_modes,
            "status": a.status,
        })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_app_config(app_id: str, language: str = "zh") -> str:
    """Fetch a single app's metadata + all enumerations needed to call run_app.

    Avoids the 4-round-trip discovery problem: instead of the agent guessing
    platform_id / persona_id / structure_id / goal_id, this returns them all in
    one call, localized.

    Supported app_ids:
      - script_writer:  returns enums.platforms / personas / structures / goals
      - content_studio: returns the same enums as script_writer (shared Composer)
      - kb_qa:          returns enums.styles
      - topic_studio:   returns enums.{} (no fixed enums at this time)

    Returns:
      {
        "app_id": "...",
        "name": "...",           (localized)
        "description": "...",    (localized)
        "actions": [...],        (action names accepted by run_app for this app)
        "enums": { <name>: [...] }
      }

    Raises on unknown app_id.
    """
    from app.apps.registry import AppRegistry
    from app.exceptions import AppError

    lang = language[:2] if language else "zh"

    app = AppRegistry.get_app(app_id)
    if app is None:
        raise AppError("UNKNOWN_APP", f"Unknown app_id '{app_id}'", 404)
    a = app.localized(lang)

    def _script_writer_enums() -> dict:
        from app.application.script_goals import list_goals
        from app.application.script_personas import list_personas
        from app.application.script_platforms import list_platforms
        from app.application.script_structures import list_structures

        # Platforms: sort by region relevance (zh-first for zh, en-first for en), matches REST.
        region_priority = {"zh": 0, "global": 1, "en": 2} if lang == "zh" else {"en": 0, "global": 1, "zh": 2}
        platforms_sorted = sorted(
            list_platforms(),
            key=lambda p: (region_priority.get(p.region, 99), p.id),
        )
        return {
            "platforms": [
                {
                    "id": p.id,
                    "name": p.localized_name(lang),
                    "emoji": p.emoji,
                    "region": p.region,
                    "content_type": p.content_type,
                    "aspect_ratio": p.aspect_ratio,
                    "max_script_chars": p.max_script_chars,
                }
                for p in platforms_sorted
            ],
            "personas": [
                {
                    "id": p.id,
                    "name": p.localized_name(lang),
                    "emoji": p.emoji,
                    "description": p.localized_description(lang),
                    "tags": p.tags,
                }
                for p in list_personas()
            ],
            "structures": [
                {
                    "id": s.id,
                    "name": s.localized_name(lang),
                    "emoji": s.emoji,
                    "description": s.localized_description(lang),
                    "section_ids": s.section_ids,
                }
                for s in list_structures()
            ],
            "goals": [
                {"id": g.id, "name": g.localized_name(lang), "emoji": g.emoji}
                for g in list_goals()
            ],
        }

    if app_id in ("script_writer", "content_studio"):
        enums = _script_writer_enums()
        actions = ["suggest_topic", "generate"] if app_id == "script_writer" else ["generate"]
    elif app_id == "kb_qa":
        from app.apps.kb_qa_styles import STYLE_TEMPLATES
        enums = {
            "styles": [
                {
                    "style_id": s.style_id,
                    "name": s.localized(lang).name,
                    "description": s.localized(lang).description,
                    "icon": s.icon,
                }
                for s in STYLE_TEMPLATES.values()
            ],
        }
        actions = ["ask"]
    elif app_id == "asset_tagging":
        from app.application.campaign_types import list_campaign_types
        from app.application.content_forms import list_content_forms
        enums = {
            "content_forms": [
                {
                    "id": cf.id,
                    "name": cf.localized_name(lang),
                    "emoji": cf.emoji,
                    "description": cf.localized_description(lang),
                }
                for cf in list_content_forms()
            ],
            "campaign_types": [
                {
                    "id": ct.id,
                    "name": ct.localized_name(lang),
                    "emoji": ct.emoji,
                    "description": ct.localized_description(lang),
                }
                for ct in list_campaign_types()
            ],
        }
        actions = ["extract_tags"]
    elif app_id == "topic_studio":
        enums = {}
        actions = ["generate"]
    else:
        # App is registered but we haven't mapped its enums yet — return empty dict
        enums = {}
        actions = []

    return json.dumps(
        {
            "app_id": a.app_id,
            "name": a.name,
            "description": a.description,
            "actions": actions,
            "enums": enums,
        },
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
async def run_app(
    app_id: str,
    action: str,
    offer_id: str,
    strategy_unit_id: str | None = None,
    language: str = "zh-CN",
    config_id: str | None = None,
    question: str = "",
    style_id: str = "professional",
    topic: str = "",
    goal: str = "reach_growth",
    tone: str = "",
    word_count: int = 150,
    cta: str = "",
    industry: str = "",
    reference: str = "",
    extra_req: str = "",
    platform_id: str | None = None,
    persona_id: str | None = None,
    structure_id: str | None = None,
    goal_id: str | None = None,
    topic_plan_id: str | None = None,
) -> str:
    """Run an AI app grounded in an offer's knowledge base — the main workhorse
    for generating marketing content, answering brand questions, writing video
    scripts, producing social copy, and generating topic/video ideas. This tool
    consolidates several AI capabilities behind one call so agents don't need
    to know about separate "write_script" / "answer_question" /
    "generate_topics" tools. Available apps and actions:

    kb_qa:
      - ask: Answer a question based on offer knowledge base.
        Required: question. Optional: style_id (professional|friendly|expert).
        Returns: answer, referenced_knowledge, has_relevant_knowledge.

    script_writer:
      - suggest_topic: Suggest a creative video script topic.
        Optional: goal, strategy_unit_id.
        Returns: topic text.
      - generate: Generate a spoken-word video script. **Prefer passing the
        Composer dimensions** (platform_id / persona_id / structure_id /
        goal_id — discover valid ids via get_app_config("script_writer")) so
        you get structured output (hook/body/cta sections + B-roll plan),
        not just plain text. If any are set, the result's `structured_content`
        will be non-null.
        Optional: topic OR topic_plan_id (if both empty, generated from KB);
        topic_plan_id (the title/hook/angle/key_points of an existing plan are
        injected into the prompt when `topic` is empty); goal_id (preferred
        over legacy `goal` free-text); platform_id, persona_id, structure_id;
        tone, word_count, cta, industry, reference, extra_req, strategy_unit_id.
        Returns: {script, knowledge_count, structured_content, creation_id}.
        The resulting creation has source_app="mcp:external".

    content_studio:
      - generate: Generate text-first social content (posts, articles, threads).
        Uses the same Composer dimensions as script_writer — agent should pick a
        non-video platform_id via get_app_config("content_studio") (e.g.
        xiaohongshu, weibo, wechat_moments). Accepts the same optional params
        as script_writer generate, including config_id for per-call LLM override.
        Returns: {script, knowledge_count, structured_content, creation_id};
        the resulting creation has source_app="mcp:external".

    topic_studio:
      - generate: Generate structured topic plans.
        Optional: strategy_unit_id, count (via word_count param, default 5),
        config_id (per-call LLM override; None = use scene default).
        Returns: list of topic plans with title, angle, hook, key_points.
        Use list_topic_plans/get_topic_plan afterwards to fetch them again.

    goal (legacy free-text) must be one of:
      reach_growth, lead_generation, conversion, education, traffic_redirect, other,
      seeding, knowledge_sharing, brand_awareness.
    """
    from app.exceptions import AppError

    oid = uuid.UUID(offer_id)
    suid = uuid.UUID(strategy_unit_id) if strategy_unit_id else None

    if app_id == "kb_qa":
        if action != "ask":
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for kb_qa. Available: ask", 400)
        from app.application.kb_qa_service import KBQAService
        from app.schemas.app import KBQAAskRequest

        async with _session_factory() as session:
            svc = KBQAService(session)
            req = KBQAAskRequest(
                offer_id=oid,
                question=question,
                style_id=style_id,
                language=language,
                config_id=config_id,
            )
            result = await svc.ask(req)
            return _serialize(result)

    elif app_id == "script_writer":
        if action == "suggest_topic":
            from app.application.script_writer_service import ScriptWriterService

            async with _session_factory() as session:
                svc = ScriptWriterService(session)
                topic_text = await svc.suggest_topic(
                    offer_id=offer_id,
                    strategy_unit_id=strategy_unit_id,
                    goal=goal,
                    language=language,
                    config_id=config_id,
                )
                return json.dumps({"topic": topic_text}, ensure_ascii=False)

        elif action == "generate":
            from app.application.script_writer_service import (
                DEFAULT_SYSTEM_PROMPT_EN,
                DEFAULT_SYSTEM_PROMPT_ZH,
                ScriptWriterService,
            )
            from app.schemas.app import ScriptWriterRequest

            sys_prompt = DEFAULT_SYSTEM_PROMPT_EN if language.startswith("en") else DEFAULT_SYSTEM_PROMPT_ZH
            async with _session_factory() as session:
                svc = ScriptWriterService(session)
                req = ScriptWriterRequest(
                    offer_id=oid,
                    strategy_unit_id=suid,
                    system_prompt=sys_prompt,
                    topic=topic,
                    goal=goal,
                    tone=tone or None,
                    word_count=word_count,
                    cta=cta or None,
                    industry=industry or None,
                    reference=reference or None,
                    extra_req=extra_req or None,
                    language=language,
                    config_id=config_id,
                    # Composer dimensions (optional) — any set → structured output
                    platform_id=platform_id,
                    persona_id=persona_id,
                    structure_id=structure_id,
                    goal_id=goal_id,
                    # Topic plan linkage
                    topic_plan_id=uuid.UUID(topic_plan_id) if topic_plan_id else None,
                    # MCP-originated creations are tagged distinctly so Settings
                    # and analytics can separate agent output from WebUI output.
                    source_app="mcp:external",
                )
                result = await svc.generate(req)
                return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for script_writer. Available: suggest_topic, generate", 400)

    elif app_id == "topic_studio":
        if action != "generate":
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for topic_studio. Available: generate", 400)
        from app.application.topic_plan_service import TopicPlanService
        from app.schemas.topic_plan import TopicPlanGenerateRequest, TopicPlanResponse

        async with _session_factory() as session:
            svc = TopicPlanService(session)
            req = TopicPlanGenerateRequest(
                offer_id=oid,
                strategy_unit_id=suid,
                count=word_count if word_count <= 20 else 5,
                language=language,
                config_id=config_id,
            )
            plans, thinking = await svc.generate(req)
            await session.commit()
            serialized = [TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json") for p in plans]
            result = {"plans": serialized, "thinking": thinking}
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    elif app_id == "content_studio":
        # Content Studio reuses ScriptWriterService but is scoped to text / social
        # platforms (the web UI filters `content_type !== 'video'`). MCP agents are
        # expected to pick a text-format platform_id for this app; the service
        # itself is platform-agnostic.
        if action != "generate":
            raise AppError("UNKNOWN_ACTION", f"Unknown action '{action}' for content_studio. Available: generate", 400)
        from app.application.script_writer_service import (
            DEFAULT_SYSTEM_PROMPT_EN,
            DEFAULT_SYSTEM_PROMPT_ZH,
            ScriptWriterService,
        )
        from app.schemas.app import ScriptWriterRequest

        sys_prompt = DEFAULT_SYSTEM_PROMPT_EN if language.startswith("en") else DEFAULT_SYSTEM_PROMPT_ZH
        async with _session_factory() as session:
            svc = ScriptWriterService(session)
            req = ScriptWriterRequest(
                offer_id=oid,
                strategy_unit_id=suid,
                system_prompt=sys_prompt,
                topic=topic,
                goal=goal,
                tone=tone or None,
                word_count=word_count,
                cta=cta or None,
                industry=industry or None,
                reference=reference or None,
                extra_req=extra_req or None,
                language=language,
                config_id=config_id,
                platform_id=platform_id,
                persona_id=persona_id,
                structure_id=structure_id,
                goal_id=goal_id,
                topic_plan_id=uuid.UUID(topic_plan_id) if topic_plan_id else None,
                source_app="mcp:external",
            )
            result = await svc.generate(req)
            return json.dumps(result, ensure_ascii=False, indent=2)

    else:
        available = ["kb_qa", "script_writer", "content_studio", "topic_studio"]
        raise AppError("UNKNOWN_APP", f"Unknown app_id '{app_id}'. Available: {available}", 400)


# ── Composite Tools ────────────────────────────────────────────


@mcp.tool()
async def get_merchant_overview(merchant_id: str) -> str:
    """Single-call overview of a marketing workspace / brand — merchant basics,
    all its offers (products/services), brand kit summary, and knowledge /
    asset counts. The best first call when an agent needs to orient itself on
    a new client / brand before generating content, answering questions, or
    running any AI app. Use this before list_offers / get_offer_context_summary
    to avoid multiple round-trips."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.application.brandkit_service import BrandKitService
    from app.application.knowledge_service import KnowledgeService
    from app.application.merchant_service import MerchantService
    from app.application.offer_service import OfferService
    from app.schemas.brandkit import BrandKitResponse
    from app.schemas.merchant import MerchantResponse
    from app.schemas.offer import OfferResponse

    mid = uuid.UUID(merchant_id)
    async with _session_factory() as session:
        merchant_svc = MerchantService(session)
        merchant = await merchant_svc.get(mid)
        merchant_data = MerchantResponse.model_validate(merchant, from_attributes=True).model_dump(mode="json")

        offer_svc = OfferService(session)
        offers, offer_total = await offer_svc.list(merchant_id=mid, page=1, page_size=100)
        offers_data = [OfferResponse.model_validate(o, from_attributes=True).model_dump(mode="json") for o in offers]
        # Flatten _json wrappers to keep MCP read shape consistent with create_offer input.
        offers_data = _unwrap_offer_json_fields(offers_data)

        bk_svc = BrandKitService(session)
        brandkits, bk_total = await bk_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=100)
        brandkits_data = [BrandKitResponse.model_validate(b, from_attributes=True).model_dump(mode="json") for b in brandkits]

        knowledge_svc = KnowledgeService(session)
        _, knowledge_total = await knowledge_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=1)

        storage = LocalStorageAdapter()
        asset_svc = AssetService(session, storage)
        _, asset_total = await asset_svc.list(scope_type="merchant", scope_id=mid, page=1, page_size=1)

        # Also count knowledge/assets per offer
        # overview only returns lightweight offer summary — full description is in get_offer_context_summary
        for od in offers_data:
            oid = uuid.UUID(od["id"])
            _, ok_total = await knowledge_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            _, oa_total = await asset_svc.list(scope_type="offer", scope_id=oid, page=1, page_size=1)
            od["knowledge_count"] = ok_total
            od["asset_count"] = oa_total
            od.pop("description", None)

        overview = {
            "merchant": merchant_data,
            "offers": {"total": offer_total, "items": offers_data},
            "brand_kits": {"total": bk_total, "items": brandkits_data},
            "merchant_knowledge_count": knowledge_total,
            "merchant_asset_count": asset_total,
        }
        return json.dumps(overview, ensure_ascii=False, indent=2, default=str)


# ── Creations (capture finished content back to OpenLucid) ─────


@mcp.tool()
async def save_creation(
    title: str,
    content: str,
    content_type: str = "general",
    offer_id: str | None = None,
    merchant_id: str | None = None,
    tags: str | list[str] = "",
    source_note: str | None = None,
) -> str:
    """Save a finished content piece (post, script, copy, email, etc.) back to OpenLucid
    so the user can find, reuse, and reference it later. This is how content created
    in the chat returns to the user's permanent OpenLucid library.

    WHEN TO CALL (be proactive — save-all, not save-if-liked):
      - You produced any complete, deliverable piece of content (a post, a script, an
        email, a hook, a caption). Save it even if the user hasn't confirmed — chat
        scrolls away, the library is what stays.
      - You generated multiple variants on the same topic — save each variant,
        differentiating via `tags` (e.g. "v1"/"v2", or platform name "tiktok"/"xhs").
      - You iterated and produced a revised final — save the revised version (the
        earlier one is fine to keep too, it's a version trail).

    WHEN NOT TO CALL:
      - Half-finished drafts (still missing a hook, a CTA, or structure).
      - Analysis / reasoning notes / tool call logs / search result dumps.
      - You only quoted or summarized existing KB content (nothing new produced).
      - Pure exploration ("brainstorm 5 raw ideas") where nothing is a deliverable yet.
      - You'd save more than ~5 items in one turn (batch — likely over-capture).

    Args:
      title: Short, descriptive title (max 512 chars).
      content: The full content text. Plain text or markdown.
      content_type: Free-form category. Common values: post / script / email /
                    caption / hook / general. Default "general".
      offer_id: Optional offer UUID this content is for. If provided, the merchant
                is auto-derived from the offer.
      merchant_id: Optional merchant UUID. Only needed if offer_id is omitted AND
                   the deployment has multiple merchants.
      tags: Optional comma-separated tags (e.g. "tiktok,launch,hook").
      source_note: Optional one-line note about how this was created (e.g.
                   "Generated from KB about feature X, second draft after user
                   asked for shorter version").

    The source_app field is auto-populated as "mcp:external" so the user can
    distinguish externally captured content from internally generated content.

    Returns the saved creation as JSON with its assigned id.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationCreate, CreationResponse

    if isinstance(tags, list):
        tag_list = [t.strip() for t in tags if isinstance(t, str) and t.strip()] or None
    else:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    async with _session_factory() as session:
        svc = CreationService(session)
        data = CreationCreate(
            title=title,
            content=content,
            content_type=content_type or "general",
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            merchant_id=uuid.UUID(merchant_id) if merchant_id else None,
            tags=tag_list,
            source_app="mcp:external",
            source_note=source_note,
        )
        creation = await svc.create(data)
        await session.commit()
        return _serialize(creation, CreationResponse)


@mcp.tool()
async def list_creations(
    merchant_id: str | None = None,
    offer_id: str | None = None,
    content_type: str | None = None,
    source_app: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List finished creations saved in OpenLucid — posts, scripts, emails, etc.

    Use this to see what's already been saved for an offer before generating
    similar content, or to let the user find a prior piece to iterate on.

    Filters:
      offer_id: Only creations for this offer (UUID string).
      merchant_id: Only creations for this merchant (UUID string).
      content_type: Free-form category (post / script / email / ...).
      source_app: Who produced it. Values: "mcp:external" (from an agent),
                  "script_writer" / "content_studio" / "topic_studio" / "manual".
      q: Substring search over title + content.

    Returns: {"total", "page", "items": [...]}. Each item is a CreationResponse
    with `content` stripped (call get_creation(id) for full text); title,
    source_app, content_type, tags, created_at, structured_content (for Script
    Writer output), video_count, and latest_video are included so the agent can
    pick which creation to drill into.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationResponse

    async with _session_factory() as session:
        svc = CreationService(session)
        items, total = await svc.list(
            merchant_id=uuid.UUID(merchant_id) if merchant_id else None,
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            content_type=content_type,
            source_app=source_app,
            q=q,
            page=page,
            page_size=page_size,
        )
        # Enrich with video summary so agents can spot which creations already have videos.
        summaries = await svc.get_video_summaries([c.id for c in items])
        for c in items:
            s = summaries.get(c.id)
            c.video_count = s["count"] if s else 0
            c.latest_video = s["latest"] if s else None
        serialized = [
            CreationResponse.model_validate(c, from_attributes=True).model_dump(mode="json")
            for c in items
        ]
        # List view: drop full content to keep responses small — use get_creation for full text.
        for item in serialized:
            item.pop("content", None)
        return json.dumps(
            {"total": total, "page": page, "items": serialized},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def get_creation(creation_id: str) -> str:
    """Fetch a single creation by id, with full `content` and `structured_content`.

    Use this after list_creations when you want to read, quote, or iterate on
    a specific prior creation. Returns the full CreationResponse including
    `content` (plain text) and `structured_content` (Script Writer sections,
    if applicable), plus video summary.
    """
    from app.application.creation_service import CreationService
    from app.schemas.creation import CreationResponse

    async with _session_factory() as session:
        svc = CreationService(session)
        creation = await svc.get(uuid.UUID(creation_id))
        summaries = await svc.get_video_summaries([creation.id])
        s = summaries.get(creation.id)
        creation.video_count = s["count"] if s else 0
        creation.latest_video = s["latest"] if s else None
        return _serialize(creation, CreationResponse)


# ── Topic Plans (generated by run_app(topic_studio), now readable back) ──


@mcp.tool()
async def list_topic_plans(
    offer_id: str | None = None,
    strategy_unit_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List previously generated topic plans — the output of run_app(topic_studio).

    Use this to recover recent topic ideas, pick one to turn into a script, or
    audit how many plans exist for an offer. Filter by offer_id and/or
    strategy_unit_id.

    Each item is a TopicPlanResponse with: id, title, angle, hook,
    key_points_json, target_audience_json, target_scenario_json, channel,
    score_relevance, score_conversion, score_asset_readiness, status, created_at.

    Feed a plan's id into run_app(script_writer, topic_plan_id=<id>) to
    auto-prefill the script prompt with the plan's title/hook/angle/key_points.
    """
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanResponse

    async with _session_factory() as session:
        svc = TopicPlanService(session)
        items, total = await svc.list(
            offer_id=uuid.UUID(offer_id) if offer_id else None,
            strategy_unit_id=uuid.UUID(strategy_unit_id) if strategy_unit_id else None,
            page=page, page_size=page_size,
        )
        serialized = [
            TopicPlanResponse.model_validate(p, from_attributes=True).model_dump(mode="json")
            for p in items
        ]
        return json.dumps(
            {"total": total, "page": page, "items": serialized},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def get_topic_plan(topic_id: str) -> str:
    """Fetch a single topic plan by id — full title, angle, hook, key_points,
    scoring, status.
    """
    from app.application.topic_plan_service import TopicPlanService
    from app.schemas.topic_plan import TopicPlanResponse

    async with _session_factory() as session:
        svc = TopicPlanService(session)
        plan = await svc.get(uuid.UUID(topic_id))
        return _serialize(plan, TopicPlanResponse)


# ── Videos (full generation workflow for agents) ───────────────


@mcp.tool()
async def list_media_providers() -> str:
    """List configured video/image/TTS providers (Chanjing, Jogg, ...).

    Agent uses this to find `provider_config_id` for subsequent calls.
    Returns [{id, provider, label, is_active}, ...] — credentials are not exposed.
    """
    from app.application.media_provider_service import list_media_provider_configs

    async with _session_factory() as session:
        configs = await list_media_provider_configs(session)
        items = [
            {
                "id": c.id,
                "provider": c.provider,
                "label": c.label,
                "is_active": c.is_active,
            }
            for c in configs
        ]
        return json.dumps({"total": len(items), "items": items}, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_avatars(provider_config_id: str, page: int = 1, page_size: int = 50) -> str:
    """List avatars available under a provider config — for picking `avatar_id`
    before calling submit_video. Hits the upstream provider live.

    Returns {total, page, items: [{id, name, gender, age, preview_image_url,
    preview_video_url, extras}]}.
    """
    from app.application.media_provider_service import list_avatars_for_config

    async with _session_factory() as session:
        avatars = await list_avatars_for_config(
            session, uuid.UUID(provider_config_id), page=page, page_size=page_size,
        )
        items = [a.model_dump(mode="json") for a in avatars]
        return json.dumps(
            {"total": len(items), "page": page, "items": items},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def list_voices(provider_config_id: str, page: int = 1, page_size: int = 50) -> str:
    """List voices available under a provider config — for picking `voice_id`
    before calling submit_video.

    Returns {total, page, items: [{id, name, gender, age, language, sample_url}]}.
    """
    from app.application.media_provider_service import list_voices_for_config

    async with _session_factory() as session:
        voices = await list_voices_for_config(
            session, uuid.UUID(provider_config_id), page=page, page_size=page_size,
        )
        items = [v.model_dump(mode="json") for v in voices]
        return json.dumps(
            {"total": len(items), "page": page, "items": items},
            ensure_ascii=False, indent=2, default=str,
        )


@mcp.tool()
async def submit_video(
    creation_id: str,
    provider_config_id: str,
    avatar_id: str,
    voice_id: str,
    script: str,
    aspect_ratio: str = "portrait",
    caption: bool = True,
    name: str | None = None,
    provider_extras: dict | None = None,
    broll: bool = False,
    subtitle_style: str = "classic",
    subtitle_color: str | None = None,
    subtitle_stroke: str | None = None,
) -> str:
    """Kick off a talking-avatar video for an existing creation. Returns the job
    id; poll with get_video. `aspect_ratio` ∈ portrait|landscape|square.

    Discovery path: call list_media_providers → list_avatars → list_voices first.

    Defaults are tuned so that an agent passing only the required params
    (creation_id, provider_config_id, avatar_id, voice_id, script) gets a
    working talking-head video with captions on, no B-roll, classic subtitle
    style. Opt into richer output by overriding the optional params below.

    IMPORTANT — avatar extras pass-through:
      When you pick an avatar from `list_avatars`, copy its entire `extras`
      object into `provider_extras` here. For Chanjing this carries the
      `figure_type` that the upstream API requires; omitting it will make
      create_video fail with code=50000 "figure_type not selected correctly"
      for any public avatar whose type is not the default `whole_body`
      (e.g. sit_body / circle_view avatars). Example:
          provider_extras = {"figure_type": "sit_body"}   # copied verbatim
                                                          # from list_avatars
                                                          # item.extras

      For Jogg, pass the same `extras` dict; unused keys are ignored.

    Optional enhancements:
      broll: True = auto-generate 1–2 AI-image/video cutaways per section that
        carries a `visual_direction` in the creation's structured_content
        (produced by script_writer). Adds roughly 3–5 minutes to generation.
        Use this for "storyboard"-style videos; leave False for pure
        talking-head output.
      subtitle_style: classic (white text, default) | bold (yellow, larger) |
        minimal (light grey, thin). Only takes effect when caption=True.
      subtitle_color / subtitle_stroke: optional hex overrides ("#RRGGBB");
        leave None to inherit the style preset.
    """
    from app.application.video_service import create_video_job
    from app.schemas.video import VideoGenerateRequest

    async with _session_factory() as session:
        data = VideoGenerateRequest(
            provider_config_id=provider_config_id,
            avatar_id=avatar_id,
            voice_id=voice_id,
            script=script,
            aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
            caption=caption,
            subtitle_style=subtitle_style,
            subtitle_color=subtitle_color,
            subtitle_stroke=subtitle_stroke,
            broll=broll,
            name=name,
            provider_extras=provider_extras or {},
        )
        job = await create_video_job(session, uuid.UUID(creation_id), data)
        return _serialize(job)


@mcp.tool()
async def get_video(video_id: str) -> str:
    """Get a video job's current state. Triggers a lazy refresh from the
    provider if status is non-terminal, so the returned `status` and
    `video_url` are fresh.
    """
    from app.application.video_service import get_video_job

    async with _session_factory() as session:
        job = await get_video_job(session, uuid.UUID(video_id))
        return _serialize(job)


@mcp.tool()
async def list_videos_for_creation(creation_id: str) -> str:
    """List all video jobs for a creation, oldest-first. Refreshes any
    non-terminal jobs inline.
    """
    from app.application.video_service import list_video_jobs_for_creation

    async with _session_factory() as session:
        jobs = await list_video_jobs_for_creation(session, uuid.UUID(creation_id))
        return json.dumps(
            {"total": len(jobs), "items": [j.model_dump(mode="json") for j in jobs]},
            ensure_ascii=False, indent=2, default=str,
        )


# ── Asset Writes (agent-ingestible) ────────────────────────────


@mcp.tool()
async def create_copy_asset(
    scope_type: str,
    scope_id: str,
    title: str,
    content_text: str,
    language: str = "zh-CN",
    tags: str | list[str] = "",
) -> str:
    """Save a text asset (a "copy") under a scope — e.g. an agent captured a
    customer testimonial, a reference passage, a marketing doc excerpt, and
    wants it persistent in the KB.

    scope_type: "merchant" | "offer". scope_id: UUID of the scope owner.
    tags: optional tags. Accepts a comma-separated string or a list[str];
      stored under a generic category in the asset's tags_json field.
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetCopyCreate, AssetResponse
    from app.domain.enums import ScopeType

    tag_list: dict[str, list[str]] = {}
    if isinstance(tags, list):
        clean = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        if clean:
            tag_list = {"general": clean}
    elif tags:
        tag_list = {"general": [t.strip() for t in tags.split(",") if t.strip()]}

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.create_copy(AssetCopyCreate(
            scope_type=ScopeType(scope_type),
            scope_id=uuid.UUID(scope_id),
            title=title,
            content_text=content_text,
            tags=tag_list,
            language=language,
        ))
        await session.commit()
        await session.refresh(asset)
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def upload_asset_from_url(
    url: str,
    scope_type: str,
    scope_id: str,
    title: str | None = None,
    language: str = "zh-CN",
) -> str:
    """Ingest a remote file (image / video / audio / PDF / doc) by URL and save
    it as an asset. The server fetches the bytes, detects type from the
    response `Content-Type` (fallback to URL extension), persists the file, and
    kicks off the normal async parse pipeline.

    Limits: 30-second fetch timeout, 50 MB max. Raises ASSET_URL_TOO_LARGE
    or ASSET_URL_FETCH_FAILED on violations.
    """
    import httpx
    from pathlib import Path as _Path
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.exceptions import AppError
    from app.schemas.asset import AssetResponse, AssetUploadMeta
    from app.domain.enums import AssetType, ScopeType

    MAX_BYTES = 50 * 1024 * 1024  # 50 MB

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise AppError("ASSET_URL_FETCH_FAILED", f"Failed to fetch {url}: {e}", 502) from e

    content_length = int(resp.headers.get("content-length") or len(resp.content))
    if content_length > MAX_BYTES:
        raise AppError(
            "ASSET_URL_TOO_LARGE",
            f"File at {url} is {content_length} bytes (max {MAX_BYTES})",
            413,
        )
    content = resp.content
    if len(content) > MAX_BYTES:
        raise AppError("ASSET_URL_TOO_LARGE", f"Body exceeds {MAX_BYTES} bytes", 413)

    # Detect content type: response header first, then URL extension
    mime_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _Path(url.split("?")[0]).suffix.lower().lstrip(".")

    def _resolve_asset_type(mime: str, extension: str) -> AssetType:
        if mime.startswith("image/"):
            return AssetType.IMAGE
        if mime.startswith("video/"):
            return AssetType.VIDEO
        if mime.startswith("audio/"):
            return AssetType.AUDIO
        if mime in {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
            return AssetType.DOCUMENT
        # Extension fallback
        if extension in {"jpg", "jpeg", "png", "gif", "webp", "svg"}:
            return AssetType.IMAGE
        if extension in {"mp4", "mov", "webm", "avi", "mkv"}:
            return AssetType.VIDEO
        if extension in {"mp3", "wav", "m4a", "ogg"}:
            return AssetType.AUDIO
        if extension in {"pdf", "docx", "doc", "xlsx", "xls", "csv", "txt", "md"}:
            return AssetType.DOCUMENT
        return AssetType.URL

    asset_type = _resolve_asset_type(mime_type, ext)

    # Derive a sane file name from the URL
    file_name = _Path(url.split("?")[0]).name or f"asset.{ext or 'bin'}"

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.upload(
            file_content=content,
            file_name=file_name,
            mime_type=mime_type or None,
            meta=AssetUploadMeta(
                scope_type=ScopeType(scope_type),
                scope_id=uuid.UUID(scope_id),
                asset_type=asset_type,
                language=language,
            ),
        )
        if title:
            await svc.repo.update(asset, title=title)
        await session.commit()
        await session.refresh(asset)
        asset_id = asset.id
        response = _serialize(asset, AssetResponse)

    # Kick background parse (metadata extraction + vision LLM tagging + slice
    # generation), matching what REST `/assets/upload` does. Fire-and-forget,
    # but hold a reference so the loop doesn't GC the weakly-referenced task.
    import asyncio as _asyncio
    from app.api.assets import _parse_in_background
    task = _asyncio.create_task(_parse_in_background(asset_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return response


@mcp.tool()
async def get_asset(asset_id: str) -> str:
    """Fetch an asset's full metadata by id — including title, tags, parse
    status, scores, storage_uri.
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.get(uuid.UUID(asset_id))
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def update_asset(
    asset_id: str,
    title: str | None = None,
    tags: str = "",
) -> str:
    """Rename or retag an asset. `tags` is a comma-separated list stored under
    the "general" category (matches REST's shape).
    """
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService
    from app.schemas.asset import AssetResponse

    tags_json: dict[str, list[str]] | None = None
    if tags:
        tags_json = {"general": [t.strip() for t in tags.split(",") if t.strip()]}

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        asset = await svc.update_asset(
            uuid.UUID(asset_id), title=title, tags_json=tags_json,
        )
        return _serialize(asset, AssetResponse)


@mcp.tool()
async def delete_asset(asset_id: str) -> str:
    """Permanently delete an asset and its stored files. Returns {deleted: true}."""
    from app.adapters.storage import LocalStorageAdapter
    from app.application.asset_service import AssetService

    async with _session_factory() as session:
        svc = AssetService(session, LocalStorageAdapter())
        await svc.delete_asset(uuid.UUID(asset_id))
        return json.dumps({"deleted": True, "asset_id": asset_id}, ensure_ascii=False)


# ── Resources ─────────────────────────────────────────────────


@mcp.resource("merchant://{merchant_id}/profile")
async def merchant_profile_resource(merchant_id: str) -> str:
    """Merchant profile including basic info, brand positioning, and compliance notes."""
    from app.application.merchant_service import MerchantService
    from app.schemas.merchant import MerchantResponse

    async with _session_factory() as session:
        svc = MerchantService(session)
        merchant = await svc.get(uuid.UUID(merchant_id))
        return _serialize(merchant, MerchantResponse)


@mcp.resource("offer://{offer_id}/context")
async def offer_context_resource(offer_id: str) -> str:
    """Full offer context: merchant info, knowledge, assets, selling points, audiences.
    Attach this resource to give an agent comprehensive product context."""
    from app.application.context_service import ContextService

    async with _session_factory() as session:
        svc = ContextService(session)
        ctx = await svc.get_offer_context(uuid.UUID(offer_id))
        return _serialize(ctx, unwrap_offer=True)


# ── Prompts ───────────────────────────────────────────────────


@mcp.prompt()
async def onboard_merchant(merchant_id: str) -> str:
    """Help me quickly understand a merchant's products and marketing strategy.
    Guides the agent through: merchant overview → offers → knowledge → brand kit."""
    return (
        f"I want to understand the merchant with ID {merchant_id}. "
        "Please follow these steps:\n"
        "1. Call get_merchant_overview to get the full picture of this enterprise.\n"
        "2. For each offer, summarize: what it is, who it targets, key selling points.\n"
        "3. Highlight any brand kit guidelines (tone, visual dos/don'ts).\n"
        "4. Identify gaps: offers without knowledge items, missing brand kits, etc.\n"
        "5. Give me a concise briefing I can act on."
    )


@mcp.prompt()
async def content_brief(offer_id: str, channel: str = "general", language: str = "zh-CN") -> str:
    """Generate a content planning brief for a specific offer.
    Combines offer context with topic generation to produce an actionable brief."""
    return (
        f"I need a content brief for offer ID {offer_id} targeting the '{channel}' channel "
        f"in {language}. Please:\n"
        "1. First call get_offer_context_summary to understand the product.\n"
        "2. Summarize the offer's positioning, audiences, and key selling points.\n"
        "3. Call run_app(app_id='topic_studio', action='generate', offer_id=...) to get 5 topic ideas.\n"
        "4. For each topic, explain why it fits the target audience and channel.\n"
        "5. Recommend which topic to prioritize and outline next steps."
    )


@mcp.prompt()
async def blog_from_offer(offer_id: str, platform: str = "wechat", language: str = "zh-CN") -> str:
    """Write a blog / long-form social post grounded in an offer's knowledge base.
    Produces a complete piece and saves it back via save_creation."""
    return (
        f"Write a blog post / long-form social content piece about offer ID {offer_id} "
        f"for the '{platform}' platform in {language}. Follow this recipe:\n"
        f"1. Call get_offer_context_summary(offer_id='{offer_id}') to load the full grounding —\n"
        "   merchant, knowledge, brand kit, audiences, scenarios.\n"
        f"2. Call list_knowledge(scope_type='offer', scope_id='{offer_id}', "
        "knowledge_type='selling_point,pain_point,proof') to surface the most citation-worthy entries.\n"
        "3. Call run_app(app_id='content_studio', action='generate', offer_id=..., platform_id="
        f"'{platform}') with goal='reach_growth' and tone from the brand kit.\n"
        "4. Review the output for brand-voice alignment and factual grounding against the KB.\n"
        "5. Save the final piece via save_creation with content_type='blog_post' and tags=['"
        f"{platform}', 'blog']."
    )


@mcp.prompt()
async def script_for_campaign(
    offer_id: str,
    goal: str = "conversion",
    platform: str = "douyin",
    language: str = "zh-CN",
) -> str:
    """Write a short-video script for a marketing campaign grounded in an offer.
    Optimized for conversion / lead_generation / reach_growth / education goals."""
    return (
        f"Write a short-video script for offer ID {offer_id}, goal='{goal}', "
        f"platform='{platform}', language={language}. Steps:\n"
        f"1. Call get_offer_context_summary(offer_id='{offer_id}') for full context.\n"
        "2. Identify the single strongest pain_point and selling_point combination from the KB "
        "that maps to the stated goal. Call list_knowledge with knowledge_type filter to narrow.\n"
        f"3. Call run_app(app_id='script_writer', action='generate', offer_id=..., goal='{goal}', "
        f"platform_id='{platform}') — use persona/structure matching the platform.\n"
        "4. Verify: hook in first 3s; claims cited to KB; CTA aligned with goal.\n"
        "5. Save via save_creation with content_type='video_script' and tags=['"
        f"{platform}', '{goal}']."
    )


@mcp.prompt()
async def knowledge_gap_report(merchant_id: str) -> str:
    """Audit which offers have weak or missing knowledge coverage (selling points,
    pain points, proofs, FAQs, audiences) — the prerequisite check before any
    agent-driven content push. Produces a per-offer punch list."""
    return (
        f"Audit the knowledge base coverage of merchant {merchant_id}. For each offer:\n"
        f"1. Call get_merchant_overview(merchant_id='{merchant_id}') for the full picture "
        "(all offers, their knowledge/asset counts, brand kits).\n"
        "2. For each offer, call list_knowledge(scope_type='offer', scope_id=<offer_id>) "
        "and bucket entries by knowledge_type.\n"
        "3. Score each offer's KB completeness against this rubric:\n"
        "   - selling_point ≥ 3 (Before-FABE structured) : 2 pts\n"
        "   - pain_point ≥ 2 : 2 pts\n"
        "   - proof ≥ 2 : 2 pts\n"
        "   - faq ≥ 5 : 2 pts\n"
        "   - audience ≥ 2 : 1 pt\n"
        "   - scenario ≥ 2 : 1 pt\n"
        "4. Output: a table — offer name / score / missing categories / 1-line suggestion "
        "of the highest-ROI entry to add next.\n"
        "5. Rank offers by lowest score to prioritize where to pour writing effort."
    )

