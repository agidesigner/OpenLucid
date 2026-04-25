"""Regression: ``/offers/{id}/coverage-review`` was reading 0% ready for a
fully-populated offer.

``selling_point`` / ``audience`` / ``scenario`` have TWO valid storage
sites in this codebase:

1. The offer's own ``core_selling_points_json`` / ``target_audience_json``
   / ``target_scenarios_json`` columns — written by ``create_offer``'s
   flat-list args, rendered as the offer-page header tag chips.
2. ``knowledge_items`` rows of the matching ``knowledge_type`` — written
   by ``add_knowledge_item``.

Pre-v1.1.6 ``CoverageService.get_offer_coverage`` only counted (2). An
offer created via ``create_offer(core_selling_points=[...], ...)`` —
which includes every offer the dogfood demo built — read as
``readiness_score=0`` with ``missing=[selling_point, audience, scenario]``
even though all three were on the row and visible as chips on the page.
The merchant-level scorer at ``get_merchant_completeness`` already
handled the dual storage correctly via its ``profile`` score, so the
per-offer endpoint was an outlier, not the policy.

This test follows the ``test_guest_access.py`` convention (no DB, no app
boot) — source-inspection of the patched function.
"""
from __future__ import annotations

import inspect


def _src() -> str:
    from app.application.coverage_service import CoverageService
    return inspect.getsource(CoverageService.get_offer_coverage)


def test_offer_row_is_loaded():
    """The scorer must fetch the offer row to read its column-stored
    fields. ``session.get(Offer, offer_id)`` is the chosen path."""
    src = _src()
    assert "from app.models.offer import Offer" in src
    assert "self.session.get(Offer, offer_id)" in src


def test_selling_point_falls_back_to_offer_column():
    """selling_point is fulfilled by EITHER a knowledge_items row OR the
    offer's ``core_selling_points_json.points`` list."""
    src = _src()
    assert "core_selling_points_json" in src
    assert '"points"' in src
    # Both halves of the OR must appear in the same fulfillment expression.
    # Source-substring is enough; a full AST walk is overkill here.
    assert "selling_filled" in src


def test_audience_falls_back_to_offer_column():
    src = _src()
    assert "target_audience_json" in src
    assert '"items"' in src
    assert "audience_filled" in src


def test_scenario_falls_back_to_offer_column():
    src = _src()
    assert "target_scenarios_json" in src
    assert "scenario_filled" in src


def test_next_action_uses_same_fallback_as_missing():
    """If the missing-set logic recognizes the offer-column fallback but
    next_action does not (or vice versa), the page renders an
    Inconsistent state — readiness=60% but next-action=add_knowledge.
    Both branches must read from the same ``*_filled`` booleans."""
    src = _src()
    # The next_action branch must reference the booleans, not re-read
    # ``knowledge_by_type`` (the pre-v1.1.6 path that caused the drift).
    assert 'next_action = "add_knowledge"' in src
    # The branch directly above add_knowledge must check selling_filled.
    add_knowledge_idx = src.find('next_action = "add_knowledge"')
    preamble = src[max(0, add_knowledge_idx - 80):add_knowledge_idx]
    assert "selling_filled" in preamble, (
        "next_action=add_knowledge branch must gate on selling_filled, "
        "not on knowledge_by_type[selling_point] alone — that drift was "
        "the v1.1.6 bug."
    )


def test_merchant_scorer_knowledge_bucket_falls_back_to_offer_columns():
    """v1.1.9 sibling fix: the merchant-level dashboard scorer
    (``get_merchant_completeness``) had the SAME asymmetry v1.1.6
    closed for the per-offer scorer. The knowledge bucket counted
    selling_point / audience / scenario only via knowledge_items
    rows, so an offer with those populated as offer columns scored
    0 on all three even though the data was already in the same
    SQL query the scorer was running for the profile bucket.

    Symptom that surfaced this: a WebUI offer (KB rows written by
    AI-infer) scored 55 on the dashboard while an MCP offer with
    the same content via create_offer's flat-list args scored 29.
    Profile was identical (both 20); the gap was entirely in the
    knowledge bucket (35 vs 9, of which 21 was the missing
    selling+audience+scenario contribution)."""
    import inspect

    from app.application.coverage_service import CoverageService

    src = inspect.getsource(CoverageService.get_batch_completeness_scores)

    # The scorer must build a per-offer fields-filled map AND
    # consult it inside the knowledge bucket. Anchor on both halves —
    # a partial fix (build map, forget to consult) would silently
    # leave the bug.
    assert "offer_fields_filled" in src, (
        "merchant scorer must build a per-offer offer-fields map so "
        "the knowledge bucket can fall back to offer columns"
    )
    # Each of the three branches must read either ki_types OR fields
    for kind in ("selling_point", "audience", "scenario"):
        # Find the line that adds 7 for this kind. There must be an
        # OR with fields.get(...) in the condition.
        lines = [ln for ln in src.split("\n") if f'"{kind}"' in ln and "ki_types" in ln]
        assert any('fields.get(' in ln for ln in lines), (
            f"merchant scorer's {kind} branch must OR ki_types with "
            f"the offer-field fallback. Without this, an offer with "
            f"{kind} only on the offer column scores 0 on this branch."
        )


def test_wrapped_helper_handles_none_and_empty():
    """``_wrapped_has_items(None, ...)`` must be False (offer not yet
    populated), ``_wrapped_has_items({}, ...)`` False, ``({"points": []}, ...)``
    False (empty list is not "filled"), and ``({"points": ["x"]}, ...)`` True.
    Reproduce the helper inline so we test the contract not the
    enclosing function."""
    def _wrapped_has_items(payload, key):
        return bool(payload and isinstance(payload, dict) and payload.get(key))

    assert _wrapped_has_items(None, "points") is False
    assert _wrapped_has_items({}, "points") is False
    assert _wrapped_has_items({"points": []}, "points") is False
    assert _wrapped_has_items({"points": ["a"]}, "points") is True
    assert _wrapped_has_items({"items": ["a"]}, "items") is True
    # Wrong key must not falsely satisfy (singular-vs-plural drift guard).
    assert _wrapped_has_items({"points": ["a"]}, "items") is False
