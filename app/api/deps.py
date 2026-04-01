from typing import Annotated

from fastapi import Depends, Query

from app.database import get_db

# Re-export for convenience
DBSession = Annotated["AsyncSession", Depends(get_db)]


def pagination_params(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> dict[str, int]:
    return {"page": page, "page_size": page_size}


PaginationDep = Annotated[dict[str, int], Depends(pagination_params)]
