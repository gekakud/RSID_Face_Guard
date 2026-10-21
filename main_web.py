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


def _apply_provisioned_mode() -> None:
    """Let the server-issued mode override config.DEVICE_MODE (FR-MODE-01, T4).

    Must run before any service is built: the mode decides whether a card
    reader exists at all. The identity is read once at boot -- a device keeps
    the mode it was bound with, and changing it means re-binding and
    restarting.
    """
    try:
        from provisioning import identity as identity_store

        identity = identity_store.load()
    except Exception as exc:
        log.error("Could not read device identity for mode (ignored): %s", exc)
        return

    if identity is None or not identity.device_mode:
        log.info("Device mode: %s (config.py fallback)", config.DEVICE_MODE)
        return

    try:
        config.set_device_mode(identity.device_mode, source="device identity")
        log.info("Device mode: %s (provisioned by server)", config.DEVICE_MODE)
    except NotImplementedError:
        # An unimplemented mode must not be downgraded to a door-opening one.
        raise
    except ValueError as exc:
        log.error("%s -- keeping %s", exc, config.DEVICE_MODE)

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
    from PySide6.QtCore import Qt
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
    from observability.events import EventType
    events.emit(EventType.DEVICE_BOOT, app_version=config.APP_VERSION, ui="web")
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

    # Before any service is built: the mode decides whether a card reader is
    # initialised at all (FR-MODE-01, T4).
    _apply_provisioned_mode()

    log.info("Discovering device type on port: %s", port)
    try:
        device_type = rsid_py.discover_device_type(port)
        log.info("Device type: %s", device_type)
    except Exception as e:
        log.exception("Could not connect to device on port %s: %s", port, e)
        # Best-effort only: no heartbeat thread/BindingManager exists yet at
        # this point in boot, so this event won't reach the server this boot
        # cycle -- still useful for local log correlation.
        events.emit(EventType.HARDWARE_ERROR, where="boot_device_discovery", error=str(e))
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
            events.emit(EventType.HARDWARE_ERROR, where="boot_device_config", error=str(e))
            os._exit(1)
        finally:
            f.disconnect()

    log.info("Using port: %s (%s)", port, device_type)
    log.info("Using camera index: %d", camera_index)

    if config.mode_uses_card_reader():
        try:
            initialize_card_reader()
            log.info("Card reader initialized")
        except Exception as e:
            log.exception("Card reader initialization error: %s", e)
            # Best-effort only (see note above).
            events.emit(EventType.HARDWARE_ERROR, where="boot_card_reader", error=str(e))
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
            events.emit(EventType.HARDWARE_ERROR, where="boot_relay", error=str(e))

    app = QApplication(sys.argv)

    # TODO: init port, camera_index, device_type inside GUIWeb - not this script responsibility
    window = GUIWeb(port, camera_index, device_type)
    window.show_appropriately()

    # Ctrl+C (SIGINT) is normally swallowed by the Qt event loop (and by
    # Chromium, which installs its own handlers when QtWebEngine starts).
    # Shut down in order (stop preview, wait for in-flight auth, disconnect
    # device) instead of letting native threads race during interpreter
    # teardown -- which otherwise aborts with "terminate called without an
    # active exception".
    def _shutdown_now(reason):
        log.info("%s -- shutting down cleanly", reason)
        # Failsafe: if graceful cleanup blocks (native threads can hang), a
        # watchdog thread force-exits after a short grace period so Ctrl+C
        # always terminates the process promptly.
        import threading as _t

        wd = _t.Timer(6.0, lambda: os._exit(0))
        wd.daemon = True
        wd.start()
        try:
            window.shutdown()
        except Exception:
            log.exception("Error during shutdown")
        os._exit(0)

    def _handle_signal(signum, _frame):
        _shutdown_now("Received signal %s" % signum)

    # Deliver signals through the Qt event loop: the C handler writes a byte to
    # a socketpair, QSocketNotifier wakes the main thread and runs Python.
    import socket

    from PySide6.QtCore import QSocketNotifier, QTimer

    _sig_r, _sig_w = socket.socketpair()
    _sig_r.setblocking(False)
    _sig_w.setblocking(False)
    signal.set_wakeup_fd(_sig_w.fileno())

    _sig_notifier = QSocketNotifier(_sig_r.fileno(), QSocketNotifier.Type.Read)

    def _drain(_fd):
        try:
            _sig_r.recv(64)
        except OSError:
            pass
    _sig_notifier.activated.connect(_drain)

    def _install_handlers():
        # Re-installed after startup: QtWebEngine/Chromium replaces SIGINT/
        # SIGTERM handlers of its own once the render processes come up.
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.set_wakeup_fd(_sig_w.fileno())

    _install_handlers()
    for _delay in (1000, 3000, 8000):
        QTimer.singleShot(_delay, _install_handlers)

    # Keyboard escape hatch from the kiosk window itself: the QWebEngineView
    # has focus and eats key events, so use application-wide shortcuts.
    from PySide6.QtGui import QKeySequence, QShortcut

    _shortcuts = []
    for _seq in ("Ctrl+C", "Ctrl+Q", "Esc"):
        sc = QShortcut(QKeySequence(_seq), window)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(lambda s=_seq: _shutdown_now("Shortcut %s" % s))
        _shortcuts.append(sc)

    # Keep the interpreter ticking as a last-resort fallback.
    _sig_timer = QTimer()
    _sig_timer.timeout.connect(lambda: None)
    _sig_timer.start(200)

    try:
        exit_code = app.exec()
    finally:
        # Ensure cleanup even if the loop exits by other means (idempotent).
        window.shutdown()
    sys.exit(exit_code)

if __name__ == '__main__':
    main()