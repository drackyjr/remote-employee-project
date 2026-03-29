# -*- mode: python ; coding: utf-8 -*-
# ==============================================================================
# KBT Executable — PyInstaller Spec File
# Produces a single-file binary bundling all dependencies.
#
# Usage:
#   cd agent/
#   pyinstaller kbt.spec
# ==============================================================================

import sys
from pathlib import Path

block_cipher = None
base_dir = Path(".").resolve()

a = Analysis(
    ["kbt_main.py"],
    pathex=[str(base_dir)],
    binaries=[],
    datas=[
        # Agent submodules
        ("collectors",   "collectors"),
        ("core",         "core"),
        ("gui",          "gui"),
        ("transparency", "transparency"),
        # Identity placeholder (replaced per-employee by generate_kbt.py)
        ("kbt_identity.json", "."),
    ],
    hiddenimports=[
        # PyQt6
        "PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui",
        "PyQt6.sip",
        # System / monitoring
        "psutil",
        "PIL", "PIL.Image", "PIL.ImageGrab", "PIL.ImageDraw",
        "watchdog", "watchdog.observers", "watchdog.events",
        "watchdog.observers.inotify",
        "pynput", "pynput.keyboard", "pynput.mouse",
        # Networking
        "requests", "urllib3", "certifi", "charset_normalizer", "idna",
        # Auth
        "jose", "jose.jwt", "bcrypt",
        # Storage
        "sqlite3",
        # Collectors internal
        "collectors.categorizer",
        "collectors.files",
        "collectors.idle",
        "collectors.input_metrics",
        "collectors.processes",
        "collectors.screenshot",
        "collectors.sysinfo",
        "collectors.usb",
        "collectors.websites",
        "collectors.window",
        # Core
        "core.auth",
        "core.security",
        # GUI
        "gui.api_client",
        "gui.chat_widget",
        "gui.kbt_main_window",
        "gui.splash_screen",
        "gui.telemetry_thread",
        "gui.work_tracker_widget",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "numpy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="KBT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,      # Set False for a windowed-only build on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,         # Add icon path here for branding: e.g. "assets/kbt.ico"
)
