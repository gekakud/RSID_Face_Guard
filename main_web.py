#!/usr/bin/env python3

"""
Entry point for the RealSense ID Host Mode application -- the only one.

Run:

    .venv/bin/python main_web.py          # or ./run_main_web.sh

Discovers and configures the device, initialises hardware, then launches the
QtWebEngine-hosted web UI (gui_web.web_window.GUIWeb) that renders the
designer-provided interface in demo_ui/. The session state machine lives in
session/controller.py; GUIWeb is its view adapter. Business/hardware layers
(config, db, hardware, face_auth) carry no UI dependency.

See howto.md section "QtWebEngine (web UI) setup" for the one-time system
dependencies (libwebp/libtiff symlinks) this requires on a fresh Pi.
"""

import argparse
import copy
import logging
import os
import platform
import signal
import sys

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
from observability.logging_setup import get_logger, install_native_log_bridge, setup_logging

setup_logging()
install_native_log_bridge()
log = get_logger("main")

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
    from observability import events
    events.emit("device_boot", app_version=config.APP_VERSION, ui="web")
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
        # Best-effort only: no heartbeat thread/BindingManager exists yet at
        # this point in boot, so this event won't reach the server this boot
        # cycle -- still useful for local log correlation.
        events.emit("hardware_error", where="boot_device_discovery", error=str(e))
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
            # Best-effort only (see note above) -- process exits before any
            # heartbeat/BindingManager can send it.
            events.emit("hardware_error", where="boot_device_config", error=str(e))
            os._exit(1)
        finally:
            f.disconnect()

    log.info("Using port: %s (%s)", port, device_type)
    log.info("Using camera index: %d", camera_index)

    if config.AUTH_ONLY_ON_CARD:
        try:
            initialize_card_reader()
            log.info("Card reader initialized")
        except Exception as e:
            log.exception("Card reader initialization error: %s", e)
            # Best-effort only (see note above).
            events.emit("hardware_error", where="boot_card_reader", error=str(e))
            raise

    if config.RUN_WITH_RELAY:
        try:
            initialize_relay(
                relay_pin=config.RELAY_PIN,
                active_low=config.RELAY_ACTIVE_LOW,
                default_off=config.RELAY_DEFAULT_OFF,
            )
            log.info("Relay initialized")
        except Exception as e:
            log.exception("Relay initialization error: %s", e)
            # Non-fatal: RelayController already degrades gracefully
            # internally, so keep booting without the relay.
            events.emit("hardware_error", where="boot_relay", error=str(e))

    app = QApplication(sys.argv)

    # TODO: init port, camera_index, device_type inside GUIWeb - not this script responsibility
    window = GUIWeb(port, camera_index, device_type)
    window.show_appropriately()

    # Ctrl+C (SIGINT) is normally swallowed by the Qt event loop. Install a
    # handler that triggers an orderly window shutdown (stop preview, wait for
    # in-flight auth, disconnect device) instead of letting native threads race
    # during interpreter teardown -- which otherwise aborts the process with
    # "terminate called without an active exception".
    def _handle_signal(signum, _frame):
        log.info("Received signal %s -- shutting down cleanly", signum)
        # Failsafe: if graceful cleanup blocks (native threads can hang), a
        # watchdog thread force-exits after a short grace period so Ctrl+C
        # always terminates the process promptly.
        import threading as _t

        def _watchdog():
            os._exit(0)
        wd = _t.Timer(6.0, _watchdog)
        wd.daemon = True
        wd.start()
        try:
            window.shutdown()
        except Exception:
            log.exception("Error during signal shutdown")
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Let the Python interpreter run periodically so the signal handler above
    # can actually fire while the Qt C++ event loop is blocking.
    from PySide6.QtCore import QTimer
    _sig_timer = QTimer()
    _sig_timer.start(200)
    _sig_timer.timeout.connect(lambda: None)

    try:
        exit_code = app.exec()
    finally:
        # Ensure cleanup even if the loop exits by other means (idempotent).
        window.shutdown()
    sys.exit(exit_code)

if __name__ == '__main__':
    main()