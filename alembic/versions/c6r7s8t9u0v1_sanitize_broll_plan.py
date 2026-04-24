"""Sanitize legacy ``creation.structured_content.broll_plan`` entries.

Revision ID: c6r7s8t9u0v1
Revises: b5q6r7s8t9u0
Create Date: 2026-04-24

Early LLM outputs wrote ``insert_after_char`` as a Chinese/English sentence
(the narration excerpt the LLM wanted the B-roll to cut over) instead of a
character offset integer. ``duration_seconds`` also frequently fell outside
the 5-10s spec. The video compositor coerced these at video-generation time,
but the bad values stuck in the database, so every Generate Video on that
creation paid the coerce cost and produced inconsistent results.

``_sanitize_broll_plan`` in ``app/application/script_writer_service.py`` is
now the single source of truth for schema enforcement and runs at script-
generation persist time. This migration replays that sanitizer over every
existing row so the stored data stops drifting from the spec.

Idempotent — reading a sanitized entry through the sanitizer returns the
same entry, so this can safely run multiple times (rollback + reapply).
"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = "c6r7s8t9u0v1"
down_revision = "b5q6r7s8t9u0"
branch_labels = None
depends_on = None


def _sanitize(raw_plan, narration: str) -> list[dict]:
    """Inline copy of script_writer_service._sanitize_broll_plan so this
    migration is self-contained and doesn't break if the app module moves.
    """
    if not isinstance(raw_plan, list):
        return []
    narration_len = len(narration or "")
    out: list[dict] = []
    for entry in raw_plan:
        if not isinstance(entry, dict):
            continue
        t = str(entry.get("type") or "illustrative").strip().lower()
        if t not in ("retention", "illustrative"):
            t = "illustrative"
        pos = entry.get("insert_after_char", 0)
        if isinstance(pos, bool):
            pos = 0
        if isinstance(pos, int):
            resolved = pos
        elif isinstance(pos, float):
            resolved = int(pos)
        elif isinstance(pos, str):
            needle = pos.strip()
            if needle.isdigit():
                resolved = int(needle)
            elif narration and needle:
                idx = narration.find(needle)
                if idx >= 0:
                    resolved = idx + len(needle)
                else:
                    continue
            else:
                continue
        else:
            continue
        if narration_len > 0:
            resolved = max(0, min(resolved, narration_len))
        else:
            resolved = max(0, resolved)

        dur_raw = entry.get("duration_seconds")
        try:
            dur = int(dur_raw) if dur_raw is not None else (5 if t == "retention" else 6)
        except (ValueError, TypeError):
            dur = 5 if t == "retention" else 6
        dur = max(5, min(dur, 10))
        if t == "retention":
            dur = 5

        prompt = str(entry.get("prompt") or "").strip()
        if not prompt:
            continue
        out.append({
            "type": t,
            "insert_after_char": resolved,
            "duration_seconds": dur,
            "prompt": prompt,
        })
    out.sort(key=lambda e: e["insert_after_char"])
    return out


def upgrade():
    conn = op.get_bind()
    # Only creations with an existing broll_plan array are candidates.
    rows = conn.execute(sa.text(
        "SELECT id, structured_content FROM creations "
        "WHERE jsonb_typeof(structured_content->'broll_plan') = 'array'"
    )).fetchall()

    fixed = 0
    for row in rows:
        sc = row.structured_content or {}
        old_plan = sc.get("broll_plan") or []
        sections = sc.get("sections") or {}
        section_ids = sc.get("section_ids") or list(sections.keys())
        narration = "".join((sections.get(sid) or {}).get("text", "") for sid in section_ids)

        new_plan = _sanitize(old_plan, narration)
        if new_plan == old_plan:
            continue  # already clean — skip the write to avoid churn
        new_sc = dict(sc)
        new_sc["broll_plan"] = new_plan
        conn.execute(
            sa.text("UPDATE creations SET structured_content = CAST(:sc AS JSONB) WHERE id = :id"),
            {"sc": json.dumps(new_sc, ensure_ascii=False), "id": row.id},
        )
        fixed += 1

    print(f"c6r7s8t9u0v1: sanitized broll_plan on {fixed}/{len(rows)} creations")


def downgrade():
    # Non-destructive migration — downgrade is a no-op. The sanitized state
    # is a strict superset of valid states, so nothing needs undoing.
    pass
