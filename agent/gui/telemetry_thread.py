"""
KBT Executable — Telemetry Daemon Thread
Wraps the NEF Agent daemon loop inside a QThread so it runs
alongside the GUI in the same process.

The thread:
  - reads kbt_config (already loaded by kbt_main)
  - creates the Uploader and starts collectors
  - emits status_update every 60 s (for the Status tab)
  - stops cleanly when _shutdown is set
"""

import logging
import time
import threading
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("kbt.telemetry")


class TelemetryThread(QThread):
    """
    Background telemetry daemon that runs collector loops and uploads
    events to the TBAPS backend every 60 seconds.

    Signals:
        status_update(dict) — emitted every 60 s with uploader status dict
        started_ok()        — emitted once when the daemon loop successfully starts
        stopped()           — emitted when the daemon shuts down cleanly
    """
    status_update = pyqtSignal(dict)
    started_ok    = pyqtSignal()
    stopped       = pyqtSignal()

    def __init__(self, cfg: dict, shutdown_event: threading.Event, parent=None):
        super().__init__(parent)
        self._cfg      = cfg
        self._shutdown = shutdown_event

    def run(self):
        """Main daemon loop — mirrors run_daemon() from main.py."""
        cfg = self._cfg
        logger.info("[telemetry] Daemon thread starting")

        try:
            self._run_loop(cfg)
        except Exception as e:
            logger.error(f"[telemetry] Daemon thread crashed: {e}", exc_info=True)
        finally:
            self.stopped.emit()
            logger.info("[telemetry] Daemon thread stopped")

    def _run_loop(self, cfg: dict):
        # ── Lazy imports to avoid slowing main-thread startup ─────────────────
        import sys, os
        # Make sure agent/ root is on path when frozen
        if getattr(__import__("sys"), "frozen", False):
            import sys as _sys
            _sys.path.insert(0, __import__("sys")._MEIPASS)

        from uploader import Uploader
        from collectors import sysinfo, processes, idle, usb, files, screenshot
        from collectors import window as window_mod
        from collectors import websites as websites_mod
        from collectors import input_metrics as input_mod
        from kbt_config import cert_available, ca_cert_path, BUFFER_DB

        # ── Auth manager (mTLS — optional) ────────────────────────────────────
        auth_mgr = None
        if cert_available(cfg):
            try:
                from core.auth import AuthManager
                auth_mgr = AuthManager(
                    server_url=cfg["server_url"],
                    agent_id=cfg.get("agent_id", cfg.get("employee_id", "")),
                    api_key=cfg.get("api_key", ""),
                    cert_path=cfg["cert_path"],
                    key_path=cfg["key_path"],
                    ca_cert_path=ca_cert_path(cfg),
                )
            except Exception as e:
                logger.debug(f"[telemetry] mTLS auth not available: {e}")

        uploader = Uploader(
            server_url=cfg["server_url"],
            agent_id=cfg.get("agent_id", cfg.get("employee_id", "kbt")),
            api_key=cfg.get("api_key", cfg.get("jwt_token", "")),
            auth_manager=auth_mgr,
            max_buffer=cfg.get("max_buffer_events", 10_000),
            batch_size=cfg.get("batch_size", 50),
            ca_verify=ca_cert_path(cfg),
            buffer_db=BUFFER_DB,
        )

        # ── DNS sniffer ───────────────────────────────────────────────────────
        dns_method = cfg.get("dns_capture_method", "cache")
        if dns_method == "packet":
            ok = websites_mod.start_packet_sniffer()
            if not ok:
                dns_method = "cache"

        # ── Input / file watchers ─────────────────────────────────────────────
        if cfg.get("collect_input_metrics", True):
            input_mod.start()
        files.start_watching()

        # ── Interval trackers ─────────────────────────────────────────────────
        last_heartbeat  = 0.0
        last_upload     = 0.0
        last_screenshot = 0.0
        last_sysinfo    = 0.0
        last_websites   = 0.0
        last_input      = 0.0
        last_status     = 0.0

        upload_interval     = cfg.get("upload_interval",        10)
        heartbeat_interval  = cfg.get("heartbeat_interval",     60)
        screenshot_interval = cfg.get("screenshot_interval",   300)
        sysinfo_interval    = cfg.get("sysinfo_interval",      600)
        website_interval    = cfg.get("website_flush_interval", 300)
        input_interval      = cfg.get("input_metrics_interval",  60)
        window_poll         = cfg.get("window_poll_interval",     5)

        self.started_ok.emit()
        logger.info(f"[telemetry] Daemon loop started → {cfg['server_url']}")

        while not self._shutdown.is_set():
            now = time.time()

            # Window session tracking (every ~5 s)
            session_event = window_mod.collect()
            uploader.buffer(session_event)
            for completed in window_mod.get_completed_sessions():
                uploader.buffer(completed)

            # Processes / idle / USB / files
            uploader.buffer(processes.collect())
            uploader.buffer(idle.collect())
            if cfg.get("collect_usb", True):
                uploader.buffer(usb.collect())
            if cfg.get("collect_files", True):
                uploader.buffer(files.collect())

            # Input metrics (every 60 s)
            if cfg.get("collect_input_metrics", True) and now - last_input >= input_interval:
                uploader.buffer(input_mod.collect())
                last_input = now

            # Website / DNS (every 5 min)
            if now - last_websites >= website_interval:
                uploader.buffer(websites_mod.collect(dns_method))
                last_websites = now

            # Sysinfo (every 10 min)
            if now - last_sysinfo >= sysinfo_interval:
                uploader.buffer(sysinfo.collect())
                last_sysinfo = now

            # Screenshot (every N sec)
            if cfg.get("collect_screenshots", True) and now - last_screenshot >= screenshot_interval:
                uploader.buffer(screenshot.collect())
                last_screenshot = now

            # Heartbeat (every 60 s)
            if now - last_heartbeat >= heartbeat_interval:
                uploader.heartbeat()
                last_heartbeat = now

            # Upload flush (every 10 s)
            if now - last_upload >= upload_interval:
                uploader.flush()
                last_upload = now

            # Emit status (every 60 s) for GUI status tab
            if now - last_status >= 60:
                self.status_update.emit(uploader.status())
                last_status = now

            self._shutdown.wait(window_poll)

        # ── Graceful shutdown ─────────────────────────────────────────────────
        logger.info("[telemetry] Flushing remaining events before shutdown")
        last_session = window_mod.flush_current_session()
        if last_session:
            uploader.buffer(last_session)
        uploader.flush()
        if auth_mgr:
            auth_mgr.stop()
        websites_mod.stop_packet_sniffer()
