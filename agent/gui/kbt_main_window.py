"""
KBT Executable — Main Application Window
No login button. Employee data comes from auto-login response.
Tabs: Work Tracker | Chat | Status
"""

import logging
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTabWidget, QFrame, QStatusBar,
    QSizePolicy, QApplication
)

from gui.chat_widget         import ChatWidget
from gui.work_tracker_widget import WorkTrackerWidget
from gui import api_client

logger = logging.getLogger(__name__)

_STYLE = """
    QMainWindow { background: #0f1117; }
    QTabWidget::pane { border: none; background: #0f1117; }
    QTabWidget::tab-bar { alignment: left; }
    QTabBar::tab {
        background: #1a1f2e; color: #94a3b8;
        border: none; padding: 10px 24px;
        font-size: 13px; font-weight: bold;
    }
    QTabBar::tab:selected {
        background: #0f1117; color: #7dd3fc;
        border-bottom: 2px solid #3b82f6;
    }
    QTabBar::tab:hover { background: #253050; color: #e0e6f0; }
    QStatusBar {
        background: #1a1f2e; color: #64748b;
        font-size: 11px; border-top: 1px solid #334155;
    }
    QLabel#header_name { color: #e0e6f0; font-size: 14px; font-weight: bold; }
    QLabel#header_role { color: #94a3b8; font-size: 12px; }
    QLabel#monitor_active   { color: #22c55e; font-size: 12px; font-weight: bold; }
    QLabel#monitor_inactive { color: #64748b; font-size: 12px; }
    QPushButton#logout_btn {
        background: transparent; color: #94a3b8;
        border: 1px solid #334155; border-radius: 6px;
        padding: 6px 14px; font-size: 12px;
    }
    QPushButton#logout_btn:hover { color: #f87171; border-color: #f87171; }
"""


class KBTMainWindow(QMainWindow):
    """
    Main window for the KBT Executable.
    Receives user_data from auto-login and starts the telemetry thread.
    """

    def __init__(self, cfg: dict, user_data: dict,
                 shutdown_event: threading.Event, parent=None):
        super().__init__(parent)
        self._cfg      = cfg
        self._user_data = user_data
        self._shutdown  = shutdown_event
        self._telem_thread = None
        self._build_ui()
        self._start_telemetry()

    def _build_ui(self):
        self.setWindowTitle("KBT — TBAPS Monitoring Active")
        self.setMinimumSize(960, 640)
        self.setStyleSheet(_STYLE)

        central = QWidget()
        central.setStyleSheet("background: #0f1117;")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # ── Top header ─────────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(52)
        header.setStyleSheet("""
            QFrame { background: #1a1f2e; border-bottom: 1px solid #334155; }
        """)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)

        brand = QLabel("🛡️ KBT")
        brand.setStyleSheet("color: #7dd3fc; font-size: 15px; font-weight: bold;")
        h.addWidget(brand)
        h.addSpacing(12)

        div = QFrame()
        div.setFixedWidth(1); div.setFixedHeight(24)
        div.setStyleSheet("background: #334155;")
        h.addWidget(div)
        h.addSpacing(12)

        name_lbl = QLabel(f"👤 {self._user_data.get('name', 'Employee')}")
        name_lbl.setObjectName("header_name")
        h.addWidget(name_lbl)

        dept = self._user_data.get("department") or ""
        if dept:
            dept_lbl = QLabel(f"  ·  {dept}")
            dept_lbl.setObjectName("header_role")
            h.addWidget(dept_lbl)

        h.addStretch()

        # Monitoring status badge
        self._monitor_lbl = QLabel("● Monitoring Active")
        self._monitor_lbl.setObjectName("monitor_active")
        h.addWidget(self._monitor_lbl)
        h.addSpacing(16)

        logout_btn = QPushButton("Sign Out")
        logout_btn.setObjectName("logout_btn")
        logout_btn.clicked.connect(self._logout)
        h.addWidget(logout_btn)

        root.addWidget(header)

        # ── Tabs ───────────────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, stretch=1)

        self._work_widget = WorkTrackerWidget()
        self.tabs.addTab(self._work_widget, "⏱️  Work Tracker")

        self._chat_widget = ChatWidget()
        self.tabs.addTab(self._chat_widget, "💬  Chat")

        status_tab = self._build_status_tab()
        self.tabs.addTab(status_tab, "📡  Status")

        # ── Status bar ─────────────────────────────────────────────────────────
        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("KBT Secure Client — Monitoring Active")

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #0f1117;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        title = QLabel("Client Status")
        title.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e6f0;")
        layout.addWidget(title)

        info = {
            "Employee ID": self._user_data.get("employee_id", "—"),
            "Name":        self._user_data.get("name", "—"),
            "Email":       self._user_data.get("email", "—"),
            "Department":  self._user_data.get("department", "—"),
            "Server":      self._cfg.get("server_url", "—"),
        }
        for k, v in info.items():
            row = QHBoxLayout()
            key_lbl = QLabel(f"{k}:")
            key_lbl.setStyleSheet("color: #64748b; font-size: 13px;")
            key_lbl.setFixedWidth(120)
            val_lbl = QLabel(str(v))
            val_lbl.setStyleSheet("color: #e0e6f0; font-size: 13px;")
            val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(key_lbl)
            row.addWidget(val_lbl)
            row.addStretch()
            layout.addLayout(row)

        # Telemetry stats (updated by TelemetryThread signal)
        layout.addSpacing(16)
        stats_title = QLabel("Telemetry")
        stats_title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        stats_title.setStyleSheet("color: #94a3b8;")
        layout.addWidget(stats_title)

        self._telem_pending  = QLabel("Events pending: —")
        self._telem_uploaded = QLabel("Events uploaded: —")
        self._telem_circuit  = QLabel("Circuit: closed")
        for lbl in (self._telem_pending, self._telem_uploaded, self._telem_circuit):
            lbl.setStyleSheet("color: #e0e6f0; font-size: 12px;")
            layout.addWidget(lbl)

        layout.addStretch()

        note = QLabel(
            "The KBT monitoring agent runs in the background.\n"
            "All activity data is securely transmitted to your employer's TBAPS server.\n"
            "Contact your IT admin for privacy policy details."
        )
        note.setStyleSheet("color: #475569; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        return w

    # ── Telemetry ──────────────────────────────────────────────────────────────

    def _start_telemetry(self):
        from gui.telemetry_thread import TelemetryThread
        self._telem_thread = TelemetryThread(self._cfg, self._shutdown)
        self._telem_thread.status_update.connect(self._on_telem_status)
        self._telem_thread.stopped.connect(self._on_telem_stopped)
        self._telem_thread.start()
        logger.info("[kbt-gui] Telemetry thread started")

    def _on_telem_status(self, status: dict):
        self._telem_pending.setText(f"Events pending:  {status.get('pending', 0)}")
        self._telem_uploaded.setText(f"Events uploaded: {status.get('total_uploaded', 0)}")
        circuit = "⚠ open (retrying)" if status.get("circuit_open") else "✓ closed"
        self._telem_circuit.setText(f"Circuit: {circuit}")

    def _on_telem_stopped(self):
        self._monitor_lbl.setObjectName("monitor_inactive")
        self._monitor_lbl.setText("○ Monitoring Inactive")
        self._monitor_lbl.setStyleSheet("color: #64748b; font-size: 12px;")

    def _logout(self):
        self._shutdown.set()
        api_client.logout()
        self.close()

    def closeEvent(self, event):
        self._shutdown.set()
        if self._telem_thread and self._telem_thread.isRunning():
            self._telem_thread.wait(3000)
        super().closeEvent(event)


# ── Launch helper ──────────────────────────────────────────────────────────────

def launch_gui(cfg: dict, user_data: dict, shutdown_event: threading.Event):
    """Called by kbt_main.launch_gui() after splash + auto-login succeed."""
    api_client.configure(cfg["server_url"])
    # Inject employee_id so chat/work widgets can use it
    api_client._session_token = cfg.get("jwt_token", "")
    api_client._employee_id   = cfg.get("employee_id", "")
    api_client._employee_name = user_data.get("name", "Employee")

    win = KBTMainWindow(cfg, user_data, shutdown_event)
    win.show()
    # Keep reference alive
    import sys
    sys.kbt_window = win
