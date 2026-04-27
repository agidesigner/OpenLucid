from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request

from app.database import get_db

# Re-export for convenience
DBSession = Annotated["AsyncSession", Depends(get_db)]


def pagination_params(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> dict[str, int]:
    return {"page": page, "page_size": page_size}


PaginationDep = Annotated[dict[str, int], Depends(pagination_params)]


async def require_owner(request: Request) -> str:
    """Reject guest sessions. Use on endpoints that manage the guest toggle
    itself or any other action that must stay inside the owner's scope even
    when the middleware allowlist would otherwise permit it.

    ``api-token`` and ``no-auth`` ARE accepted as owner here — they
    represent the deployment operator (CLI / API key) and the open-
    access mode respectively. Only ``guest`` is rejected, because guests
    must not be able to flip the guest toggle themselves."""
    from app.api.auth import SENTINEL_GUEST
    uid = getattr(request.state, "user_id", None)
    if not uid or uid == SENTINEL_GUEST:
        raise HTTPException(status_code=403, detail="Owner-only endpoint")
    return uid
