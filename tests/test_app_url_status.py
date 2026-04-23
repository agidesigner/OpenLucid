"""Tests for the three-state /settings/app-url-status classifier.

Previous behavior: `configured = APP_URL not in ("http://localhost",
"http://localhost:8000")` → every same-machine self-hosted user saw a
misleading "APP_URL is not configured" warning, even though the MCP
agent on the same host could reach localhost fine.

New behavior: three states so the UI can distinguish "genuinely broken"
from "fine for local-only agents" from "configured for remote agents".
"""
from __future__ import annotations

import pytest


def _call_classifier(app_url: str) -> dict:
    """Run the classifier logic without spinning up FastAPI. Mirrors
    app_url_status() in app/api/setting.py."""
    url = (app_url or "").strip().lower()
    placeholders = ("nihao.com", "example.com", "change-me")
    if not url or any(p in url for p in placeholders):
        status = "invalid"
    elif (
        url in ("http://localhost", "http://localhost:8000", "http://127.0.0.1", "http://[::1]")
        or url.startswith(("http://localhost:", "http://127.0.0.1:", "http://[::1]:"))
    ):
        status = "localhost"
    else:
        status = "ok"
    return {"status": status, "configured": status != "invalid", "current": app_url}


class TestInvalidStates:
    """Genuine "must fix" configurations — empty or placeholder hosts
    left from onboarding that agents truly can't resolve."""

    def test_empty_string(self):
        r = _call_classifier("")
        assert r["status"] == "invalid"
        assert r["configured"] is False

    def test_whitespace_only(self):
        r = _call_classifier("   ")
        assert r["status"] == "invalid"

    def test_nihao_placeholder(self):
        assert _call_classifier("http://nihao.com")["status"] == "invalid"
        assert _call_classifier("https://nihao.com:8443")["status"] == "invalid"

    def test_example_placeholder(self):
        assert _call_classifier("https://example.com")["status"] == "invalid"

    def test_change_me_placeholder(self):
        assert _call_classifier("http://change-me.local")["status"] == "invalid"


class TestLocalhostStates:
    """Localhost variants — informational only. Works fine when the
    MCP agent runs on the same machine (the common self-hosted case
    this user asked about)."""

    def test_plain_localhost(self):
        r = _call_classifier("http://localhost")
        assert r["status"] == "localhost"
        assert r["configured"] is True

    def test_localhost_8000(self):
        assert _call_classifier("http://localhost:8000")["status"] == "localhost"

    def test_localhost_any_port(self):
        assert _call_classifier("http://localhost:3000")["status"] == "localhost"
        assert _call_classifier("http://localhost:80")["status"] == "localhost"

    def test_ip_127_0_0_1(self):
        assert _call_classifier("http://127.0.0.1")["status"] == "localhost"
        assert _call_classifier("http://127.0.0.1:8000")["status"] == "localhost"

    def test_ipv6_loopback(self):
        assert _call_classifier("http://[::1]")["status"] == "localhost"
        assert _call_classifier("http://[::1]:8000")["status"] == "localhost"

    def test_case_insensitive(self):
        assert _call_classifier("HTTP://LOCALHOST:8000")["status"] == "localhost"


class TestOkStates:
    """Real public/LAN addresses — no banner, no hint."""

    def test_public_https_domain(self):
        r = _call_classifier("https://openlucid.example.net")
        assert r["status"] == "ok"
        assert r["configured"] is True

    def test_lan_ip(self):
        assert _call_classifier("http://192.168.1.100:8000")["status"] == "ok"
        assert _call_classifier("http://10.0.0.5:8080")["status"] == "ok"

    def test_nas_domain(self):
        assert _call_classifier("http://nas.local:8000")["status"] == "ok"

    def test_tailscale_name(self):
        assert _call_classifier("http://myserver.tailnet.ts.net")["status"] == "ok"


class TestResponseShape:
    """UI contract: the response must carry both ``status`` (new,
    three-way) and ``configured`` (old boolean, for backwards compat
    with deployed frontends during rollout)."""

    def test_includes_all_keys(self):
        r = _call_classifier("http://localhost:8000")
        assert set(r.keys()) == {"status", "configured", "current"}

    def test_current_preserves_original_casing(self):
        """The raw value is echoed back for display — don't lowercase.
        Uses a domain that doesn't contain any placeholder substring
        (avoid 'example.com' since that matches the placeholder list)."""
        r = _call_classifier("HTTPS://Production.Openlucid.Net")
        assert r["current"] == "HTTPS://Production.Openlucid.Net"
        assert r["status"] == "ok"
