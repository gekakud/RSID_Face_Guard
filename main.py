#!/usr/bin/env python3

"""
Entry point for the RealSense ID Host Mode application.

Parses CLI args, discovers and configures the RealSense ID device,
initialises hardware (card reader, relay), then constructs and runs
the GUI.

The application is split into focused packages/modules:
    config.py       - all tunable settings/flags
    db/             - unified local/remote user data access (UserDatabase)
    hardware/       - relay_api, card_reader_api, camera_preview
    face_auth/      - HostModeService (business logic, no GUI dependency)
    gui/            - Tkinter GUI (main_window.GUI) + display_utils
"""

import argparse
import copy
import ctypes
import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler

import config


def _setup_logging() -> logging.Logger:
    """Configure and return the application logger.

    Sets up two handlers: a StreamHandler (captured by journald when running as
    a systemd service) and a RotatingFileHandler writing to face_guard.log
    alongside this script (max 1 MB, 5 backups).
    """
    _log = logging.getLogger("face_guard")
    _log.setLevel(logging.INFO)
    if _log.handlers:          # avoid duplicate handlers on reload
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

# Import rsid_py BEFORE tkinter to avoid native library conflicts
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
    from PIL import Image  # noqa: F401
except ImportError:
    log.critical("Failed importing PIL. Please install Pillow (pip install Pillow).")
    sys.exit(1)

try:
    import tkinter  # noqa: F401
except ImportError:
    log.critical("Failed importing tkinter.")
    sys.exit(1)

from gui.main_window import GUI
from hardware.card_reader_api import initialize_card_reader
from hardware.relay_api import initialize_relay


def main():
    """Entry point: parse CLI args, discover and configure the device, then run the GUI."""
    parser = argparse.ArgumentParser(prog='main', description='RealSense ID Host Mode GUI (Tkinter)')
    parser.add_argument('-p', '--port', help='Device port', type=str, default=None)
    parser.add_argument('-c', '--camera', help='Camera number (-1 for autodetect)', type=int, default=-1)
    args = parser.parse_args()

    # Determine port
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

    # Discover device type
    log.info("Discovering device type on port: %s", port)
    try:
        device_type = rsid_py.discover_device_type(port)
        log.info("Device type: %s", device_type)
    except Exception as e:
        log.exception("Could not connect to device on port %s: %s", port, e)
        sys.exit(1)

    # Configure device
    log.info("Configuring device...")
    with rsid_py.FaceAuthenticator(device_type, str(port)) as f:
        try:
            device_config = copy.copy(f.query_device_config())
            device_config.dump_mode = rsid_py.DumpMode.Disable
            f.set_device_config(device_config)
            log.info("Device configured successfully")
        except Exception as e:
            log.exception("Device configuration error: %s", e)
            os._exit(1)
        finally:
            f.disconnect()

    log.info("Using port: %s (%s)", port, device_type)
    log.info("Using camera index: %d", camera_index)

    # Initialize card reader
    if config.RUN_WITH_CARD_READER:
        initialize_card_reader()
        log.info("Card reader initialized")

    if config.RUN_WITH_RELAY:
        initialize_relay(
            relay_pin=config.RELAY_PIN,
            active_low=config.RELAY_ACTIVE_LOW,
            default_off=config.RELAY_DEFAULT_OFF,
        )
        log.info("Relay initialized")

    gui = GUI(port, camera_index, device_type)
    gui.mainloop()


if __name__ == '__main__':
    if sys.platform.startswith('win'):
        app_id = 'intel.realsenseid.hostmode.1.0'
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()

    main()