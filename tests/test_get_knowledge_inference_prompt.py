"""v1.2.1 — guard the new ``get_knowledge_inference_prompt`` MCP tool.

The point of v1.2.1 is to keep OpenLucid's marketing-knowledge
discipline (the 167-line system prompt at
``app/adapters/ai.py:61-227``) authoritative while letting the
EXTERNAL agent be the brain — i.e. run that prompt through its own
LLM rather than be forced through OpenLucid's configured server
model.

Tests pin five contracts:

1. The tool exists, is async, takes the documented signature.
2. The returned payload carries every field an external agent needs
   to (a) run the prompt and (b) write rows back consistently —
   system_prompt, user_message, output_schema, write_back_instructions.
3. The system_prompt the tool returns is BYTE-IDENTICAL to what the
   built-in adapter would feed its own LLM. Drift here re-fragments
   the prompt across two paths — exactly the v1.1.3-class bug we
   spent v1.1.6/v1.1.9/v1.2.0 closing for other dual-storage cases.
4. The write_back_instructions name the ``external-agent:`` prefix so
   audit logs can distinguish external-agent runs from server-side
   ``auto-infer:`` runs.
5. The server instructions and create_offer docstring both surface
   the external-agent path as the preferred MCP-first usage. Without
   that, the tool exists but the rest of the surface still steers
   agents toward the convenience path (v1.2.0 regression).
"""
from __future__ import annotations

import inspect


# ── 1. Tool surface ───────────────────────────────────────────────────


class TestGetKnowledgeInferencePromptSurface:
    def test_tool_exists_and_is_async(self):
        from app.mcp_server import get_knowledge_inference_prompt
        assert inspect.iscoroutinefunction(get_knowledge_inference_prompt)

    def test_signature(self):
        from app.mcp_server import get_knowledge_inference_prompt
        params = inspect.signature(get_knowledge_inference_prompt).parameters
        assert "offer_id" in params and params["offer_id"].default is inspect.Parameter.empty
        assert "language" in params and params["language"].default is None
        assert "user_hint" in params and params["user_hint"].default is None


# ── 2. Returned payload completeness ──────────────────────────────────


class TestPayloadShape:
    """The body must construct every field an external agent
    needs in one round-trip — re-querying offer / list_knowledge
    just to use this tool would defeat the convenience."""

    def _src(self) -> str:
        from app.mcp_server import get_knowledge_inference_prompt
        return inspect.getsource(get_knowledge_inference_prompt)

    def test_payload_keys_present(self):
        src = self._src()
        for key in (
            "system_prompt", "user_message", "output_schema",
            "write_back_instructions", "language", "offer",
            "recommended_temperature",
        ):
            assert f'"{key}"' in src, (
                f"get_knowledge_inference_prompt payload missing ``{key}`` "
                "— external agent would need a second tool call to compose "
                "what it needs"
            )

    def test_output_schema_lists_all_seven_kb_types(self):
        """Schema hint must enumerate the same 7 types the built-in
        adapter validates against (see ``OpenAICompatibleAdapter.
        infer_knowledge`` line 1172). Without this, an agent's LLM
        might emit only 3-4 types and the agent won't know that's
        below the prompt's coverage standard."""
        src = self._src()
        for kt in ("selling_point", "audience", "scenario",
                   "pain_point", "faq", "objection", "proof"):
            assert f'"{kt}":' in src, (
                f"output_schema missing knowledge_type ``{kt}``"
            )

    def test_write_back_instructions_name_external_agent_prefix(self):
        """The audit-trail prefix is the load-bearing distinction
        between server-internal ``auto-infer:`` and external-agent-
        driven inferences. The instruction string must literally
        contain ``external-agent:`` so agents copy it verbatim into
        their add_knowledge_item calls."""
        src = self._src()
        assert "external-agent:" in src

    def test_write_back_instructions_mention_unique_constraint(self):
        """Re-running the prompt-driven flow must be safe (idempotent
        in title-space). Tell the agent that, so it doesn't add
        de-dup logic of its own that the DB already enforces."""
        src = self._src()
        text = src.lower()
        # Either UNIQUE constraint or the word "duplicate" is named
        assert "unique" in text or "duplicate" in text or "in place" in text


# ── 3. System prompt parity with built-in adapter ─────────────────────


class TestSystemPromptParity:
    """If the prompt the external agent runs DIFFERS from the prompt
    the built-in model runs, agents on the two paths produce
    diverging KB shapes — same family of bug v1.1.6 / v1.1.9 / v1.2.0
    fixed for other dual-storage cases. Anchor on the source-level
    composition so we catch the drift the moment someone forks the
    prompt for one path."""

    def test_uses_same_system_prompt_builder(self):
        from app.mcp_server import get_knowledge_inference_prompt
        src = inspect.getsource(get_knowledge_inference_prompt)
        assert "_build_infer_knowledge_system_prompt" in src, (
            "get_knowledge_inference_prompt must compose the system "
            "prompt via the same _build_infer_knowledge_system_prompt "
            "helper the built-in adapter uses (app/adapters/ai.py:"
            "1149/1190). Forking the prompt for the external-agent "
            "path re-introduces a v1.2.0-class consistency gap."
        )

    def test_uses_same_user_message_helpers(self):
        from app.mcp_server import get_knowledge_inference_prompt
        src = inspect.getsource(get_knowledge_inference_prompt)
        assert "format_offer_summary" in src
        assert "format_existing_knowledge" in src

    def test_user_hint_appended_when_supplied(self):
        from app.mcp_server import get_knowledge_inference_prompt
        src = inspect.getsource(get_knowledge_inference_prompt)
        # Same pattern as the built-in adapter at app/adapters/ai.py:1152-1153
        assert "user_hint" in src
        assert "Additional notes from user" in src


# ── 4. Server-instructions surface preference for external-agent path ─


class TestServerInstructionsPreferExternalAgent:
    """v1.2.1 isn't done if the tool exists but the server still
    steers agents elsewhere. The instructions returned on MCP
    initialize must (a) name the new tool, (b) call the external-
    agent path PREFERRED, (c) call the server-side path
    CONVENIENCE — not the other way around."""

    def test_instructions_name_get_knowledge_inference_prompt(self):
        from app.mcp_server import mcp
        instr = (mcp.instructions or "")
        assert "get_knowledge_inference_prompt" in instr, (
            "Server instructions must mention the v1.2.1 tool by name "
            "so a freshly-connected agent sees it without inspecting "
            "the full tool catalogue"
        )

    def test_instructions_label_external_agent_as_preferred(self):
        from app.mcp_server import mcp
        instr = (mcp.instructions or "").lower()
        # Either explicit "preferred" tag, or the structural ordering
        # signal: external-agent path described BEFORE the
        # convenience/built-in path.
        assert "preferred" in instr
        # External-agent path must be (a) before (b) — find both
        # occurrences and check order.
        ext_idx = instr.find("get_knowledge_inference_prompt")
        conv_idx = instr.find("create_offer(infer_knowledge=true)")
        assert ext_idx > 0 and conv_idx > 0
        assert ext_idx < conv_idx, (
            "external-agent path must be described BEFORE the "
            "convenience path so agents reading top-to-bottom see the "
            "MCP-first design first"
        )


# ── 5. create_offer docstring updated to match v1.2.1 framing ─────────


class TestCreateOfferDocstringV121Framing:
    def test_docstring_describes_three_paths(self):
        from app.mcp_server import create_offer
        doc = (create_offer.__doc__ or "").lower()
        # Mentions the external-agent route by name
        assert "get_knowledge_inference_prompt" in doc, (
            "create_offer docstring must point at the v1.2.1 external-"
            "agent route or agents won't discover it"
        )
        # Marks one path as preferred / the other as convenience
        assert "preferred" in doc
        assert "convenience" in doc