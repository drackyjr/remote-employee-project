"""
NEF Agent v2 — Test Suite
Tests all components without requiring a live backend or privileged access.

Run from the agent/ directory:
    cd R:\MACHINE\MACHINE\agent
    python -m pytest tests/ -v

Or run individual sections:
    python tests/test_agent.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Make sure agent/ is importable ────────────────────────────────────────────
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Categorizer
# ══════════════════════════════════════════════════════════════════════════════

class TestCategorizer(unittest.TestCase):

    def setUp(self):
        from collectors.categorizer import categorize, categorize_domains
        self.categorize         = categorize
        self.categorize_domains = categorize_domains

    def test_known_productivity_domain(self):
        cat, score = self.categorize("github.com")
        self.assertEqual(cat, "productivity")
        self.assertGreater(score, 70)

    def test_known_social_media(self):
        cat, score = self.categorize("facebook.com")
        self.assertEqual(cat, "social_media")
        self.assertLess(score, 40)

    def test_known_entertainment(self):
        cat, score = self.categorize("youtube.com")
        self.assertEqual(cat, "entertainment")

    def test_known_adult_flag(self):
        cat, score = self.categorize("pornhub.com")
        self.assertEqual(cat, "adult")
        self.assertEqual(score, 0)

    def test_unknown_domain(self):
        cat, score = self.categorize("some-totally-unknown-site-xyz.com")
        self.assertEqual(cat, "unknown")
        self.assertEqual(score, 50)

    def test_www_prefix_stripped(self):
        cat1, _ = self.categorize("github.com")
        cat2, _ = self.categorize("www.github.com")
        self.assertEqual(cat1, cat2)

    def test_case_insensitive(self):
        cat1, _ = self.categorize("GITHUB.COM")
        cat2, _ = self.categorize("github.com")
        self.assertEqual(cat1, cat2)

    def test_batch_categorize(self):
        domains = ["github.com", "facebook.com", "unknown.xyz"]
        results = self.categorize_domains(domains)
        self.assertEqual(len(results), 3)
        self.assertTrue(all("category" in r for r in results))
        self.assertTrue(all("productivity_score" in r for r in results))

    def test_empty_domain_skipped(self):
        results = self.categorize_domains(["", None, "github.com"])
        # Empty/None should be skipped
        self.assertEqual(len(results), 1)

    def test_linkedin_is_higher_score(self):
        # LinkedIn is professional — should score higher than Facebook
        _, li_score = self.categorize("linkedin.com")
        _, fb_score = self.categorize("facebook.com")
        self.assertGreater(li_score, fb_score)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Input Metrics (Privacy Verification)
# ══════════════════════════════════════════════════════════════════════════════

class TestInputMetrics(unittest.TestCase):

    def test_collect_returns_required_fields(self):
        from collectors.input_metrics import collect
        result = collect()
        self.assertIn("type", result)
        self.assertEqual(result["type"], "input_metrics")
        self.assertIn("keystrokes_per_min", result)
        self.assertIn("mouse_clicks_per_min", result)
        self.assertIn("mouse_moves_per_min", result)
        self.assertIn("is_active", result)
        self.assertIn("collected_at", result)

    def test_no_key_content_in_output(self):
        """CRITICAL: Verify that no key values or content fields appear."""
        from collectors.input_metrics import collect
        result = collect()
        forbidden = {"key_content", "keys_pressed", "text", "clipboard",
                     "keystrokes_content", "characters", "password"}
        for field in forbidden:
            self.assertNotIn(field, result,
                msg=f"PRIVACY VIOLATION: field '{field}' found in input_metrics output")

    def test_headless_returns_sentinel(self):
        """Without a display, values should be -1 or False — not an error."""
        from collectors import input_metrics
        # Simulate headless — patch _listeners_available to False
        original = input_metrics._listeners_available
        input_metrics._listeners_available = False
        try:
            result = input_metrics.collect()
            self.assertEqual(result["keystrokes_per_min"], -1)
            self.assertEqual(result["mouse_clicks_per_min"], -1)
            self.assertFalse(result["is_active"])
            self.assertFalse(result["available"])
        finally:
            input_metrics._listeners_available = original

    def test_collect_does_not_raise(self):
        """collect() must never raise regardless of environment."""
        from collectors.input_metrics import collect
        try:
            collect()
        except Exception as e:
            self.fail(f"collect() raised unexpectedly: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Window Session Tracker
# ══════════════════════════════════════════════════════════════════════════════

class TestWindowSessionTracker(unittest.TestCase):

    def setUp(self):
        """Reset module-level session state before each test."""
        import collectors.window as w
        import importlib
        importlib.reload(w)
        self.w = w

    def test_first_collect_starts_session(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            result = self.w.collect()
        self.assertIn(result["type"], ("app_session_active", "app_session_end"))
        self.assertEqual(result["application"], "chrome.exe")

    def test_same_app_gives_active_event(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            self.w.collect()  # initialise
            time.sleep(0.1)
            result = self.w.collect()
        self.assertEqual(result["type"], "app_session_active")
        self.assertGreaterEqual(result["duration_sec"], 0)

    def test_app_change_emits_end_event(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            self.w.collect()  # start chrome session
        with patch.object(self.w, "_get_foreground", return_value=("code.exe", "VSCode")):
            result = self.w.collect()  # switch to VSCode
        self.assertEqual(result["type"], "app_session_end")
        self.assertEqual(result["application"], "chrome.exe")
        self.assertIn("duration_sec", result)
        self.assertIn("started_at", result)
        self.assertIn("ended_at", result)

    def test_get_completed_sessions_drains(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            self.w.collect()
        with patch.object(self.w, "_get_foreground", return_value=("code.exe", "VSCode")):
            self.w.collect()
        sessions = self.w.get_completed_sessions()
        self.assertGreater(len(sessions), 0)
        # Calling again should return empty (drained)
        sessions2 = self.w.get_completed_sessions()
        self.assertEqual(len(sessions2), 0)

    def test_flush_current_session_on_shutdown(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            self.w.collect()
        result = self.w.flush_current_session()
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "app_session_end")
        self.assertEqual(result["reason"], "agent_shutdown")

    def test_duration_increases_over_time(self):
        with patch.object(self.w, "_get_foreground", return_value=("chrome.exe", "GitHub")):
            self.w.collect()
            time.sleep(0.2)
            r1 = self.w.collect()
            time.sleep(0.2)
            r2 = self.w.collect()
        self.assertGreaterEqual(r2["duration_sec"], r1["duration_sec"])


# ══════════════════════════════════════════════════════════════════════════════
# 4. Offline Buffer & Uploader
# ══════════════════════════════════════════════════════════════════════════════

class TestOfflineBuffer(unittest.TestCase):

    def setUp(self):
        # Use a temp DB for each test
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db_path = Path(self._tmp.name)

    def tearDown(self):
        try:
            self._db_path.unlink()
        except Exception:
            pass

    def _make_uploader(self, server_url="http://invalid-server-99999"):
        from uploader import Uploader
        return Uploader(
            server_url=server_url,
            agent_id="test-agent-001",
            api_key="test-key-abc",
            buffer_db=self._db_path,
        )

    def test_buffer_stores_events(self):
        u = self._make_uploader()
        u.buffer({"type": "test", "data": "hello"})
        u.buffer({"type": "test", "data": "world"})
        self.assertEqual(u.pending_count(), 2)

    def test_flush_fails_gracefully_on_bad_server(self):
        """flush() must return False — not raise — when server is unreachable."""
        u = self._make_uploader()
        u.buffer({"type": "test", "data": "offline"})
        result = u.flush()
        self.assertFalse(result)
        # Events should still be in buffer
        self.assertEqual(u.pending_count(), 1)

    def test_events_survive_after_failed_flush(self):
        u = self._make_uploader()
        for i in range(5):
            u.buffer({"type": "test", "i": i})
        u.flush()  # will fail — bad server
        # All 5 should still be buffered
        self.assertEqual(u.pending_count(), 5)

    def test_successful_flush_clears_buffer(self):
        """Mock a successful HTTP response; verify buffer clears."""
        u = self._make_uploader()
        u.buffer({"type": "test", "data": "upload-me"})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(u.session, "post", return_value=mock_resp):
            result = u.flush()
        self.assertTrue(result)
        self.assertEqual(u.pending_count(), 0)

    def test_circuit_breaker_opens_after_threshold(self):
        u = self._make_uploader()
        u.buffer({"type": "test", "data": "x"})
        # Simulate threshold failures
        u._consecutive_failures = u._CIRCUIT_FAIL_THRESHOLD
        u._circuit_open = True
        u._circuit_open_until = time.time() + 300

        result = u.flush()   # circuit open — should skip
        self.assertFalse(result)
        self.assertTrue(u._circuit_open)

    def test_buffer_cap_evicts_oldest(self):
        u = self._make_uploader()
        u.max_buffer = 10
        for i in range(15):  # exceed cap
            u.buffer({"type": "test", "i": i})
        count = u.pending_count()
        self.assertLessEqual(count, 10)

    def test_status_dict_structure(self):
        u = self._make_uploader()
        status = u.status()
        self.assertIn("pending", status)
        self.assertIn("total_uploaded", status)
        self.assertIn("circuit_open", status)
        self.assertIn("consecutive_failures", status)

    def test_wal_mode_enabled(self):
        """Verify SQLite WAL mode is set on the buffer DB."""
        import sqlite3
        u = self._make_uploader()
        conn = sqlite3.connect(str(self._db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Auth Module (no live server needed)
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthModule(unittest.TestCase):

    def test_load_cert_returns_none_when_missing(self):
        from core.auth import load_cert
        result = load_cert("/nonexistent/path/nef.crt", "/nonexistent/path/nef.key")
        self.assertIsNone(result)

    def test_load_cert_returns_tuple_when_present(self):
        from core.auth import load_cert
        with tempfile.NamedTemporaryFile(suffix=".crt", delete=False) as c, \
             tempfile.NamedTemporaryFile(suffix=".key", delete=False) as k:
            c.write(b"FAKE CERT")
            k.write(b"FAKE KEY")
            c_path, k_path = c.name, k.name
        try:
            result = load_cert(c_path, k_path)
            self.assertIsNotNone(result)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)
        finally:
            os.unlink(c_path)
            os.unlink(k_path)

    def test_jwt_expiry_parse(self):
        from core.auth import _parse_jwt_expiry
        import base64, json, time
        # Build a fake JWT (unsigned — just for payload decode test)
        payload = {"exp": time.time() + 3600, "sub": "test"}
        b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_token = f"header.{b64}.sig"
        exp = _parse_jwt_expiry(fake_token)
        self.assertIsNotNone(exp)
        self.assertGreater(exp, time.time())

    def test_token_validity_check(self):
        from core.auth import _token_is_valid
        self.assertFalse(_token_is_valid(None))
        self.assertFalse(_token_is_valid(""))
        self.assertFalse(_token_is_valid("invalid.token.here"))

    def test_fetch_jwt_graceful_failure(self):
        """fetch_jwt() must return None — not raise — when server is unreachable."""
        from core.auth import fetch_jwt
        import requests
        session = requests.Session()
        result = fetch_jwt(session, "http://invalid-server-99999", "agent-id", "api-key")
        self.assertIsNone(result)

    def test_sign_challenge_hmac(self):
        """Verify HMAC signature is deterministic and correct."""
        from core.auth import _sign_challenge
        sig1 = _sign_challenge("agent-001", "nonce-abc", "secret-key")
        sig2 = _sign_challenge("agent-001", "nonce-abc", "secret-key")
        self.assertEqual(sig1, sig2)  # deterministic
        sig3 = _sign_challenge("agent-001", "nonce-xyz", "secret-key")
        self.assertNotEqual(sig1, sig3)  # different nonce → different sig


# ══════════════════════════════════════════════════════════════════════════════
# 6. Security Module
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityModule(unittest.TestCase):

    def test_check_process_integrity_returns_bool(self):
        from core.security import check_process_integrity
        result = check_process_integrity()
        self.assertIsInstance(result, bool)

    def test_secure_wipe_removes_file(self):
        from core.security import secure_wipe
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"sensitive data " * 100)
            path = f.name
        result = secure_wipe(path)
        self.assertTrue(result)
        self.assertFalse(Path(path).exists())

    def test_secure_wipe_nonexistent_is_ok(self):
        from core.security import secure_wipe
        result = secure_wipe("/totally/nonexistent/file.tmp")
        self.assertTrue(result)

    def test_run_startup_checks_returns_bool(self):
        from core.security import run_startup_checks
        cfg = {"cert_path": "", "ca_cert_path": ""}
        result = run_startup_checks(cfg)
        self.assertIsInstance(result, bool)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Config
# ══════════════════════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):

    def test_default_config_has_required_keys(self):
        from config import get_default_config
        cfg = get_default_config()
        required = [
            "server_url", "agent_id", "api_key",
            "cert_path", "key_path", "ca_cert_path",
            "upload_interval", "heartbeat_interval",
            "dns_capture_method", "enable_tray_icon",
            "max_buffer_events", "batch_size",
        ]
        for key in required:
            self.assertIn(key, cfg, f"Missing required config key: {key}")

    def test_upload_interval_is_10s(self):
        from config import get_default_config
        cfg = get_default_config()
        self.assertEqual(cfg["upload_interval"], 10)

    def test_dns_method_defaults_to_cache(self):
        from config import get_default_config
        cfg = get_default_config()
        self.assertEqual(cfg["dns_capture_method"], "cache")

    def test_cert_available_false_when_missing(self):
        from config import cert_available, get_default_config
        cfg = get_default_config()
        self.assertFalse(cert_available(cfg))

    def test_load_config_does_not_raise(self):
        from config import load_config
        try:
            cfg = load_config()
            self.assertIsInstance(cfg, dict)
        except Exception as e:
            self.fail(f"load_config() raised: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 8. Website DNS Collector
# ══════════════════════════════════════════════════════════════════════════════

class TestWebsitesCollector(unittest.TestCase):

    def test_collect_returns_dns_activity_type(self):
        from collectors.websites import collect
        result = collect("cache")
        self.assertEqual(result["type"], "dns_activity")

    def test_collect_has_required_fields(self):
        from collectors.websites import collect
        result = collect("cache")
        self.assertIn("domains", result)
        self.assertIn("capture_method", result)
        self.assertIn("collected_at", result)
        self.assertIn("domain_count", result)

    def test_no_full_urls_in_output(self):
        """PRIVACY: Ensure no 'url' field appears anywhere in the output."""
        from collectors.websites import collect
        result = collect("cache")
        result_str = json.dumps(result)
        # A full URL would contain "://"; domains won't
        for domain_entry in result.get("domains", []):
            self.assertNotIn("://", domain_entry.get("domain", ""),
                msg="Full URL found in domain field — only domain names allowed")

    def test_domains_have_category(self):
        """If any domains are returned, they must all have categories."""
        from collectors.websites import collect
        result = collect("cache")
        for entry in result.get("domains", []):
            self.assertIn("category", entry)
            self.assertIn(entry["category"],
                {"productivity", "social_media", "entertainment",
                 "news", "shopping", "adult", "unknown"})

    def test_local_domains_filtered(self):
        """mDNS / localhost / .local domains should not appear."""
        from collectors.websites import collect
        result = collect("cache")
        for entry in result.get("domains", []):
            domain = entry["domain"]
            self.assertNotIn("localhost",   domain)
            self.assertFalse(domain.endswith(".local"),
                msg=f"mDNS domain leaked into output: {domain}")
            self.assertFalse(domain.endswith(".arpa"),
                msg=f"Reverse DNS domain leaked into output: {domain}")

    def test_browser_method_does_not_raise(self):
        from collectors.websites import collect
        try:
            collect("browser")
        except Exception as e:
            self.fail(f"collect('browser') raised: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 9. Transparency / Status Log
# ══════════════════════════════════════════════════════════════════════════════

class TestTransparency(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._log = Path(self._tmp_dir) / "status.log"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_write_status_creates_log(self):
        from transparency.tray import write_status
        import transparency.tray as tray_mod
        original = tray_mod._STATUS_LOG
        tray_mod._STATUS_LOG = self._log
        try:
            write_status("STATUS=connected server=http://localhost:8000")
            self.assertTrue(self._log.exists())
            content = self._log.read_text()
            self.assertIn("STATUS=connected", content)
        finally:
            tray_mod._STATUS_LOG = original

    def test_status_log_rotation(self):
        """Log should not grow beyond _MAX_LOG_LINES."""
        from transparency import tray as tray_mod
        original_log = tray_mod._STATUS_LOG
        original_max = tray_mod._MAX_LOG_LINES
        tray_mod._STATUS_LOG  = self._log
        tray_mod._MAX_LOG_LINES = 10
        try:
            for i in range(20):  # write twice the limit
                tray_mod.write_status(f"line {i}")
            lines = self._log.read_text().splitlines()
            self.assertLessEqual(len(lines), 10)
        finally:
            tray_mod._STATUS_LOG  = original_log
            tray_mod._MAX_LOG_LINES = original_max

    def test_update_status_log_writes_three_lines(self):
        from transparency import tray as tray_mod
        original_log = tray_mod._STATUS_LOG
        tray_mod._STATUS_LOG = self._log
        try:
            status = {
                "last_upload_ok": time.time(),
                "circuit_open": False,
                "consecutive_failures": 0,
                "total_uploaded": 100,
                "pending": 5,
            }
            tray_mod.update_status_log(status, "http://localhost:8000")
            lines = self._log.read_text().strip().splitlines()
            self.assertGreaterEqual(len(lines), 3)
        finally:
            tray_mod._STATUS_LOG = original_log


# ══════════════════════════════════════════════════════════════════════════════
# 10. KBT Config
# ══════════════════════════════════════════════════════════════════════════════

class TestKBTConfig(unittest.TestCase):

    def test_kbt_config_dir_uses_dot_kbt(self):
        """Config dir must be ~/.kbt, not ~/.nef."""
        import kbt_config
        self.assertTrue(str(kbt_config.CONFIG_DIR).endswith(".kbt"),
            msg=f"Expected CONFIG_DIR to end with .kbt, got: {kbt_config.CONFIG_DIR}")

    def test_kbt_default_config_has_employee_id(self):
        """Default config must include employee_id and kbt_token keys."""
        from kbt_config import get_default_config
        cfg = get_default_config()
        self.assertIn("employee_id", cfg)
        self.assertIn("kbt_token",   cfg)

    def test_kbt_server_url_env_override(self):
        """KBT_SERVER_URL env var should override server_url."""
        import kbt_config
        import importlib
        original = os.environ.get("KBT_SERVER_URL")
        try:
            os.environ["KBT_SERVER_URL"] = "https://kbt.test.example"
            cfg = kbt_config.get_default_config()
            cfg = kbt_config._apply_env_overrides(cfg)
            self.assertEqual(cfg["server_url"], "https://kbt.test.example")
        finally:
            if original is None:
                os.environ.pop("KBT_SERVER_URL", None)
            else:
                os.environ["KBT_SERVER_URL"] = original

    def test_validate_config_catches_missing_server_url(self):
        from kbt_config import validate_config
        cfg = {"server_url": "", "employee_id": "emp-001"}
        errors = validate_config(cfg)
        self.assertTrue(any("server_url" in e for e in errors))

    def test_validate_config_catches_missing_employee_id(self):
        from kbt_config import validate_config
        cfg = {"server_url": "https://ok.example", "employee_id": None}
        errors = validate_config(cfg)
        self.assertTrue(any("employee_id" in e for e in errors))

    def test_validate_config_passes_valid(self):
        from kbt_config import validate_config
        cfg = {"server_url": "https://ok.example", "employee_id": "emp-001"}
        errors = validate_config(cfg)
        self.assertEqual(errors, [])


# ══════════════════════════════════════════════════════════════════════════════
# 11. KBT Auto-Login (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestKBTAutoLogin(unittest.TestCase):

    def _load_auto_login_fn(self):
        """Import auto_login from kbt_main without triggering GUI/signal init."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kbt_main",
            str(_AGENT_DIR / "kbt_main.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # kbt_main guards its entry with 'if __name__ == "__main__"'
        # so exec_module is safe — no GUI or sys.exit is triggered.
        spec.loader.exec_module(mod)
        return mod.auto_login

    def test_auto_login_success(self):
        """A 200 response returns session data dict."""
        auto_login = self._load_auto_login_fn()
        identity = {
            "employee_id": "emp-001",
            "token":       "raw-token-abc",
            "api_url":     "http://fake-tbaps.test",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "jwt-abc",
            "employee_id":  "emp-001",
            "name":         "Test Employee",
            "email":        "test@tbaps.test",
        }
        with patch("requests.post", return_value=mock_resp):
            result = auto_login(identity, max_retries=1)
        self.assertEqual(result["access_token"], "jwt-abc")

    def test_auto_login_401_raises_runtime_error(self):
        """A 401 response raises RuntimeError immediately (no retry)."""
        auto_login = self._load_auto_login_fn()
        identity = {
            "employee_id": "emp-bad",
            "token":       "invalid-token",
            "api_url":     "http://fake-tbaps.test",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"detail": "Unauthorized or expired KBT client."}
        with patch("requests.post", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                auto_login(identity, max_retries=1)
        self.assertIn("Unauthorized", str(ctx.exception))

    def test_auto_login_network_failure_raises_connection_error(self):
        """Repeated network failures raise ConnectionError."""
        import requests as req_mod
        auto_login = self._load_auto_login_fn()
        identity = {
            "employee_id": "emp-001",
            "token":       "raw-token",
            "api_url":     "http://unreachable.test",
        }
        with patch("requests.post", side_effect=req_mod.exceptions.ConnectionError("timeout")):
            with self.assertRaises(ConnectionError):
                auto_login(identity, max_retries=2)

    def test_load_identity_raises_when_missing(self):
        """load_identity() raises FileNotFoundError if no file exists."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kbt_main", str(_AGENT_DIR / "kbt_main.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Patch all three search paths to non-existent
        with patch.object(mod, "_BASE_DIR", Path("/tmp/no_such_dir_kbt_test")):
            with self.assertRaises(FileNotFoundError):
                mod.load_identity()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    test_classes = [
        TestCategorizer,
        TestInputMetrics,
        TestWindowSessionTracker,
        TestOfflineBuffer,
        TestAuthModule,
        TestSecurityModule,
        TestConfig,
        TestWebsitesCollector,
        TestTransparency,
        # KBT-specific tests
        TestKBTConfig,
        TestKBTAutoLogin,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    print("\n" + "=" * 70)
    print("  KBT Executable — Full Test Suite (NEF + KBT)")
    print("=" * 70 + "\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    total  = result.testsRun
    passed = total - len(result.failures) - len(result.errors)
    print(f"  Total: {total}  |  Passed: {passed}  |  Failed: {len(result.failures)}  |  Errors: {len(result.errors)}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
