"""Memory-system schemas — keep tight; the model is intentionally thin."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# A memory either applies to a single product (offer) or to every offer
# under a merchant (cross-product brand rules).
MemoryScopeType = Literal["merchant", "offer"]

# Which generator consumes the preference. Keep this intentionally
# narrow: current prompt assemblers consume image and script rules; "all"
# is for true brand-wide generation preferences.
MemorySurface = Literal["all", "image", "script"]

# Provenance — for audit and future "auto-suggest" UIs.
# 'manual' = user typed it via /offer.html memory tab.
# 'refine_capture' = user clicked "💾 记住" after a refine; source_ref
#   carries the parent job_id so we can trace which generation taught us.
# 'mcp' = saved by an external agent through the MCP tool.
MemorySource = Literal["manual", "refine_capture", "mcp"]


class MemoryCreate(BaseModel):
    """Body for POST /api/v1/memories."""

    scope_type: MemoryScopeType
    scope_id: UUID
    content: str = Field(..., min_length=1, max_length=500)
    surface: MemorySurface = "all"
    # Server overrides this for non-manual flows; clients can omit.
    # When the cover panel chip submits, the request includes
    # source='refine_capture' + source_ref=<job_id>; manual UI submits
    # leave both unset (server defaults to 'manual').
    source: MemorySource = "manual"
    source_ref: str | None = Field(None, max_length=256)


class MemoryUpdate(BaseModel):
    """Body for PATCH /api/v1/memories/{id}.

    All fields optional — caller specifies only what changes. Toggling
    is_active=False is the soft-delete path used by the UI's "stop
    using this preference" affordance; hard delete uses DELETE.
    """

    content: str | None = Field(None, min_length=1, max_length=500)
    surface: MemorySurface | None = None
    is_active: bool | None = None


class MemoryResponse(BaseModel):
    id: UUID
    merchant_id: UUID
    scope_type: MemoryScopeType
    scope_id: UUID
    surface: MemorySurface
    content: str
    source: MemorySource
    source_ref: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    """List endpoint result — flat array, no pagination needed at
    v1 caps (50 entries per scope)."""

    items: list[MemoryResponse]
