"""Regression: knowledge_items needs a DB-level UNIQUE on the upsert key.

``KnowledgeService.batch_upsert`` dedups on
``(scope_type, scope_id, knowledge_type, title)`` but the DB didn't
enforce it, so concurrent calls (or a title edit that collided with an
existing row) could produce duplicates. Migration ``e8t9u0v1w2x3``
creates ``uq_knowledge_title``; the ORM mirrors it via ``__table_args__``
on ``KnowledgeItem``.

Test infra note: no DB fixture (see test_guest_access.py). We verify the
constraint at the metadata layer — which is what both SQLAlchemy uses
for validation and what Alembic autogenerate diffs against.
"""
from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models.knowledge_item import KnowledgeItem


def test_knowledge_item_has_unique_title_constraint():
    """The (scope_type, scope_id, knowledge_type, title) tuple must be
    unique at the DB level so upsert collisions never silently duplicate.
    """
    uniques = [
        c for c in KnowledgeItem.__table__.constraints
        if isinstance(c, UniqueConstraint)
    ]
    named = {c.name: c for c in uniques}
    assert "uq_knowledge_title" in named, (
        "KnowledgeItem must declare UNIQUE(scope_type, scope_id, "
        "knowledge_type, title) named 'uq_knowledge_title' — see "
        "alembic/versions/e8t9u0v1w2x3_knowledge_unique_title.py"
    )
    cols = [c.name for c in named["uq_knowledge_title"].columns]
    assert cols == ["scope_type", "scope_id", "knowledge_type", "title"], (
        f"uq_knowledge_title must cover exactly the upsert key; got {cols}"
    )


def test_constraint_columns_match_find_by_title():
    """Belt-and-braces: the columns in the unique constraint must exactly
    match what ``KnowledgeItemRepository.find_by_title`` filters on. If
    someone changes one without the other, upserts start producing
    duplicates again.
    """
    import inspect

    from app.infrastructure.knowledge_repo import KnowledgeItemRepository

    src = inspect.getsource(KnowledgeItemRepository.find_by_title)
    for col in ("scope_type", "scope_id", "knowledge_type", "title"):
        assert f"KnowledgeItem.{col}" in src, (
            f"find_by_title must still filter on {col} to match "
            f"uq_knowledge_title; look for drift with the UNIQUE constraint"
        )
