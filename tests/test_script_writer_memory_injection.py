"""Regression: script_writer's memory-injection block must use the
correct DB session attribute.

Why this exists: v1.6.0 added a ``list_memories_for_offer(self.db, ...)``
call inside ``ScriptWriterService._prepare`` — but the class stores the
session on ``self.session`` (line 517), not ``self.db``. Every script
generation against an offer with memories enabled crashed in production
with ``AttributeError: 'ScriptWriterService' object has no attribute
'db'``, surfacing as a stacked ``生成失败，请重试`` toast for the user.
The unit tests didn't catch it because they mock memory_service from
outside; nothing exercised the actual call site.

These tests pin the contract at the call site: when the service injects
memories, it MUST pass ``self.session`` (the same session every other
method uses) — not ``self.db`` or any other typo.
"""
from __future__ import annotations

import ast


def _parse_service_module():
    import app.application.script_writer_service as svc_mod
    return ast.parse(open(svc_mod.__file__).read()), svc_mod


def test_memory_injection_uses_self_session_not_self_db():
    """AST-level guard. Every call to ``list_memories_for_offer`` inside
    script_writer_service must reference ``self.session`` as its first
    argument.

    AST inspection beats a behavioral mock for this failure mode: the
    bug is a typo in the attribute name (``self.db`` vs ``self.session``).
    A behavioral test that monkeypatches the function would still catch
    it (the typo evaluates first and raises AttributeError) but the AST
    check makes the intent and the failure message immediately readable.
    """
    tree, _ = _parse_service_module()

    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "ScriptWriterService"
    )
    init = next(
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    assigns_session = any(
        isinstance(stmt, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "session"
            for t in stmt.targets
        )
        for stmt in init.body
    )
    assert assigns_session, (
        "ScriptWriterService.__init__ must store the AsyncSession as "
        "self.session — every other method in the file already assumes this."
    )

    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "list_memories_for_offer":
            pass
        elif isinstance(func, ast.Attribute) and func.attr == "list_memories_for_offer":
            pass
        else:
            continue
        found += 1
        first = node.args[0] if node.args else None
        assert isinstance(first, ast.Attribute), (
            "list_memories_for_offer's first arg must be an attribute "
            f"access (self.session); got {ast.dump(first) if first else 'no args'}"
        )
        assert first.attr == "session", (
            f"list_memories_for_offer must be called with self.session "
            f"(not self.{first.attr}). This is the v1.6.0 regression that "
            "crashed every script generation as 'AttributeError: object "
            "has no attribute db'."
        )
    assert found >= 1, (
        "Expected at least one call to list_memories_for_offer in "
        "script_writer_service.py — was memory injection removed? "
        "If it moved elsewhere, update or delete this test."
    )


def test_no_self_db_anywhere_in_script_writer_service():
    """Hard guard: no ``self.db`` attribute access anywhere in the file.

    Codebase convention is ``self.session`` for the AsyncSession. A
    single ``self.db`` typo is exactly how the v1.6.0 regression slipped
    past review. Catch any new typo at test time, not at runtime."""
    import app.application.script_writer_service as svc_mod
    src = open(svc_mod.__file__).read()
    assert "self.db" not in src, (
        "Found self.db in script_writer_service.py — the convention is "
        "self.session. This is the v1.6.0 typo pattern that crashed "
        "production script generation."
    )
