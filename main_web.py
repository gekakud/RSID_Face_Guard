#!/usr/bin/env python3

"""
Entry point for the RealSense ID Host Mode application (Web UI front-end).

Run this for the web-based GUI:

    .venv/bin/python main_web.py          # (main.py = Tkinter, main_qt.py = Qt widgets)

Identical device discovery/configuration flow to main.py / main_qt.py, but
launches the QtWebEngine-hosted web UI (gui_web.web_window.GUIWeb) that renders
the designer-provided interface in demo_ui/. All business/hardware layers
(config, db, hardware, face_auth) are shared unchanged across all three
front-ends.

See howto.md section "QtWebEngine (web UI) setup" for the one-time system
dependencies (libwebp/libtiff symlinks) this requires on a fresh Pi.
"""

import argparse
import copy
import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler

# --- QtWebEngine runtime requirements (must be set before any Qt import) -----
# 1. Chromium refuses to run as root with its sandbox on; the kiosk runs as root.
# 2. Force software OpenGL: the Pi's GL stack under X can't back Chromium's GPU
#    process, which otherwise silently stalls page loads.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-gpu-compositing --in-process-gpu",
)
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

import config

def _setup_logging() -> logging.Logger:
    """Configure and return the application logger (same setup as main_qt.py)."""
    _log = logging.getLogger("face_guard")
    _log.setLevel(logging.INFO)
    if _log.handlers:
        return _log
    _fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setFormatter(_fmt)
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_guard.log")
    fh = RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(_fmt)
    _log.addHandler(ch)
    _log.addHandler(fh)
    return _log

log = _setup_logging()

try:
    import rsid_py
    log.info("rsid_py version: %s", rsid_py.__version__)
except ImportError:
    log.critical("Failed importing rsid_py. Please ensure rsid_py module is available.")
    sys.exit(1)

try:
    import numpy  # noqa: F401
except ImportError:
    log.critical("Failed importing numpy. Please install it (pip install numpy).")
    sys.exit(1)

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
except ImportError as e:
    log.critical(
        "Failed importing PySide6 QtWebEngine (%s). See howto.md section 7 "
        "(QtWebEngine setup) for the libwebp/libtiff symlink fixes.", e
    )
    sys.exit(1)

from gui_web.web_window import GUIWeb
from hardware.card_reader_api import initialize_card_reader
from hardware.relay_api import initialize_relay

def main():
    """Entry point: parse CLI args, discover and configure the device, then run the web GUI."""
    parser = argparse.ArgumentParser(prog='main_web', description='RealSense ID Host Mode GUI (Web UI)')
    parser.add_argument('-p', '--port', help='Device port', type=str, default=None)
    parser.add_argument('-c', '--camera', help='Camera number (-1 for autodetect)', type=int, default=-1)
    args = parser.parse_args()

    if args.port is None:
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            port = "COM9" if platform.system() == "Windows" else "/dev/ttyACM0"
            log.warning("No devices auto-detected. Trying default port: %s", port)
        else:
            port = devices[0]
            log.info("Auto-detected device on port: %s", port)
    else:
        port = args.port
        log.info("Using specified port: %s", port)

    camera_index = args.camera

    log.info("Discovering device type on port: %s", port)
    try:
        device_type = rsid_py.discover_device_type(port)
        log.info("Device type: %s", device_type)
    except Exception as e:
        log.exception("Could not connect to device on port %s: %s", port, e)
        sys.exit(1)

    log.info("Configuring device...")
    with rsid_py.FaceAuthenticator(device_type, str(port)) as f:
        try:
            device_config = copy.copy(f.query_device_config())
            # device_config.dump_mode = rsid_py.DumpMode.Disable
            # f.set_device_config(device_config)
            log.info("Device configured successfully")
        except Exception as e:
            log.exception("Device configuration error: %s", e)
            os._exit(1)
        finally:
            f.disconnect()

    log.info("Using port: %s (%s)", port, device_type)
    log.info("Using camera index: %d", camera_index)

    if config.AUTH_ONLY_ON_CARD:
        initialize_card_reader()
        log.info("Card reader initialized")

    if config.RUN_WITH_RELAY:
        initialize_relay(
            relay_pin=config.RELAY_PIN,
            active_low=config.RELAY_ACTIVE_LOW,
            default_off=config.RELAY_DEFAULT_OFF,
        )
        log.info("Relay initialized")

    app = QApplication(sys.argv)
    window = GUIWeb(port, camera_index, device_type)
    window.show_appropriately()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()