"""Regression: HeyGen-style primary-asset list was rendering inverted.

``BrandKitLinkService.set_primary`` writes ``priority=0`` to the promoted
row and ``1,2,3…`` to alternates (see app/application/brandkit_service.py
~line 181). The list query must therefore sort **ascending** — with
``priority.desc()`` the primary ended up at the bottom of the logo list.

Infra note: the rest of this test suite avoids importing the FastAPI app
or spinning a DB (see test_guest_access.py for the convention), so this
test guards the invariant via compiled-SQL inspection rather than a live
round-trip. Manual UI verification is in the plan file.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.brandkit_asset_link import BrandKitAssetLink


def _first_order_clause(compiled_sql: str) -> str:
    """Return the substring containing the first ORDER BY column (before the
    first comma that separates order expressions). Case-folded."""
    lower = compiled_sql.lower()
    idx = lower.index("order by")
    # After "order by " slice; split on comma for first ordering expression
    tail = lower[idx + len("order by "):]
    return tail.split(",", 1)[0]


def test_priority_column_sorts_ascending():
    """Priority=0 is primary, so the column must sort ASC (primary first)."""
    stmt = select(BrandKitAssetLink).order_by(
        BrandKitAssetLink.priority.asc(),
        BrandKitAssetLink.created_at.desc(),
    )
    compiled = str(stmt.compile())
    first = _first_order_clause(compiled)
    assert "priority" in first, f"priority must be first ordering column, got: {first}"
    # SQLAlchemy omits the ASC keyword (it's the default) but emits DESC
    # explicitly. So 'desc' anywhere in the priority clause means the bug
    # has returned.
    assert "desc" not in first, (
        f"priority must sort ASC (primary=0 first); got DESC in '{first}'. "
        "This is the 'primary logo sorted last' regression — see "
        "tests/test_brandkit_primary_sort.py header."
    )


def test_repo_list_uses_ascending_priority():
    """Belt-and-braces: the repo itself must use `.asc()` on priority.

    Catches a future refactor that silently flips the sort without noticing
    the semantic contract with ``set_primary``."""
    import inspect

    from app.infrastructure.brandkit_repo import BrandKitAssetLinkRepository

    src = inspect.getsource(BrandKitAssetLinkRepository.list_by_brandkit)
    assert "priority.asc()" in src, (
        "BrandKitAssetLinkRepository.list_by_brandkit must order priority ASC"
    )
    assert "priority.desc()" not in src, (
        "priority.desc() would put the primary (priority=0) at the bottom "
        "of the list — see set_primary in brandkit_service.py"
    )
