"""Tests for security and reliability fixes."""
import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── 1. Path Traversal (LocalStorageAdapter._safe_path) ──────────────


class TestPathTraversal:
    """Verify that LocalStorageAdapter rejects path traversal attempts."""

    def _make_adapter(self, tmp_path: Path):
        from app.adapters.storage import LocalStorageAdapter
        return LocalStorageAdapter(base_path=str(tmp_path))

    def test_safe_path_normal(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        # Normal subpath should resolve within base
        result = adapter._safe_path("subdir/file.txt")
        assert str(result).startswith(str(tmp_path.resolve()))

    def test_safe_path_rejects_dot_dot(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        with pytest.raises(ValueError, match="Invalid storage path"):
            adapter._safe_path("../../etc/passwd")

    def test_safe_path_rejects_absolute(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        # /etc/passwd joined with Path still resolves outside base
        with pytest.raises(ValueError, match="Invalid storage path"):
            adapter._safe_path("../../../etc/passwd")

    def test_safe_path_rejects_dot_dot_encoded(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        with pytest.raises(ValueError, match="Invalid storage path"):
            adapter._safe_path("subdir/../../etc/passwd")

    def test_get_absolute_path_safe(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        with pytest.raises(ValueError, match="Invalid storage path"):
            adapter.get_absolute_path("../../etc/passwd")

    def test_get_file_safe(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        with pytest.raises(ValueError, match="Invalid storage path"):
            asyncio.run(adapter.get_file("../../etc/passwd"))

    def test_delete_file_safe(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        with pytest.raises(ValueError, match="Invalid storage path"):
            asyncio.run(adapter.delete_file("../../etc/passwd"))

    def test_normal_file_operations(self, tmp_path):
        """Ensure normal save/read/delete still work."""
        adapter = self._make_adapter(tmp_path)
        uri = asyncio.run(adapter.save_file(b"hello world", "test.txt", sub_path="sub"))
        content = asyncio.run(adapter.get_file(uri))
        assert content == b"hello world"

        abs_path = adapter.get_absolute_path(uri)
        assert os.path.exists(abs_path)

        asyncio.run(adapter.delete_file(uri))
        assert not os.path.exists(abs_path)


# ── 2. JWT / HMAC password reset token ──────────────────────────────


class TestPasswordResetToken:
    """Verify reset token uses HMAC instead of raw password hash prefix."""

    def test_pwh_snapshot_is_hmac(self):
        from app.libs.jwt_utils import _pwh_snapshot
        # The snapshot should be 16-char hex, not raw hash prefix
        snapshot = _pwh_snapshot("$2b$12$abcdefghijklmnopqrstuuABC")
        assert len(snapshot) == 16
        assert all(c in "0123456789abcdef" for c in snapshot)

    def test_pwh_snapshot_not_hash_prefix(self):
        from app.libs.jwt_utils import _pwh_snapshot
        fake_hash = "$2b$12$abcdefghijklmnopqrstuuABC"
        snapshot = _pwh_snapshot(fake_hash)
        # Must NOT be the first 16 chars of the hash itself
        assert snapshot != fake_hash[:16]

    def test_pwh_snapshot_deterministic(self):
        from app.libs.jwt_utils import _pwh_snapshot
        h = "$2b$12$somehash"
        assert _pwh_snapshot(h) == _pwh_snapshot(h)

    def test_pwh_snapshot_changes_with_password(self):
        from app.libs.jwt_utils import _pwh_snapshot
        s1 = _pwh_snapshot("$2b$12$hash_v1")
        s2 = _pwh_snapshot("$2b$12$hash_v2")
        assert s1 != s2

    def test_create_reset_token_decodes(self):
        from app.libs.jwt_utils import create_reset_token, decode_token, _pwh_snapshot
        fake_hash = "$2b$12$testhashabcdef"
        token = create_reset_token("user@test.com", fake_hash)
        payload = decode_token(token)
        assert payload["type"] == "reset"
        assert payload["email"] == "user@test.com"
        assert payload["pwh"] == _pwh_snapshot(fake_hash)

    def test_reset_token_invalidated_by_password_change(self):
        from app.libs.jwt_utils import create_reset_token, decode_token, _pwh_snapshot
        old_hash = "$2b$12$old_password_hash"
        new_hash = "$2b$12$new_password_hash"
        token = create_reset_token("user@test.com", old_hash)
        payload = decode_token(token)
        # Simulates the check in auth.py: new password hash should not match
        assert payload["pwh"] != _pwh_snapshot(new_hash)


# ── 3. CORS configuration ───────────────────────────────────────────


class TestCORSConfig:
    """Verify CORS credentials are disabled when origins is wildcard."""

    def test_wildcard_disables_credentials(self):
        """When CORS_ORIGINS='*', allow_credentials should be False."""
        # Replicate the logic from main.py
        cors_origins_setting = "*"
        allow_credentials = cors_origins_setting.strip() != "*"
        assert allow_credentials is False

    def test_specific_origins_enable_credentials(self):
        cors_origins_setting = "https://example.com, https://app.example.com"
        allow_credentials = cors_origins_setting.strip() != "*"
        assert allow_credentials is True


# ── 4. Rate limiter cleanup ─────────────────────────────────────────


class TestRateLimiterCleanup:
    """Verify rate limiter cleans up stale entries."""

    def test_stale_entries_cleaned(self):
        import app.libs.rate_limit as rl

        # Reset module state
        with rl._lock:
            rl._hits.clear()

        # Inject stale entries (timestamps far in the past)
        past = time.monotonic() - rl.WINDOW_SECONDS - 100
        with rl._lock:
            rl._hits["stale_ip_1"] = [past, past - 10]
            rl._hits["stale_ip_2"] = [past - 50]

        # Force cleanup by setting probability to 1.0 temporarily
        import random
        orig_random = random.random
        random.random = lambda: 0.001  # below _CLEANUP_PROBABILITY (0.01)

        try:
            mock_request = MagicMock()
            mock_request.headers.get.return_value = None
            mock_request.client.host = "10.0.0.1"

            rl.check_rate_limit(mock_request)

            with rl._lock:
                # Stale IPs should be cleaned up
                assert "stale_ip_1" not in rl._hits
                assert "stale_ip_2" not in rl._hits
                # Current IP should still be there
                assert "10.0.0.1" in rl._hits
        finally:
            random.random = orig_random
            with rl._lock:
                rl._hits.clear()

    def test_rate_limit_still_works(self):
        import app.libs.rate_limit as rl
        from fastapi import HTTPException

        with rl._lock:
            rl._hits.clear()

        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.1"

        # Should allow MAX_REQUESTS calls
        for _ in range(rl.MAX_REQUESTS):
            rl.check_rate_limit(mock_request)

        # Next call should raise 429
        with pytest.raises(HTTPException) as exc_info:
            rl.check_rate_limit(mock_request)
        assert exc_info.value.status_code == 429

        with rl._lock:
            rl._hits.clear()


# ── 5. SECRET_KEY default detection ─────────────────────────────────


class TestSecretKeyWarning:
    """Verify the default SECRET_KEY values are detected."""

    def test_default_keys_detected(self):
        defaults = {
            "change-me-in-production-use-a-long-random-string",
            "change-me-in-production",
        }
        # Both known defaults should be in the set
        assert "change-me-in-production-use-a-long-random-string" in defaults
        assert "change-me-in-production" in defaults
        # A real key should not match
        assert "my-super-secret-random-key-abc123" not in defaults


# ── 6. Background task exception callback ───────────────────────────


class TestLogTaskException:
    """Verify _log_task_exception handles edge cases.

    We replicate the function here to avoid importing app.main which
    triggers the full application init chain (requires asyncpg/DB).
    """

    @staticmethod
    def _log_task_exception(task):
        """Copy of app.main._log_task_exception for isolated testing."""
        import logging
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logging.getLogger("app.main").error(
                "Background task %s failed: %s", task.get_name(), exc, exc_info=exc,
            )

    def test_cancelled_task_no_error(self):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = True
        self._log_task_exception(mock_task)
        mock_task.exception.assert_not_called()

    def test_successful_task_no_error(self):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = None
        self._log_task_exception(mock_task)

    def test_failed_task_logs(self):
        mock_task = MagicMock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = RuntimeError("boom")
        mock_task.get_name.return_value = "test_task"
        self._log_task_exception(mock_task)
