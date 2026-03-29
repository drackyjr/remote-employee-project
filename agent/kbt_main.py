"""
KBT Executable — Main Entry Point
Single-file executable for the TBAPS Zero-Trust monitoring platform.

Execution flow (no login required):
  1. Load kbt_identity.json (embedded in binary or beside it)
  2. Show SplashScreen "Initializing TBAPS Secure Client (KBT)..."
  3. POST /api/v1/kbt/auto-login → {employee_id, token}
  4. On success → start TelemetryThread + open MainWindow
  5. On failure → show error in splash + exit

CLI flags (for admins / debug):
  --daemon           Run headless daemon (no GUI)
  --test-collectors  Test all collectors and exit
  --status           Print status from ~/.kbt/ and exit
"""

import argparse
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup: works both frozen (PyInstaller) and dev ───────────────────────
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys._MEIPASS)
else:
    _BASE_DIR = Path(__file__).resolve().parent

if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

import kbt_config as _cfg_mod
from kbt_config import load_config, save_config, CONFIG_DIR, validate_config

# ── Logging ───────────────────────────────────────────────────────────────────
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(CONFIG_DIR / "kbt.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("kbt")

# ── Graceful shutdown event ────────────────────────────────────────────────────
_shutdown = threading.Event()

def _handle_signal(sig, frame):
    logger.info(f"[kbt] Signal {sig} — shutting down")
    _shutdown.set()

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── Identity Loader ────────────────────────────────────────────────────────────

def load_identity() -> dict:
    """
    Load kbt_identity.json from:
    1. _MEIPASS (inside frozen binary)
    2. Beside the executable (for generate_kbt.py packaging)
    3. Current working directory (development)
    Returns dict with employee_id, token, api_url.
    Raises FileNotFoundError if not found.
    """
    search_paths = [
        _BASE_DIR / "kbt_identity.json",
        Path(sys.executable).parent / "kbt_identity.json",
        Path.cwd() / "kbt_identity.json",
    ]
    for p in search_paths:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                logger.info(f"[kbt] Loaded identity from {p}")
                return data
            except json.JSONDecodeError as e:
                raise ValueError(f"kbt_identity.json is malformed: {e}")
    raise FileNotFoundError(
        "kbt_identity.json not found. This executable may be corrupted.\n"
        "Please re-download your KBT executable from the onboarding email."
    )


# ── Auto-Login ────────────────────────────────────────────────────────────────

def auto_login(identity: dict, max_retries: int = 3,
               status_cb=None) -> dict:
    """
    POST /api/v1/kbt/auto-login with {employee_id, token}.
    Returns the session dict on success.
    Raises RuntimeError on auth failure.
    Raises ConnectionError on network failure after retries.
    """
    import requests

    api_url   = identity["api_url"].rstrip("/")
    employee_id = identity["employee_id"]
    token       = identity["token"]
    endpoint  = f"{api_url}/api/v1/kbt/auto-login"

    if status_cb:
        status_cb("Connecting to TBAPS server...")

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                endpoint,
                json={"employee_id": employee_id, "token": token},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"[kbt] Auto-login success: {employee_id}")
                return data
            elif resp.status_code == 401:
                detail = resp.json().get("detail", "Unauthorized")
                raise RuntimeError(detail)
            else:
                logger.warning(f"[kbt] Auto-login HTTP {resp.status_code}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[kbt] Auto-login attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                if status_cb:
                    status_cb(f"Retrying... ({attempt}/{max_retries})")
                time.sleep(2 ** attempt)

    raise ConnectionError(
        "Cannot reach TBAPS server after multiple attempts.\n"
        "Check your internet connection and try again."
    )


# ── Collector Test (admin/debug) ───────────────────────────────────────────────

def test_collectors():
    from collectors import sysinfo, processes, idle, usb, files, screenshot
    from collectors import window as window_mod
    from collectors import websites as websites_mod
    from collectors import input_metrics as input_mod

    print("\n" + "=" * 60)
    print("  KBT — Collector Test Suite")
    print("=" * 60)

    input_mod.start()
    time.sleep(1)

    tests = [
        ("sysinfo",       sysinfo.collect),
        ("processes",     processes.collect),
        ("window",        window_mod.collect),
        ("idle",          idle.collect),
        ("usb",           usb.collect),
        ("files",         files.collect),
        ("input_metrics", input_mod.collect),
        ("websites",      lambda: websites_mod.collect("cache")),
        ("screenshot",    lambda: {**screenshot.collect(), "image_b64": "<omitted>"}),
    ]

    passed = failed = 0
    for name, fn in tests:
        try:
            result = fn()
            snippet = json.dumps(result, indent=2, default=str)[:400]
            print(f"\n✅ {name}:\n{snippet}")
            passed += 1
        except Exception as e:
            print(f"\n❌ {name}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)


# ── Status (admin/debug) ───────────────────────────────────────────────────────

def show_status():
    cfg = load_config()
    print("\nKBT Executable Status")
    print(f"  Server:      {cfg.get('server_url') or '(not configured)'}")
    print(f"  Employee ID: {cfg.get('employee_id') or '(not provisioned)'}")
    print(f"  JWT:         {'yes' if cfg.get('jwt_token') else 'no'}")
    log_path = CONFIG_DIR / "kbt.log"
    if log_path.exists():
        lines = log_path.read_text().splitlines()[-10:]
        print("\n  Last 10 log lines:")
        for l in lines:
            print(f"    {l}")


# ── Headless Daemon Mode ──────────────────────────────────────────────────────

def run_daemon(cfg: dict):
    """Headless daemon mode (--daemon flag). Mirrors NEF Agent run_daemon()."""
    errors = validate_config(cfg)
    if errors:
        logger.error("[kbt] Config invalid — cannot start daemon:")
        for err in errors:
            logger.error(f"  ✗ {err}")
        sys.exit(1)

    # Import here to avoid slowing GUI startup
    from uploader import Uploader
    from collectors import sysinfo, processes, idle, usb, files, screenshot
    from collectors import window as window_mod
    from collectors import websites as websites_mod
    from collectors import input_metrics as input_mod
    from kbt_config import cert_available, ca_cert_path, BUFFER_DB

    auth_mgr = None
    uploader = Uploader(
        server_url=cfg["server_url"],
        agent_id=cfg.get("employee_id", "kbt"),
        api_key=cfg.get("jwt_token", ""),
        max_buffer=cfg.get("max_buffer_events", 10_000),
        batch_size=cfg.get("batch_size", 50),
        buffer_db=BUFFER_DB,
    )

    dns_method = cfg.get("dns_capture_method", "cache")
    if cfg.get("collect_input_metrics", True):
        input_mod.start()
    files.start_watching()

    last_heartbeat = last_upload = last_screenshot = 0.0
    last_sysinfo = last_websites = last_input = 0.0

    upload_interval     = cfg.get("upload_interval",        10)
    heartbeat_interval  = cfg.get("heartbeat_interval",     60)
    screenshot_interval = cfg.get("screenshot_interval",   300)
    sysinfo_interval    = cfg.get("sysinfo_interval",      600)
    website_interval    = cfg.get("website_flush_interval", 300)
    input_interval      = cfg.get("input_metrics_interval",  60)
    window_poll         = cfg.get("window_poll_interval",     5)

    logger.info(f"[kbt] Daemon started → {cfg['server_url']}")

    while not _shutdown.is_set():
        now = time.time()
        uploader.buffer(window_mod.collect())
        for s in window_mod.get_completed_sessions():
            uploader.buffer(s)
        uploader.buffer(processes.collect())
        uploader.buffer(idle.collect())
        if cfg.get("collect_usb", True):
            uploader.buffer(usb.collect())
        if cfg.get("collect_files", True):
            uploader.buffer(files.collect())
        if cfg.get("collect_input_metrics", True) and now - last_input >= input_interval:
            uploader.buffer(input_mod.collect())
            last_input = now
        if now - last_websites >= website_interval:
            uploader.buffer(websites_mod.collect(dns_method))
            last_websites = now
        if now - last_sysinfo >= sysinfo_interval:
            uploader.buffer(sysinfo.collect())
            last_sysinfo = now
        if cfg.get("collect_screenshots", True) and now - last_screenshot >= screenshot_interval:
            uploader.buffer(screenshot.collect())
            last_screenshot = now
        if now - last_heartbeat >= heartbeat_interval:
            uploader.heartbeat()
            last_heartbeat = now
        if now - last_upload >= upload_interval:
            uploader.flush()
            last_upload = now
        _shutdown.wait(window_poll)

    logger.info("[kbt] Daemon shutting down")
    last_session = window_mod.flush_current_session()
    if last_session:
        uploader.buffer(last_session)
    uploader.flush()
    websites_mod.stop_packet_sniffer()
    logger.info("[kbt] Shutdown complete")


# ── GUI Launch ────────────────────────────────────────────────────────────────

def launch_gui(identity: dict):
    """
    Full GUI boot sequence:
    1. Show SplashScreen
    2. Auto-login in background thread
    3. On success → open MainWindow + start TelemetryThread
    4. On failure → show error in splash
    """
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtGui import QFont

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))

    from gui.splash_screen import SplashScreen
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    # Auto-login in a plain thread (not QThread) so we can update splash via signal
    session_data = {}
    login_error  = {}

    def _do_login():
        try:
            data = auto_login(
                identity,
                status_cb=lambda msg: splash.set_message(msg),
            )
            session_data.update(data)
        except Exception as e:
            login_error["msg"] = str(e)

    t = threading.Thread(target=_do_login, daemon=True)
    t.start()

    # Poll for completion — keep GUI responsive
    while t.is_alive():
        app.processEvents()
        time.sleep(0.05)

    if login_error:
        splash.set_error(login_error["msg"])
        splash.exec()     # blocks until user clicks Close
        sys.exit(1)

    # ── Success ────────────────────────────────────────────────────────────
    splash.set_message("Authentication successful. Starting monitoring...")
    splash.set_success()
    app.processEvents()
    time.sleep(0.5)

    # Save session JWT to config for telemetry thread
    cfg = load_config()
    cfg["server_url"]  = identity["api_url"]
    cfg["employee_id"] = identity["employee_id"]
    cfg["jwt_token"]   = session_data.get("access_token", "")
    save_config(cfg)

    # Launch main window
    from gui.kbt_main_window import launch_gui as _launch_main
    splash.hide()
    _launch_main(cfg, session_data, _shutdown)

    sys.exit(app.exec())


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="KBT Executable — TBAPS Secure Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kbt                     Launch GUI (default)
  kbt --daemon            Headless daemon mode
  kbt --test-collectors   Test all collectors and exit
  kbt --status            Show current status
        """
    )
    parser.add_argument("--daemon",           action="store_true", help="Run headless daemon (no GUI)")
    parser.add_argument("--test-collectors",  action="store_true", help="Test all collectors and exit")
    parser.add_argument("--status",           action="store_true", help="Show current agent status and exit")
    args = parser.parse_args()

    if args.test_collectors:
        test_collectors()
        sys.exit(0)

    if args.status:
        show_status()
        sys.exit(0)

    # Load identity
    try:
        identity = load_identity()
    except (FileNotFoundError, ValueError) as e:
        print(f"\n❌ KBT Error: {e}\n", file=sys.stderr)
        sys.exit(1)

    if args.daemon:
        # Headless: auto-login then run daemon loop
        try:
            session = auto_login(identity)
        except Exception as e:
            logger.error(f"[kbt] Auto-login failed: {e}")
            sys.exit(1)
        cfg = load_config()
        cfg["server_url"]  = identity["api_url"]
        cfg["employee_id"] = identity["employee_id"]
        cfg["jwt_token"]   = session.get("access_token", "")
        save_config(cfg)
        run_daemon(cfg)
    else:
        # GUI mode (default — employee just double-clicks)
        launch_gui(identity)
