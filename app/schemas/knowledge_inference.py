"""v1.2.0 — schemas for the offer-knowledge inference workflow.

Single source of truth for the report shape returned by the new
``KnowledgeInferenceService.infer_and_persist_offer_knowledge`` method.
The same report is consumed by:

- The new REST endpoint ``POST /offers/{id}/infer-knowledge``
- The new MCP tool ``infer_knowledge_for_offer``
- The optional ``inference_report`` block returned from
  ``create_offer(infer_knowledge=True)``

Why a dedicated module: keeping the report shape co-located with the
service decouples it from ``schemas/ai.py`` (which is the WebUI-
facing wizard payload — different audience, different lifetime). If
the wizard payload changes, the agent-facing report shouldn't move.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel


class KnowledgeInferenceReport(BaseModel):
    """Result of running AI inference + persisting KB rows for an offer.

    The report is intentionally small and machine-friendly so an agent
    can branch on it — e.g. retry on ``success=False``, or surface
    ``written_count``/``by_type`` to the user without re-querying
    list_knowledge.
    """

    success: bool
    offer_id: uuid.UUID
    written_count: int = 0
    """How many new KB rows were inserted by this run."""
    updated_count: int = 0
    """How many existing rows were updated (matched on
    (scope_type, scope_id, knowledge_type, title) — the v1.1.6 unique
    constraint). AI re-runs over an existing offer normally have a
    mix of new + updated entries; the split helps the user see what
    the latest LLM pass actually changed."""
    by_type: dict[str, int] = {}
    """Per-type counts of rows touched (created + updated). Mirrors
    the layout of ``coverage-review.knowledge_by_type`` so the UI can
    diff before/after without a second query."""
    model_label: str | None = None
    """Display label of the LLM used (provider/model). Useful for
    audit when the same offer gets re-inferred under a different
    model and the KB content shifts."""
    reason: str | None = None
    """Populated when ``success=False`` — the friendly LLM error
    message (same format as ``_friendly_llm_error`` returns to the
    WebUI). When ``success=True`` this is None."""
