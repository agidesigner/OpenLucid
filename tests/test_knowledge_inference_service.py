"""v1.2.0 — guard the new shared knowledge-inference service.

These tests follow the source-inspection convention from
``test_guest_access.py`` / ``test_mcp_unify.py`` (no DB boot, no
FastAPI app spinup). The actual end-to-end "spin up Postgres + call a
real LLM" path is covered by the v1.2.0 dogfood verification, not by
unit tests.

Three contracts are pinned:

1. **build_offer_data shape** — the dict the adapter sees has the same
   keys regardless of which entry point built it. A drift here
   re-introduces the v1.1.3-class read/write asymmetry.
2. **friendly_llm_error coverage** — every OpenAI SDK exception class
   the WebUI handles must also be handled by the shared helper, so MCP
   / CLI errors carry the same Chinese-localised hint about the
   model + Settings link.
3. **infer_and_persist_offer_knowledge contract** — the service's
   docstring + body must show: (a) provenance fields are stamped on
   every persisted row (source_type / source_ref / confidence),
   (b) adapter failure does NOT raise out of the service (best-effort
   contract that v1.2.0's MCP create_offer path depends on),
   (c) the trigger string is interpolated into source_ref so an audit
   can distinguish create_offer vs. infer_knowledge_for_offer
   inferences.
"""
from __future__ import annotations

import inspect


# ── 1. build_offer_data shape ─────────────────────────────────────────


class TestBuildOfferDataShape:
    def test_canonical_keys_present(self):
        from app.application.knowledge_inference_service import build_offer_data
        out = build_offer_data(
            offer_name="x", offer_type="product", description=None,
        )
        # The five top-level keys the prompt-builder template expects.
        # Same shape as ``app/api/ai.py:_build_offer_data`` returned
        # pre-v1.2.0; the WebUI endpoint now wraps over this helper.
        for k in ("offer", "selling_points", "target_audiences",
                  "target_scenarios", "knowledge_items"):
            assert k in out, f"build_offer_data missing canonical key {k}"

    def test_offer_subdict_carries_name_type_description(self):
        from app.application.knowledge_inference_service import build_offer_data
        out = build_offer_data(
            offer_name="Acme", offer_type="service", description="hello",
        )
        assert out["offer"] == {
            "name": "Acme",
            "offer_type": "service",
            "description": "hello",
        }

    def test_none_description_normalises_to_empty_string(self):
        """LLM template doesn't tolerate None — pre-v1.2.0 the endpoint
        forwarded ``body.description`` (a str default ``""``); the
        service signature accepts None for MCP convenience and must
        normalise so the prompt body never gets a literal ``None``."""
        from app.application.knowledge_inference_service import build_offer_data
        out = build_offer_data(
            offer_name="x", offer_type="product", description=None,
        )
        assert out["offer"]["description"] == ""

    def test_existing_knowledge_passes_through(self):
        from app.application.knowledge_inference_service import build_offer_data
        existing = [{"knowledge_type": "faq", "title": "Q1", "content_raw": "A1"}]
        out = build_offer_data(
            offer_name="x", offer_type="product", description=None,
            existing_knowledge=existing,
        )
        assert out["knowledge_items"] == existing

    def test_existing_knowledge_default_is_empty_list(self):
        """Adapter's prompt-builder iterates ``knowledge_items`` —
        ``None`` would TypeError. Default must be a list."""
        from app.application.knowledge_inference_service import build_offer_data
        out = build_offer_data(
            offer_name="x", offer_type="product", description=None,
        )
        assert out["knowledge_items"] == []


# ── 2. friendly_llm_error coverage ────────────────────────────────────


class TestFriendlyLlmErrorCoverage:
    """The service helper must handle the same five OpenAI SDK
    exception classes the WebUI endpoint did. Missing one means an
    MCP / CLI caller gets the bare ``str(e)`` instead of the
    Chinese-localised "switch model in Settings" hint."""

    def test_handles_all_documented_openai_exceptions(self):
        import inspect as _inspect
        from app.application.knowledge_inference_service import friendly_llm_error
        src = _inspect.getsource(friendly_llm_error)
        for cls in ("APITimeoutError", "APIConnectionError",
                    "RateLimitError", "AuthenticationError", "BadRequestError"):
            assert cls in src, (
                f"friendly_llm_error doesn't branch on {cls} — MCP/CLI "
                "caller would get the raw OpenAI error instead of the "
                "WebUI-style Chinese hint."
            )

    def test_includes_model_label_in_message(self):
        """Every branch's message must include ``provider/model`` so
        the user can tell which model failed when multiple LLMs are
        configured (which is the common multi-scene setup)."""
        import inspect as _inspect
        from app.application.knowledge_inference_service import friendly_llm_error
        src = _inspect.getsource(friendly_llm_error)
        assert "f\"{provider}/{model}\"" in src or "label = " in src


# ── 3. infer_and_persist_offer_knowledge contract ────────────────────


class TestInferAndPersistContract:
    def _src(self) -> str:
        from app.application.knowledge_inference_service import KnowledgeInferenceService
        return inspect.getsource(KnowledgeInferenceService.infer_and_persist_offer_knowledge)

    def test_method_is_async(self):
        from app.application.knowledge_inference_service import KnowledgeInferenceService
        assert inspect.iscoroutinefunction(
            KnowledgeInferenceService.infer_and_persist_offer_knowledge
        )

    def test_provenance_fields_stamped(self):
        """Every persisted row must carry source_type / source_ref /
        confidence so the WebUI can show AI-vs-manual badges and an
        audit can trace each row back to its trigger."""
        src = self._src()
        assert '"ai_inferred"' in src, "source_type=ai_inferred must be stamped"
        # source_ref is interpolated with the trigger
        assert "auto-infer:" in src, "source_ref prefix must be auto-infer:"
        # confidence is forwarded from the adapter result
        assert "confidence" in src, "confidence must be forwarded from adapter"

    def test_trigger_interpolated_into_source_ref(self):
        """``trigger`` parameter must reach the persisted source_ref
        so an audit can distinguish e.g. create_offer-time inferences
        from manual infer_knowledge_for_offer re-runs."""
        src = self._src()
        # Find the source_ref construction. Pattern:
        # ``source_ref = f"auto-infer:{trigger}:{offer_id}"``
        assert "auto-infer:{trigger}:" in src or "auto-infer:" in src and "trigger" in src

    def test_adapter_failure_returns_report_does_not_raise(self):
        """Plan section 'Provenance / 可审计性' + create_offer's
        infer_knowledge=True path both depend on this contract: the
        adapter raising must not propagate out of the service. v1.2.0's
        MCP create_offer assumes a Report comes back even on failure
        and surfaces ``inference_status: {success: false, ...}``
        without rolling back the offer creation."""
        src = self._src()
        assert "except Exception as e:" in src
        # The except block must construct a report with success=False,
        # not re-raise.
        except_idx = src.find("except Exception as e:")
        # 800 chars after the except is enough to capture the report
        # construction
        tail = src[except_idx:except_idx + 800]
        assert "success=False" in tail, (
            "adapter exception block must build a Report(success=False) "
            "rather than re-raise — agents/UIs depend on the fail-open "
            "contract"
        )
        assert "raise" not in tail.split("return")[0], (
            "no raise before return inside the except block"
        )

    def test_empty_adapter_response_is_success_with_zero(self):
        """Adapter returning an empty dict (no items in any type) is
        a valid non-failure outcome — the LLM just had nothing to say.
        Reporting it as success=True with written_count=0 lets the
        agent branch on ``written_count`` for "should I retry?" vs.
        ``success=False`` for hard errors."""
        src = self._src()
        # Anchors that prove the empty-dict short-circuit exists
        assert "not isinstance(raw, dict)" in src or "not any(raw.get" in src
        # Within the empty-handling block, success=True must appear
        # before the persist loop.
        empty_idx = src.find("not any(raw.get")
        assert empty_idx > 0
        # Widen the window enough to span the explanatory comment +
        # KnowledgeInferenceReport(...) literal — comments can grow
        # without changing the contract.
        post = src[empty_idx:empty_idx + 1200]
        assert "success=True" in post

    def test_uses_v116_unique_constraint_for_safe_rerun(self):
        """Re-inferring an offer with existing rows must update in
        place via find_by_title (the v1.1.6 ``uq_knowledge_title``
        unique-constraint pattern), not duplicate. Anchor on the
        find_by_title call."""
        src = self._src()
        assert "find_by_title" in src

    def test_skips_malformed_items(self):
        """Adapter occasionally returns items missing ``title`` (LLM
        truncation). These must be skipped, not crash the loop."""
        src = self._src()
        assert "if not title:" in src

    def test_service_uses_knowledge_scene_adapter(self):
        """The model must come from ``scene_key="knowledge"`` so the
        whole project's knowledge inferences (WebUI wizard, MCP create,
        REST endpoint) share the same prompt + same model + same
        temperature. Drift across scenes is the v1.1.6-class bug
        applied to AI config."""
        src = self._src()
        assert 'scene_key="knowledge"' in src
