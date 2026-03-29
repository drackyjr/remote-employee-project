"""
KBT Executable — Splash Screen
Displayed during auto-authentication before the main window opens.
Shows progress messages, animated progress bar, and error state.
"""

import logging
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout
)

logger = logging.getLogger(__name__)


class SplashScreen(QDialog):
    """
    Full-screen splash dialog shown while KBT auto-authenticates.
    Call set_message() to update status text.
    Call set_error() to show an error and enable the exit button.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TBAPS KBT Secure Client")
        self.setFixedSize(480, 300)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )

        # Dark palette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,     QColor("#0f1117"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e6f0"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.setStyleSheet("""
            QDialog { background: #0f1117; border: 1px solid #334155; border-radius: 12px; }
            QLabel#title {
                color: #7dd3fc;
                font-size: 20px;
                font-weight: bold;
            }
            QLabel#subtitle {
                color: #94a3b8;
                font-size: 12px;
            }
            QLabel#status_lbl {
                color: #cbd5e1;
                font-size: 13px;
            }
            QLabel#error_lbl {
                color: #f87171;
                font-size: 13px;
                font-weight: bold;
            }
            QProgressBar {
                background: #1e2336;
                border: 1px solid #334155;
                border-radius: 4px;
                height: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #3b82f6, stop:1 #6366f1);
                border-radius: 4px;
            }
            QPushButton#exit_btn {
                background: #ef4444;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#exit_btn:hover { background: #dc2626; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        # Brand
        title = QLabel("🛡️ KBT Secure Client")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("TBAPS Zero-Trust Monitoring Platform")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # Progress bar (indeterminate)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)        # indeterminate
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        # Status message
        self.status_lbl = QLabel("Initializing TBAPS Secure Client (KBT)...")
        self.status_lbl.setObjectName("status_lbl")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        # Error label (hidden by default)
        self.error_lbl = QLabel("")
        self.error_lbl.setObjectName("error_lbl")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        layout.addStretch()

        # Exit button (only shown on error)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.exit_btn = QPushButton("Close")
        self.exit_btn.setObjectName("exit_btn")
        self.exit_btn.setVisible(False)
        self.exit_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.exit_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_message(self, text: str):
        """Update the status line during init."""
        self.status_lbl.setText(text)

    def set_error(self, text: str):
        """Switch to error state: stop spinner, show red message + exit button."""
        self.progress.setRange(0, 1)        # stop indeterminate spin
        self.progress.setValue(0)
        self.status_lbl.setVisible(False)
        self.error_lbl.setText(text)
        self.error_lbl.setVisible(True)
        self.exit_btn.setVisible(True)

    def set_success(self):
        """Switch progress bar to full (green) before hiding splash."""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_lbl.setText("Authentication successful. Starting monitoring...")
