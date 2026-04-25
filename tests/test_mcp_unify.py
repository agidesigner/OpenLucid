"""Locks in the v1.1.3 MCP field-name unification.

Three classes of regression are guarded:

1. **Offer JSON-wrapper unwrap** (``_unwrap_offer_json_fields``): MCP
   tools that emit OfferResponse-shaped payloads must flatten the legacy
   ``foo_json: {wrapper_key: [...]}`` columns to flat ``foo: [...]`` so
   read shape matches the input shape of ``create_offer``. A drift here
   re-introduces the round-trip booby trap.

2. **``add_knowledge_item`` field surface**: must take ``content_raw``
   (matching the REST schema and ``list_knowledge`` output) plus the
   provenance fields ``source_type`` / ``source_ref`` / ``confidence`` /
   ``tags``. The legacy ``content`` alias must still resolve, but be
   visibly second-class (deprecation log).

3. **``KnowledgeSourceType`` enum**: ``ai_inferred`` and ``web_extract``
   must be present so the MCP add path can label AI-derived entries
   without pydantic silently rejecting the call.

The tests follow the existing ``test_guest_access.py`` convention: pure
logic, no DB, no FastAPI app boot. Source-inspection guards complement
the pure-function checks for the surface-area items that are too
intertwined with the DB to test live.
"""
from __future__ import annotations

import inspect

import pytest

from app.domain.enums import KnowledgeSourceType
from app.mcp_server import _unwrap_offer_json_fields


# ── 1. _unwrap_offer_json_fields ─────────────────────────────────────


class TestUnwrapOfferJsonFields:
    """Pure-function tests for the offer-shape flattener."""

    def test_unwraps_core_selling_points(self):
        out = _unwrap_offer_json_fields({
            "core_selling_points_json": {"points": ["A", "B", "C"]},
        })
        assert out == {"core_selling_points": ["A", "B", "C"]}

    def test_unwraps_target_audience_with_singular_to_plural_rename(self):
        # The DB column is singular, the agent-facing name is plural — this
        # is the rename we lock in: ``target_audience_json`` → ``target_audiences``.
        out = _unwrap_offer_json_fields({
            "target_audience_json": {"items": ["Creators", "Sellers"]},
        })
        assert out == {"target_audiences": ["Creators", "Sellers"]}

    def test_unwraps_all_five_offer_wrappers_in_one_payload(self):
        payload = {
            "id": "abc",
            "core_selling_points_json": {"points": ["sp1"]},
            "target_audience_json": {"items": ["aud1"]},
            "target_scenarios_json": {"items": ["sc1"]},
            "objections_json": {"items": ["o1"]},
            "proofs_json": {"items": ["p1"]},
        }
        out = _unwrap_offer_json_fields(payload)
        assert out == {
            "id": "abc",
            "core_selling_points": ["sp1"],
            "target_audiences": ["aud1"],
            "target_scenarios": ["sc1"],
            "objections": ["o1"],
            "proofs": ["p1"],
        }

    def test_none_value_passes_through(self):
        """A wrapped field that's None on the row should land as None
        under the new key (not crash, not silently drop)."""
        out = _unwrap_offer_json_fields({
            "core_selling_points_json": None,
        })
        assert out == {"core_selling_points": None}

    def test_unknown_wrapper_shape_preserved_under_new_key(self):
        """If the value isn't ``{wrapper_key: [...]}`` (e.g. a raw list,
        or a dict missing the wrapper) the unwrap must not drop the data
        — it gets renamed but kept verbatim so debugging is possible."""
        weird = {"core_selling_points_json": ["already-flat"]}
        out = _unwrap_offer_json_fields(weird)
        assert out == {"core_selling_points": ["already-flat"]}

        wrong_wrapper = {"core_selling_points_json": {"oops": ["x"]}}
        out = _unwrap_offer_json_fields(wrong_wrapper)
        assert out == {"core_selling_points": {"oops": ["x"]}}

    def test_idempotent_when_already_flat(self):
        flat = {"core_selling_points": ["A"]}
        assert _unwrap_offer_json_fields(flat) == flat

    def test_non_offer_keys_pass_through_untouched(self):
        payload = {"name": "Acme", "merchant_id": "m-1", "tags_json": {"x": "y"}}
        # ``tags_json`` is not in the offer wrapper map; it stays as-is.
        out = _unwrap_offer_json_fields(payload)
        assert out == payload

    def test_recurses_into_nested_dicts_and_lists(self):
        payload = {
            "merchant": {"id": "m"},
            "offers": [
                {"id": "o1", "core_selling_points_json": {"points": ["a"]}},
                {"id": "o2", "core_selling_points_json": {"points": ["b"]}},
            ],
        }
        out = _unwrap_offer_json_fields(payload)
        assert out["offers"][0]["core_selling_points"] == ["a"]
        assert out["offers"][1]["core_selling_points"] == ["b"]
        assert "core_selling_points_json" not in out["offers"][0]

    def test_top_level_list_payload(self):
        out = _unwrap_offer_json_fields([
            {"core_selling_points_json": {"points": ["x"]}},
            {"core_selling_points_json": {"points": ["y"]}},
        ])
        assert out == [
            {"core_selling_points": ["x"]},
            {"core_selling_points": ["y"]},
        ]


# ── 2. add_knowledge_item surface ────────────────────────────────────


class TestAddKnowledgeItemSurface:
    """Lock the v1.1.3 surface: canonical name + new fields + legacy alias.

    The function opens a DB session so we don't invoke it; signature
    inspection is enough to catch the regressions that motivated this
    change (silent field drop, missing provenance)."""

    def _params(self):
        from app.mcp_server import add_knowledge_item
        return inspect.signature(add_knowledge_item).parameters

    def test_canonical_field_is_content_raw(self):
        params = self._params()
        assert "content_raw" in params, (
            "MCP add_knowledge_item must expose ``content_raw`` (the "
            "schema/DB/list_knowledge name) as the canonical body field."
        )

    def test_legacy_content_alias_still_present(self):
        params = self._params()
        assert "content" in params, (
            "Legacy ``content`` param must remain accepted for "
            "backwards-compat. Removing it without a deprecation cycle "
            "breaks every script written before v1.1.3."
        )

    def test_provenance_fields_exposed(self):
        """source_type / source_ref / confidence / tags must all be
        callable params. Their absence in the original surface meant
        every MCP-written KB row was silently ``manual`` with no
        confidence — indistinguishable from a hand-typed entry."""
        params = self._params()
        for required in ("source_type", "source_ref", "confidence", "tags"):
            assert required in params, (
                f"MCP add_knowledge_item missing ``{required}``. "
                "Pre-v1.1.3 every AI-inferred entry written via MCP got "
                "stamped source_type=manual with no confidence; the "
                "fields are now first-class so the WebUI can show "
                "verified-vs-suggested badges."
            )

    def test_deprecation_warning_text_in_source(self):
        """The deprecation log must mention BOTH the old and new names so
        a grep on operators' logs finds the renames quickly."""
        from app.mcp_server import add_knowledge_item
        src = inspect.getsource(add_knowledge_item)
        assert "deprecated" in src.lower()
        assert "content_raw" in src
        assert "content" in src


# ── 3. KnowledgeSourceType enum ──────────────────────────────────────


class TestKnowledgeSourceTypeEnum:
    """The MCP add_knowledge_item ``source_type`` param has to map to a
    valid pydantic enum — otherwise validation rejects the value inside
    fastmcp's tool wrapper and the call silently fails (no row inserted,
    no obvious error in logs).
    """

    def test_ai_inferred_value_present(self):
        assert KnowledgeSourceType("ai_inferred") == KnowledgeSourceType.AI_INFERRED

    def test_web_extract_value_present(self):
        assert KnowledgeSourceType("web_extract") == KnowledgeSourceType.WEB_EXTRACT

    def test_legacy_values_preserved(self):
        """Pre-v1.1.3 values must still parse — DB rows with these
        source_types exist on every running deployment."""
        for legacy in ("manual", "file", "url", "imported"):
            assert KnowledgeSourceType(legacy).value == legacy


# ── 4. KnowledgeItemUpdate schema (v1.1.4 fields) ────────────────────


class TestKnowledgeItemUpdateSchema:
    """Pre-v1.1.4 ``KnowledgeItemUpdate`` was missing ``confidence``,
    ``source_type``, and ``source_ref`` — so a PATCH that tried to bump
    a confidence score (re-run AI inference, owner verified an AI
    suggestion, etc.) silently no-op'd without error. This locks them
    in as accepted, optional update fields."""

    def test_update_schema_accepts_confidence(self):
        from app.schemas.knowledge import KnowledgeItemUpdate
        u = KnowledgeItemUpdate(confidence=0.95)
        assert u.confidence == 0.95

    def test_update_schema_accepts_source_type_and_ref(self):
        from app.schemas.knowledge import KnowledgeItemUpdate
        u = KnowledgeItemUpdate(source_type="ai_inferred", source_ref="run-2026-04-25")
        assert u.source_type.value == "ai_inferred"
        assert u.source_ref == "run-2026-04-25"

    def test_update_schema_omits_unchanged_fields(self):
        """exclude_unset round-trip: a PATCH with only confidence must
        not zero out title / content_raw on the row."""
        from app.schemas.knowledge import KnowledgeItemUpdate
        u = KnowledgeItemUpdate(confidence=0.9)
        dumped = u.model_dump(exclude_unset=True)
        assert dumped == {"confidence": 0.9}


# ── 5. MCP update / delete knowledge tools (v1.1.4) ──────────────────


class TestKnowledgeItemUpdateAndDelete:
    """The pre-v1.1.4 MCP surface had no way to fix or remove a KB
    entry — once added, the only escape was raw REST PATCH/DELETE.
    These tests pin the new tools' presence + signatures so a future
    refactor that drops them gets caught immediately."""

    def test_update_tool_exists_and_is_async(self):
        import inspect
        from app.mcp_server import update_knowledge_item
        assert inspect.iscoroutinefunction(update_knowledge_item)
        params = inspect.signature(update_knowledge_item).parameters
        # All write fields must be patchable
        for p in ("item_id", "title", "content_raw", "knowledge_type",
                  "language", "source_type", "source_ref", "confidence", "tags"):
            assert p in params, (
                f"update_knowledge_item missing ``{p}`` — partial patch "
                "surface defeats the whole point of the tool."
            )

    def test_delete_tool_exists_and_is_async(self):
        import inspect
        from app.mcp_server import delete_knowledge_item
        assert inspect.iscoroutinefunction(delete_knowledge_item)
        params = inspect.signature(delete_knowledge_item).parameters
        assert "item_id" in params

    def test_update_empty_payload_returns_error_not_silent_noop(self):
        """An update call that touches zero fields should surface an
        error, not silently 200. Locks the empty-patch hint added in
        v1.1.4 so an agent debugging "nothing changed" gets a clue."""
        import inspect
        from app.mcp_server import update_knowledge_item
        src = inspect.getsource(update_knowledge_item)
        assert "no fields provided" in src or '"error"' in src


# ── 6. add_knowledge_item commit-rollback safety (bug #10) ───────────


class TestAddKnowledgeCommitOrder:
    """v1.1.3 dogfood found that an exception during _serialize ran AFTER
    session.commit(), so a failed call still left a half-row in the DB
    that needed manual cleanup. v1.1.4 reorders to flush → refresh →
    serialize → commit so any failure during build / serialize triggers
    the async-with rollback. Source-inspection guards the order."""

    def test_serialize_runs_before_commit(self):
        import inspect
        from app.mcp_server import add_knowledge_item
        src = inspect.getsource(add_knowledge_item)
        # The result variable must be assigned BEFORE session.commit()
        result_idx = src.find("result = _serialize")
        commit_idx = src.find("await session.commit()")
        assert result_idx > 0 and commit_idx > 0, "anchors moved — update test"
        assert result_idx < commit_idx, (
            "_serialize must run before session.commit() so a failed "
            "serialize triggers async-with rollback. Reverting this "
            "order re-introduces the v1.1.3 dirty-row bug — see commit "
            "message for the privacycrop._DEMO_MCP_ leftover incident."
        )


# ── 7. MCP update / delete offer tools (v1.1.5) ──────────────────────


class TestOfferUpdateAndDelete:
    """Pre-v1.1.5 the MCP surface had ``create_offer`` but no
    ``update_offer`` / ``delete_offer`` — once an offer was created, the
    only way to fix a typo or drop it was raw REST. v1.1.5 closes the
    gap and additionally exposes a ``clear_fields`` channel for explicit
    NULLs (since ``None`` already means "leave alone")."""

    def test_update_offer_exists_and_is_async(self):
        import inspect
        from app.mcp_server import update_offer
        assert inspect.iscoroutinefunction(update_offer)
        params = inspect.signature(update_offer).parameters
        for p in ("offer_id", "name", "description", "positioning",
                  "core_selling_points", "target_audiences",
                  "target_scenarios", "objections", "proofs",
                  "locale", "status", "clear_fields"):
            assert p in params, f"update_offer missing ``{p}``"

    def test_delete_offer_exists_and_is_async(self):
        import inspect
        from app.mcp_server import delete_offer
        assert inspect.iscoroutinefunction(delete_offer)
        assert "offer_id" in inspect.signature(delete_offer).parameters

    def test_update_offer_takes_flat_list_shape(self):
        """``core_selling_points`` etc. must be ``list[str] | None`` — the
        same flat shape ``create_offer`` accepts and ``list_offers``
        emits. Anything else re-introduces the wrap/unwrap asymmetry the
        v1.1.3 unification was meant to kill."""
        import inspect
        from app.mcp_server import update_offer
        sig = inspect.signature(update_offer)
        for p in ("core_selling_points", "target_audiences",
                  "target_scenarios", "objections", "proofs"):
            ann = sig.parameters[p].annotation
            assert "list" in str(ann).lower(), (
                f"{p} must be a flat list shape, got {ann!r}"
            )

    def test_clear_fields_alias_maps_flat_names_to_json_columns(self):
        """The ``clear_fields=["core_selling_points"]`` MCP call must
        translate to nulling the ``core_selling_points_json`` DB column.
        Source-inspect the alias dict so a typo at either end of the
        rename is caught immediately."""
        import inspect
        from app.mcp_server import update_offer
        src = inspect.getsource(update_offer)
        # All five wrapped columns must have an entry in _CLEAR_ALIAS
        for flat, col in [
            ("core_selling_points", "core_selling_points_json"),
            ("target_audiences", "target_audience_json"),
            ("target_scenarios", "target_scenarios_json"),
            ("objections", "objections_json"),
            ("proofs", "proofs_json"),
        ]:
            assert f'"{flat}": "{col}"' in src, (
                f"_CLEAR_ALIAS missing or wrong for {flat} → {col}"
            )

    def test_update_offer_empty_payload_returns_error_not_silent_noop(self):
        import inspect
        from app.mcp_server import update_offer
        src = inspect.getsource(update_offer)
        assert "no fields provided" in src

    def test_update_offer_serialize_runs_before_commit(self):
        """Same rollback-safety contract as add_knowledge_item: the
        OfferResponse serialization must complete BEFORE session.commit()
        so any MissingGreenlet / serialization failure rolls back the
        write rather than leaving a half-committed row."""
        import inspect
        from app.mcp_server import update_offer
        src = inspect.getsource(update_offer)
        result_idx = src.find("result = _serialize")
        commit_idx = src.find("await session.commit()")
        assert result_idx > 0 and commit_idx > 0, "anchors moved — update test"
        assert result_idx < commit_idx


# ── 8. OfferRepository.update no longer filters None (bug #12) ───────


class TestMerchantDeleteAndDefaultingRule:
    """v1.1.7: closed two gaps surfaced during the PrivacyCrop dogfood.

    1. ``delete_merchant`` MCP tool didn't exist. Pre-v1.1.7 the only
       way to drop a workspace via MCP was: enumerate every offer,
       delete each, then escape to raw SQL for the merchant row.
       Symmetric to ``delete_offer`` (v1.1.5).
    2. Agents called ``create_merchant`` reflexively whenever they
       lacked a merchant_id, even when an existing merchant matched
       the user's intent. The new defaulting rule (one merchant ⇒
       use it; many ⇒ ask; zero ⇒ confirm before create) is
       documented in BOTH the FastMCP server instructions (visible
       at session start) and the ``create_offer`` tool docstring
       (visible at tool-list time)."""

    def test_delete_merchant_tool_exists_and_is_async(self):
        import inspect
        from app.mcp_server import delete_merchant
        assert inspect.iscoroutinefunction(delete_merchant)
        assert "merchant_id" in inspect.signature(delete_merchant).parameters

    def test_delete_merchant_warns_about_destructive_scope(self):
        """The docstring must call out that this nukes every offer
        + dependents — agents otherwise call it as a sibling of
        ``delete_offer`` and don't realize the blast radius."""
        import inspect
        from app.mcp_server import delete_merchant
        doc = (delete_merchant.__doc__ or "").lower()
        assert "destructive" in doc
        # Must enumerate the cascade so an agent reading the doc
        # before calling sees what's wiped.
        for term in ("offer", "knowledge", "brandkit", "asset"):
            assert term in doc, f"delete_merchant docstring missing ``{term}``"

    def test_server_instructions_carry_merchant_defaulting_rule(self):
        """Cross-user enforcement: the rule lives in the FastMCP
        ``instructions=`` payload so every agent gets it on connect,
        not in any single user's local memory."""
        from app.mcp_server import mcp
        instr = (mcp.instructions or "").lower()
        # Anchors that prove the rule is present + actionable
        assert "list_merchants" in instr
        # The "exactly one ⇒ use" branch
        assert "one merchant" in instr or "single merchant" in instr
        # The "multiple ⇒ ask" branch — the load-bearing branch that
        # prevents silent wrong-brand grounding
        assert "ask" in instr and ("multiple merchants" in instr or "multiple" in instr)
        # The "do not auto-create" instruction
        assert "create_merchant" in instr

    def test_create_offer_docstring_carries_merchant_defaulting_rule(self):
        """The rule must live on create_offer's docstring — agents
        often inspect tool docs at call time, not session start. v1.1.8
        moved the *enforcement* server-side, but the docstring still
        has to explain the contract so the agent knows what the error
        responses mean and when to ask the user vs. retry."""
        import inspect
        from app.mcp_server import create_offer
        doc = (create_offer.__doc__ or "").lower()
        # Contract anchors — must explain the three branches and the
        # "name reference is not a create signal" guard.
        assert "one merchant" in doc, "missing single-merchant auto-fill explanation"
        assert "multiple" in doc, "missing multi-merchant ask-the-user explanation"
        assert "ask" in doc
        # The "name reference is not a create signal" guard — protects
        # against the dogfood pattern of saying "create an offer for
        # PrivacyCrop" and getting a brand-new merchant.
        assert "name reference" in doc
        assert "explicit" in doc or "explicitly" in doc

    def test_merchant_repo_update_does_not_filter_none(self):
        """Same fix as v1.1.5 OfferRepository.update (bug #12) — pre-
        v1.1.7 ``MerchantRepository.update`` filtered None kwargs,
        breaking PATCH-null-to-clear. The fix doesn't change any
        observable v1.1.6 behavior because no UI/CLI flow currently
        clears merchant fields, but the asymmetry was a latent
        footgun and inconsistent with the offer side."""
        import inspect
        from app.infrastructure.merchant_repo import MerchantRepository
        src = inspect.getsource(MerchantRepository.update)
        assert "if value is not None" not in src
        assert "setattr(merchant, key, value)" in src

    def test_merchant_service_delete_cascades_via_offer_service(self):
        """The cascade must delegate per-offer cleanup to
        ``OfferService.delete`` — re-implementing the cascade
        per-table inline (the temptation) would silently drift from
        the offer-side cleanup any time it changes."""
        import inspect
        from app.application.merchant_service import MerchantService
        src = inspect.getsource(MerchantService.delete)
        assert "OfferService" in src
        assert "offer_svc.delete" in src
        # Plus the merchant-scoped sweeps for the three polymorphic tables
        for tbl in ("KnowledgeItem", "BrandKit", "Asset"):
            assert tbl in src, (
                f"MerchantService.delete must explicitly sweep "
                f"merchant-scoped {tbl} rows — they share scope_type/"
                "scope_id with offer rows but have no FK cascade."
            )


class TestMerchantDefaultingServerEnforcement:
    """v1.1.8: text instructions weren't enough — even with the rule
    in ``mcp.instructions`` and the ``create_offer`` docstring, agents
    rationalize around them ("user named PrivacyCrop ⇒ they want a new
    merchant"). v1.1.8 makes the rule a server-side constraint:

    - ``create_offer.merchant_id`` becomes optional. Server fills it
      when exactly one merchant exists, or returns a structured error
      naming the alternatives when ambiguous.
    - ``create_merchant`` rejects when other merchants exist unless
      ``confirm_intent=True``. Forces the agent to acknowledge.

    Same shape as ``UNIQUE`` constraints + ``ON CONFLICT`` — wrong
    calls fail fast with the data the agent needs to recover."""

    def test_create_offer_merchant_id_is_optional(self):
        import inspect
        from app.mcp_server import create_offer
        sig = inspect.signature(create_offer)
        mid = sig.parameters["merchant_id"]
        # Default must be None (the auto-fill trigger), not str-required.
        assert mid.default is None, (
            "create_offer.merchant_id must default to None so v1.1.8 can "
            "trigger the auto-fill / fail-loud path. Reverting to a "
            "required str re-introduces the v1.1.7 'agent invented a "
            "PrivacyCrop merchant' regression."
        )

    def test_create_offer_autofills_or_errors_on_no_merchant_id(self):
        """The body must enumerate all three cases: zero / one / many."""
        import inspect
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        # Calls list to discover existing merchants
        assert "MerchantService" in src
        assert ".list(" in src
        # All three branches present in the no-merchant_id path
        assert '"no_merchants"' in src, "missing zero-merchant error path"
        assert '"multiple_merchants_pick_one"' in src, "missing many-merchant error path"
        # Auto-fill: pulls the single merchant's id when total == 1
        assert "merchants[0].id" in src, "missing single-merchant auto-fill"

    def test_create_offer_error_includes_merchant_list_for_recovery(self):
        """The multiple-merchants error must include id+name pairs so
        the agent can re-call with the right merchant_id without a
        second round-trip to list_merchants."""
        import inspect
        from app.mcp_server import create_offer
        src = inspect.getsource(create_offer)
        # The error payload constructs {id, name} pairs — anchor on both
        assert '"id": str(m.id)' in src
        assert '"name": m.name' in src

    def test_create_merchant_has_confirm_intent_param(self):
        import inspect
        from app.mcp_server import create_merchant
        sig = inspect.signature(create_merchant)
        assert "confirm_intent" in sig.parameters, (
            "create_merchant must expose confirm_intent so the rule is "
            "an explicit override, not a hidden one."
        )
        ci = sig.parameters["confirm_intent"]
        assert ci.default is False, (
            "confirm_intent must default to False — making it required "
            "(no default) would break legacy callers; defaulting to True "
            "would make the guard useless."
        )

    def test_create_merchant_rejects_when_others_exist_without_confirm(self):
        import inspect
        from app.mcp_server import create_merchant
        src = inspect.getsource(create_merchant)
        assert '"merchants_exist"' in src
        # The guard must check BOTH conditions — others-exist AND
        # not-confirmed. Either alone produces the v1.1.7 footgun.
        assert "existing_total > 0" in src
        assert "not confirm_intent" in src
        # Recovery hint must include the existing list
        assert '"existing"' in src

    def test_create_merchant_emptyset_does_not_require_confirm(self):
        """Bootstrapping (zero merchants) must not require
        ``confirm_intent`` — the new-installation flow shouldn't trip
        the guard. Source-inspect the gate to confirm it's an
        AND-with-others-exist, not a unilateral block."""
        import inspect
        from app.mcp_server import create_merchant
        src = inspect.getsource(create_merchant)
        # The condition must require existing_total > 0 to fire — so
        # zero-merchant first-create still flows through.
        guard_idx = src.find('"merchants_exist"')
        assert guard_idx > 0
        preamble = src[max(0, guard_idx - 200):guard_idx]
        assert "existing_total > 0" in preamble


class TestOfferRepoUpdateAcceptsNone:
    """Pre-v1.1.5 ``OfferRepository.update`` skipped any kwarg whose
    value was ``None``. That broke "PATCH a field to null to clear it" —
    the canonical REST semantic — so calls like
    ``OfferUpdate(description=None)`` returned 200 with no DB change.
    The fix removes the filter; ``model_dump(exclude_unset=True)``
    upstream already guarantees only explicitly-set fields land in
    kwargs, so a ``None`` here is always intentional."""

    def test_repo_update_does_not_filter_none(self):
        import inspect
        from app.infrastructure.offer_repo import OfferRepository
        src = inspect.getsource(OfferRepository.update)
        # The pre-v1.1.5 bug was literally ``if value is not None:`` inside
        # the loop. Fail loudly if anyone re-introduces that filter.
        assert "if value is not None" not in src, (
            "OfferRepository.update must NOT filter None — that re-breaks "
            "PATCH-null-to-clear. See bug #12 in v1.1.5 release notes."
        )
        # Positive check: every kwarg is setattr'd unconditionally.
        assert "setattr(offer, key, value)" in src
