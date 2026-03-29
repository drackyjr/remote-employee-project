"""
KBT Executable — Configuration Shim
Identical to the NEF Agent's config.py but stores everything under ~/.kbt/
instead of ~/.nef/, and reads KBT_SERVER_URL in addition to NEF_SERVER_URL.
"""

import json
import os
import platform
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
CONFIG_DIR  = Path.home() / ".kbt"
CONFIG_FILE = CONFIG_DIR / "config.json"
BUFFER_DB   = CONFIG_DIR / "buffer.db"
STATUS_LOG  = CONFIG_DIR / "status.log"
CERT_DIR    = CONFIG_DIR / "certs"


def get_default_config() -> dict:
    return {
        # ── Server connection ──────────────────────────────────────────────────
        "server_url":     "",
        "agent_id":       None,
        "api_key":        None,

        # ── KBT identity (loaded from kbt_identity.json at runtime) ───────────
        "employee_id":    None,
        "kbt_token":      None,

        # ── JWT cache (written by auth.py at runtime) ─────────────────────────
        "jwt_token":      None,
        "jwt_expires_at": None,
        "jwt_refresh":    None,

        # ── Authentication ─────────────────────────────────────────────────────
        "cert_path":      str(CERT_DIR / "kbt.crt"),
        "key_path":       str(CERT_DIR / "kbt.key"),
        "ca_cert_path":   str(CERT_DIR / "ca.crt"),
        "tls_verify":     True,

        # ── Intervals (seconds) ───────────────────────────────────────────────
        "heartbeat_interval":     60,
        "upload_interval":        10,
        "screenshot_interval":    300,
        "website_flush_interval": 300,
        "sysinfo_interval":       600,
        "input_metrics_interval": 60,
        "window_poll_interval":   5,

        # ── Collection toggles ────────────────────────────────────────────────
        "collect_screenshots":     True,
        "collect_files":           True,
        "collect_browser_history": True,
        "collect_input_metrics":   True,
        "collect_usb":             True,

        # ── DNS / Website monitoring ──────────────────────────────────────────
        "dns_capture_method":     "cache",

        # ── Offline buffer ─────────────────────────────────────────────────────
        "max_buffer_events":      10000,
        "batch_size":             50,

        # ── Transparency ──────────────────────────────────────────────────────
        "enable_tray_icon":       True,
    }


def _apply_env_overrides(cfg: dict) -> dict:
    """
    Environment variable overrides.
    KBT_SERVER_URL takes precedence, then NEF_SERVER_URL (backwards-compat),
    then the value in config.json.
    """
    overrides = {
        "server_url":          os.environ.get("KBT_SERVER_URL") or os.environ.get("NEF_SERVER_URL", ""),
        "agent_id":            os.environ.get("KBT_AGENT_ID",            ""),
        "api_key":             os.environ.get("KBT_API_KEY",             ""),
        "upload_interval":     os.environ.get("KBT_UPLOAD_INTERVAL",     ""),
        "heartbeat_interval":  os.environ.get("KBT_HEARTBEAT_INTERVAL",  ""),
        "screenshot_interval": os.environ.get("KBT_SCREENSHOT_INTERVAL", ""),
    }
    numeric_fields = {"upload_interval", "heartbeat_interval", "screenshot_interval"}
    for key, value in overrides.items():
        if value:
            if key in numeric_fields and value.isdigit():
                cfg[key] = int(value)
            else:
                cfg[key] = value
    return cfg


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            cfg = get_default_config()
            cfg.update(data)
            return _apply_env_overrides(cfg)
        except (json.JSONDecodeError, OSError):
            pass
    cfg = get_default_config()
    return _apply_env_overrides(cfg)


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def validate_config(cfg: dict) -> list:
    """Returns list of error strings; empty = config is valid."""
    errors = []
    server_url = cfg.get("server_url", "").strip()
    if not server_url:
        errors.append(
            "server_url is not set. "
            "Set KBT_SERVER_URL environment variable or re-run with a valid kbt_identity.json."
        )
    elif not (server_url.startswith("http://") or server_url.startswith("https://")):
        errors.append(f"server_url '{server_url}' must start with http:// or https://.")

    if not cfg.get("employee_id"):
        errors.append("employee_id is not set. Ensure kbt_identity.json is present.")

    return errors


def cert_available(cfg: dict) -> bool:
    return (
        Path(cfg.get("cert_path", "")).exists()
        and Path(cfg.get("key_path",  "")).exists()
    )


def ca_cert_path(cfg: dict):
    ca = cfg.get("ca_cert_path", "")
    if ca and Path(ca).exists():
        return ca
    return cfg.get("tls_verify", True)
