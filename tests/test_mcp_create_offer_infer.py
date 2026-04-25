"""v1.2.0 — guard the MCP-side knowledge-inference surface.

Pins three things:

1. ``create_offer`` exposes ``infer_knowledge: bool`` defaulting to
   False (v1.1.5 minimal-write semantic preserved)
2. ``create_offer`` calls KnowledgeInferenceService when the flag
   is True, AFTER the offer is committed (fail-open contract — AI
   failure mustn't roll back the offer)
3. The new ``infer_knowledge_for_offer`` MCP tool exists, is async,
   takes offer_id + optional language/user_hint, and routes to the
   service with the right trigger string

Source-inspection style — DB / app boot deferred to v1.2.0 dogfood
verification (test_guest_access.py convention).
"""
from __future__ import annotations

import inspect


# ── create_offer.infer_knowledge param + body wiring ─────────────────


class TestCreateOfferInferKnowledgeParam:
    def test_param_exists_with_false_default(self):
        from app.mcp_server import create_offer
        sig = inspect.signature(create_offer)
        assert "infer_knowledge" in sig.parameters, (
            "create_offer must expose infer_knowledge — without it the "
            "v1.2.0 'one-shot create + AI populate' path doesn't exist"
        )
        ik = sig.parameters["infer_knowledge"]
        assert ik.default is False, (
            "infer_knowledge default must be False to preserve v1.1.5 "
            "minimal-write semantic — switching to True silently incurs "
            "LLM cost on every legacy caller"
        )

    def test_body_imports_knowledge_inference_service(self):
        """Avoid a top-level import (would create a circular dep at
        load time if KIS imports anything from offer_service in the
        future). The import lives inside the ``if infer_knowledge``
        block so callers with infer_knowledge=False pay zero cost."""
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        assert "KnowledgeInferenceService" in src

    def test_inference_runs_after_commit_not_before(self):
        """Fail-open contract: offer creation commits FIRST, then AI
        inference runs in a fresh session. AI failure must not undo
        the offer. Anchor: the ``await session.commit()`` for the
        offer-create session must precede the ``KnowledgeInference
        Service`` invocation."""
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        # The offer's session.commit() is inside the first
        # ``async with _session_factory()`` block; the inference
        # service is invoked after.
        commit_idx = src.find("await session.commit()")
        kis_idx = src.find("KnowledgeInferenceService(session2)")
        assert commit_idx > 0 and kis_idx > 0, "anchors moved — update test"
        assert commit_idx < kis_idx, (
            "AI inference must run AFTER the offer is committed (fail-"
            "open). Reverting this order means an AI failure rolls back "
            "the offer creation — exactly the regression v1.2.0 plan "
            "says we don't want."
        )

    def test_inference_uses_create_offer_trigger(self):
        """Provenance: the source_ref written for create-time
        inferences must encode trigger=create_offer so an audit can
        distinguish them from infer_knowledge_for_offer re-runs."""
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        assert 'trigger="create_offer"' in src

    def test_inference_report_attached_to_response(self):
        """Agent must be able to see the report on the same response
        without a second tool call. v1.2.0 spec."""
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        assert '"inference_report"' in src


# ── infer_knowledge_for_offer tool ────────────────────────────────────


class TestInferKnowledgeForOfferTool:
    def test_tool_exists_and_is_async(self):
        from app.mcp_server import infer_knowledge_for_offer
        assert inspect.iscoroutinefunction(infer_knowledge_for_offer)

    def test_signature_matches_plan(self):
        from app.mcp_server import infer_knowledge_for_offer
        params = inspect.signature(infer_knowledge_for_offer).parameters
        assert "offer_id" in params
        assert "language" in params
        assert "user_hint" in params
        # offer_id is required (no default)
        assert params["offer_id"].default is inspect.Parameter.empty
        # language + user_hint are optional with None default
        assert params["language"].default is None
        assert params["user_hint"].default is None

    def test_uses_infer_knowledge_for_offer_trigger(self):
        from app.mcp_server import infer_knowledge_for_offer
        src = inspect.getsource(infer_knowledge_for_offer)
        assert 'trigger="infer_knowledge_for_offer"' in src

    def test_returns_report_json(self):
        """Return must be a JSON-serialised KnowledgeInferenceReport
        with the model_dump fields: success / written_count /
        updated_count / by_type / model_label / reason. Source-anchor:
        the response is built from ``report.model_dump(...)``."""
        from app.mcp_server import infer_knowledge_for_offer
        src = inspect.getsource(infer_knowledge_for_offer)
        assert "report.model_dump" in src
        assert "json.dumps(" in src

    def test_docstring_warns_about_provenance_and_safe_rerun(self):
        """Agents calling this should know (a) rows are stamped
        ai_inferred (review them, don't trust blindly), (b) re-running
        won't duplicate (uq_knowledge_title constraint)."""
        from app.mcp_server import infer_knowledge_for_offer
        doc = (infer_knowledge_for_offer.__doc__ or "").lower()
        assert "ai_inferred" in doc
        assert "duplicate" in doc or "constraint" in doc or "in place" in doc


# ── REST endpoint wiring ──────────────────────────────────────────────


class TestRestEndpointWiring:
    """The new POST /offers/{id}/infer-knowledge endpoint must route
    to KnowledgeInferenceService with trigger=manual:rest_endpoint
    (so audit logs can tell REST/CLI calls apart from MCP create-time
    inferences)."""

    def test_endpoint_function_exists(self):
        from app.api.offers import infer_offer_knowledge
        assert inspect.iscoroutinefunction(infer_offer_knowledge)

    def test_endpoint_uses_rest_trigger(self):
        from app.api.offers import infer_offer_knowledge
        src = inspect.getsource(infer_offer_knowledge)
        assert 'trigger="manual:rest_endpoint"' in src

    def test_endpoint_uses_knowledge_inference_service(self):
        from app.api.offers import infer_offer_knowledge
        src = inspect.getsource(infer_offer_knowledge)
        assert "KnowledgeInferenceService" in src
