#!/usr/bin/env python3
"""Cross-check the agent Skills against the live OpenLucid surface.

Skills hardcode tool names, CLI subcommands, and app_ids. When the surface
evolves (a tool gets renamed, a CLI command added, an app_id deprecated),
the Skills go stale silently — agents then call into nothing or the wrong
thing. This script catches that drift before commit.

Surfaces audited:
  - MCP tools          → ``app/mcp_server.py`` (functions decorated with
                         ``@mcp.tool()``)
  - CLI subcommands    → ``tools/openlucid`` (functions named ``cmd_*``)
  - App ids            → hardcoded list inferred from ``mcp_server.py``
                         + ``app/apps/`` registrations

Skills audited:
  - ``.claude/skills/openlucid-install/SKILL.md``
  - ``.claude/skills/openlucid-use/SKILL.md``
  - ``skills/openlucid/SKILL.md``  (legacy CLI-only, distributed via
                                    ``tools/install.sh``)
  - ``app/mcp_server.py`` ``instructions=`` string

Exits non-zero if any name in a Skill / instructions block doesn't resolve.
Run from repo root: ``python3 tools/check_skill_consistency.py``
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def real_mcp_tools() -> set[str]:
    """Functions decorated with ``@mcp.tool()`` in ``app/mcp_server.py``."""
    src = (ROOT / "app/mcp_server.py").read_text()
    tree = ast.parse(src)
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for d in node.decorator_list:
            if "mcp.tool" in (ast.unparse(d) if hasattr(ast, "unparse") else ""):
                tools.add(node.name)
                break
    return tools


def real_cli_subcommands() -> set[str]:
    """``cmd_*`` functions in ``tools/openlucid`` mapped to subcommand form."""
    src = (ROOT / "tools/openlucid").read_text()
    tree = ast.parse(src)
    cmds: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
            cmds.add(node.name[len("cmd_") :].replace("_", "-"))
    return cmds


def real_app_ids() -> set[str]:
    """App ids the runtime knows about. Mined from ``mcp_server.py`` enum
    branches and ``app/apps/registry.py`` registrations."""
    src = (ROOT / "app/mcp_server.py").read_text()
    ids: set[str] = set()
    # Look for ``app_id == "..."`` and ``("...", "...", "...")`` style tuples
    for m in re.finditer(r'app_id\s*==\s*"([a-z_]+)"', src):
        ids.add(m.group(1))
    for m in re.finditer(r'in\s*\("([a-z_]+)",\s*"([a-z_]+)"\)', src):
        ids.add(m.group(1))
        ids.add(m.group(2))
    # Pick up registered app_ids from app/apps/*.py if any define ``app_id="..."``
    for py in (ROOT / "app/apps").rglob("*.py"):
        try:
            t = py.read_text()
        except Exception:
            continue
        for m in re.finditer(r'\bapp_id\s*=\s*"([a-z_]+)"', t):
            ids.add(m.group(1))
    return ids


def cited_mcp_tools(text: str, real: set[str]) -> set[str]:
    """Return tokens in ``text`` that are presented as MCP tool calls.

    Conservative — bare backticked snake_case tokens routinely match
    parameter names (``merchant_id``), enum values (``flash_sale``), and
    schema fields (``source_type``) which we do NOT want to flag. We only
    accept two strong signals:

      1. Backtick + call form: `` `name(` `` (with or without args inside)
      2. Backtick + exact whitelist hit: `` `name` `` where ``name`` is in
         the real-tools set (canonical mention)

    Anything else is treated as prose / parameter / enum and ignored.
    """
    candidates: set[str] = set()
    # 1. Backtick-wrapped call form: ``name(`` is the unambiguous signal
    for m in re.finditer(r"`([a-z_][a-z0-9_]+)\s*\(", text):
        candidates.add(m.group(1))
    # 2. Backtick + exact match against real tool set
    for m in re.finditer(r"`([a-z_][a-z0-9_]+)`", text):
        if m.group(1) in real:
            candidates.add(m.group(1))
    return candidates


def cited_cli_subcommands(text: str) -> set[str]:
    """Tokens after ``openlucid `` in body prose / code blocks. Strips YAML
    frontmatter first so the skill's own ``name: openlucid\\ndescription: …``
    block doesn't get parsed as the subcommand ``description``."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5 :]
    return set(re.findall(r"openlucid\s+([a-z][a-z0-9-]+)", text))


def cited_app_ids(text: str) -> set[str]:
    """Tokens passed as ``app_id=...`` or in the app_id enum docs."""
    cited: set[str] = set()
    for m in re.finditer(r'app_id\s*=\s*"([a-z_]+)"', text):
        cited.add(m.group(1))
    for m in re.finditer(r'app_id="([a-z_]+)"', text):
        cited.add(m.group(1))
    # Also catch backtick-only mentions like `topic_studio`, `script_writer`
    for known in ("topic_studio", "script_writer", "content_studio", "kb_qa"):
        if f"`{known}`" in text:
            cited.add(known)
    return cited


def mcp_instructions_block() -> str:
    """Pull the ``instructions=(...)`` string body from ``mcp_server.py``."""
    src = (ROOT / "app/mcp_server.py").read_text()
    m = re.search(r"instructions=\(\s*\n((?:\s*\".+\n?)+?)\s*\),", src)
    return "\n".join(re.findall(r'"([^"]*)"', m.group(1))) if m else ""


def main() -> int:
    real_tools = real_mcp_tools()
    real_cli = real_cli_subcommands()
    real_apps = real_app_ids() | {
        # Known app_ids referenced in code branches even when not ``app_id=`` literal
        "topic_studio",
        "script_writer",
        "content_studio",
        "kb_qa",
    }

    print(f"MCP tools registered:    {len(real_tools)}")
    print(f"CLI subcommands:         {len(real_cli)}")
    print(f"App ids known:           {sorted(real_apps)}")
    print()

    candidate_paths = [
        ".claude/skills/openlucid-install/SKILL.md",
        ".claude/skills/openlucid-use/SKILL.md",
        "skills/openlucid/SKILL.md",
    ]
    sources: list[tuple[str, str]] = []
    for rel in candidate_paths:
        p = ROOT / rel
        if p.is_file():
            label = f"{rel} (legacy)" if rel == "skills/openlucid/SKILL.md" else rel
            sources.append((label, p.read_text()))
    sources.append(("app/mcp_server.py instructions=", mcp_instructions_block()))

    bad = 0
    for label, text in sources:
        tools_cited = cited_mcp_tools(text, real_tools)
        cli_cited = cited_cli_subcommands(text)
        apps_cited = cited_app_ids(text)

        bad_tools = {t for t in tools_cited if t not in real_tools}
        bad_cli = {c for c in cli_cited if c not in real_cli}
        bad_apps = {a for a in apps_cited if a not in real_apps}

        status = "ok " if not (bad_tools or bad_cli or bad_apps) else "BAD"
        print(f"[{status}] {label}")
        print(f"        tools: cited {len(tools_cited)}, unknown {sorted(bad_tools) or '—'}")
        print(f"        cli:   cited {len(cli_cited)}, unknown {sorted(bad_cli) or '—'}")
        print(f"        apps:  cited {len(apps_cited)}, unknown {sorted(bad_apps) or '—'}")
        if bad_tools or bad_cli or bad_apps:
            bad += 1

    if bad:
        print(f"\nFAIL — {bad} source(s) reference names the runtime doesn't recognize.")
        print("Fix: rename the citation in the Skill / instructions to a real one,")
        print("or add the missing tool / subcommand / app_id to the runtime first.")
        return 1
    print("\nOK — all Skill / instructions citations resolve to real runtime names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
