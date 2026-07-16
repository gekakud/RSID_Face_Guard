#!/usr/bin/env python3

"""
Tkinter GUI for RealSense ID Host Mode with camera preview and authentication
Based on host_mode_gui.py (PyQt5 version) - converted to Tkinter
"""

import argparse
import copy
import ctypes
import logging
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple
from remote_db_sync import sync_remote_users_into_local
from config import (
    USER_DB_FILE,
    DB_SYNC_INTERVAL_SEC,
    CUSTOM_THRESHOLD,
    RUN_WITH_RELAY,
    RELAY_PIN,
    RELAY_ACTIVE_LOW,
    RELAY_DEFAULT_OFF,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    WELCOME_DURATION_MS,
    FAIL_DURATION_MS,
)


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
    # Console → captured by journald via StandardOutput=journal in service
    ch = logging.StreamHandler()
    ch.setFormatter(_fmt)
    # Rotating file: 1 MB max, keep 5 backups
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_guard.log")
    fh = RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(_fmt)
    _log.addHandler(ch)
    _log.addHandler(fh)
    return _log


log = _setup_logging()


# Configuration
SIMULATE_HW = True

# Set to True for RPi5 with small 800x480 screen
RUN_ON_REAL_DEVICE = True

# Set to True to enable card reader monitoring (auto-authenticate when card is detected)
RUN_WITH_CARD_READER = False

# UI mode:
# True  -> manual auth via button
# False -> periodic auto-auth (no button shown)
WITH_BUTTON = False

# Auto-auth interval (seconds) when WITH_BUTTON=False
AUTO_AUTH_INTERVAL_SEC = 5.0

# If True, borderless "kiosk-like" window. Required to prevent WM from
# moving the window to the primary display on a multi-monitor setup.
KIOSK_BORDERLESS = True



# Card API support
if SIMULATE_HW:
    from card_api_sim import (
        initialize_card_reader, get_card_id, disconnect_card_reader,
        initialize_wiegand_tx, send_w32, close_wiegand_tx,
    )
else:
    try:
        from card_api import (
            initialize_card_reader, get_card_id, disconnect_card_reader,
            initialize_wiegand_tx, send_w32, close_wiegand_tx,
        )
    except ImportError:
        log.critical("Card API module not available.")
        sys.exit(1)

# Relay (door) API
if RUN_WITH_RELAY:
    from relay_api import initialize_relay, open_door, disconnect_relay


# Import rsid_py BEFORE tkinter to avoid native library conflicts
try:
    import rsid_py
    log.info("rsid_py version: %s", rsid_py.__version__)
except ImportError:
    log.critical("Failed importing rsid_py. Please ensure rsid_py module is available.")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    log.critical("Failed importing numpy. Please install it (pip install numpy).")
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    log.critical("Failed importing PIL. Please install Pillow (pip install Pillow).")
    sys.exit(1)

try:
    import tkinter as tk
    import tkinter.ttk as ttk
except ImportError:
    log.critical("Failed importing tkinter.")
    sys.exit(1)

from user_db import UserDatabase

WINDOW_NAME = 'RealSenseID Host Mode'


def _parse_xrandr_connected():
    """
    Parse xrandr and return list of connected displays:
    [(name, w, h, x, y), ...]
    """
    displays = []
    if not sys.platform.startswith("linux"):
        return displays
    try:
        out = subprocess.check_output(["xrandr", "--query"], text=True, stderr=subprocess.DEVNULL)
        # Example line:
        # HDMI-1 connected primary 1920x1080+0+0 ...
        # DSI-1 connected 800x480+1920+0 ...
        pat = re.compile(
            r"^(?P<name>\S+)\s+connected(?:\s+primary)?\s+"
            r"(?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+)"
        )
        for line in out.splitlines():
            m = pat.search(line.strip())
            if m:
                displays.append((
                    m.group("name"),
                    int(m.group("w")),
                    int(m.group("h")),
                    int(m.group("x")),
                    int(m.group("y")),
                ))
    except Exception as e:
        print("xrandr parse failed:", e)
    return displays


def _find_small_display_xy(prefer_w=800, prefer_h=480) -> Optional[Tuple[int, int, str]]:
    """
    Return (x, y, output_name) for preferred small display if found.
    """
    displays = _parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == prefer_w and h == prefer_h:
            return (x, y, name)
    return None


def _is_command_available(cmd: str) -> bool:
    """Return True if the given shell command exists on PATH."""
    from shutil import which
    return which(cmd) is not None


def _setup_display_rotation():
    """Ensure the small display is in portrait orientation via xrandr.

    When the Waveshare is the only display it reports its native landscape
    resolution (800x480).  We need to rotate it left so it appears as
    480x800 to the window manager and Tkinter, matching WINDOW_WIDTH x
    WINDOW_HEIGHT.  Called once at startup, before the GUI is created.
    """
    if not sys.platform.startswith("linux"):
        return
    if not _is_command_available("xrandr"):
        return

    # Already in portrait — nothing to do.
    if _find_small_display_xy(WINDOW_WIDTH, WINDOW_HEIGHT) is not None:
        log.info("Small display already in portrait orientation (%dx%d).", WINDOW_WIDTH, WINDOW_HEIGHT)
        return

    # Look for the display in landscape (native 800x480 when we want 480x800).
    displays = _parse_xrandr_connected()
    for name, w, h, x, y in displays:
        if w == WINDOW_HEIGHT and h == WINDOW_WIDTH:
            log.info(
                "Display %s found in landscape (%dx%d) — applying portrait rotation.",
                name, w, h,
            )
            try:
                subprocess.run(
                    ["xrandr", "--output", name, "--rotate", "left"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Portrait rotation applied to %s.", name)
                time.sleep(0.8)  # allow the rotation to take effect
            except Exception as e:
                log.warning("xrandr rotation failed for %s: %s", name, e)
            return


class PreviewController(threading.Thread):
    """Handles camera preview in a separate thread"""

    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        """Initialise the preview thread.

        Args:
            port: Serial port of the RealSense ID device (unused by preview
                  itself but kept for symmetry with HostModeService).
            camera_index: UVC camera index (-1 for auto-detect).
            device_type: Device variant reported by rsid_py.discover_device_type.
        """
        super().__init__(daemon=True)
        self.port = port
        self.camera_index = camera_index
        self.device_type = device_type
        self.running = True
        self._paused = False
        self.preview = None
        self.image_queue = queue.Queue(maxsize=2)
        self._restart_event = threading.Event()
        self._pause_event = threading.Event()
        self._paused_ack = threading.Event()

    def on_image(self, image):
        """Callback invoked by rsid_py for each decoded preview frame.

        Converts the raw buffer to a NumPy RGB array and drops it into
        image_queue (max depth 2 — oldest frame is discarded if the GUI
        thread is falling behind).
        """
        if not self.running:
            return
        try:
            buffer = memoryview(image.get_buffer())
            arr = np.asarray(buffer, dtype=np.uint8)
            array2d = arr.reshape((image.height, image.width, -1))
            if self.image_queue.full():
                try:
                    self.image_queue.get_nowait()
                except queue.Empty:
                    pass
            self.image_queue.put(array2d.copy())
        except Exception:
            log.exception("Error in preview frame callback")

    def start_preview(self):
        """Create a PreviewConfig and start the UVC camera stream.

        Called once on thread start and again by resume() after authentication
        releases the camera.
        """
        preview_cfg = rsid_py.PreviewConfig()
        preview_cfg.device_type = self.device_type
        preview_cfg.camera_number = self.camera_index
        preview_cfg.preview_mode = rsid_py.PreviewMode.MJPEG_1080P

        self.preview = rsid_py.Preview(preview_cfg)
        self.preview.start(preview_callback=self.on_image, snapshot_callback=None)

    # Previews whose USB device disconnected mid-stream: calling stop() on them
    # crashes (libusb mutex already destroyed by the disconnect handler). We keep
    # a reference here so Python's GC never calls __del__ → stop() on them.
    # The OS reclaims the resources when the process exits.
    _dead_previews: list = []

    def run(self):
        """Thread entry point — starts the preview stream then idles until stop() is called."""
        self.start_preview()
        while self.running:
            # Handle pause request from auth thread (set _paused_ack when done).
            if self._pause_event.is_set():
                self._pause_event.clear()
                if self.preview is not None:
                    try:
                        self.preview.stop()
                        self.preview = None
                        log.info("Preview paused for authentication")
                    except Exception as e:
                        log.warning("Preview pause failed: %s", e)
                self._paused_ack.set()

            if self._restart_event.wait(0.05):
                self._restart_event.clear()
                if self.preview:
                    # Do NOT call self.preview.stop() — the USB device already
                    # disconnected, so libusb's internal mutex is gone. Calling
                    # stop() here would hit a pthread_mutex_destroy assertion
                    # and abort the process. Park the dead object instead.
                    PreviewController._dead_previews.append(self.preview)
                    self.preview = None
                time.sleep(1.5)  # let the re-enumerated UVC device settle
                # Don't restart if auth intentionally paused the stream.
                if not self._paused:
                    try:
                        self.start_preview()
                        log.info("Preview restarted after reconnect")
                    except Exception as e:
                        log.error("Preview restart failed: %s", e)

        if self.preview:
            self.preview.stop()
            self.preview = None
        log.info("Preview controller thread exited")

    def restart(self):
        """Signal the preview thread to restart the UVC stream (thread-safe)."""
        self._restart_event.set()

    def pause(self):
        """Stop the UVC stream and block until it is fully stopped (max 2 s).

        Must be called before authentication so the camera is free for the
        RealSense firmware. Calling while already paused is safe.
        """
        self._paused = True
        self._paused_ack.clear()
        self._pause_event.set()
        self._paused_ack.wait(timeout=2.0)

    def resume(self):
        """Restart the UVC stream after authentication."""
        self._paused = False
        if self.preview is None and self.running:
            try:
                self.start_preview()
                log.info("Preview resumed after authentication")
            except Exception as e:
                log.error("Preview resume failed: %s", e)

    def stop(self):
        """Signal the preview thread to exit its run loop and stop the UVC stream."""
        self.running = False
        self._paused_ack.set()  # unblock any pause() call waiting on ack


class HostModeService:
    """Business logic for host mode authentication"""

    def __init__(self, port: str):
        """Connect to the RealSense ID device and initialise the Wiegand transmitter.

        Args:
            port: Serial port path (e.g. '/dev/ttyACM0').
        """
        self.port = port
        self.user_db = UserDatabase()
        self._error_backoff_until = 0.0  # epoch time; auth is blocked until this passes
        self.on_reconnect = None  # optional callback fired after successful reconnect

        self._authenticator = rsid_py.FaceAuthenticator(port)
        try:
            self._authenticator.connect(self.port)
            log.info("FaceAuthenticator connected")
        except Exception as e:
            log.error("FaceAuthenticator connect failed: %s", e)

        try:
            initialize_wiegand_tx()
            log.info("Wiegand transmitter initialized")
        except Exception as e:
            log.warning("Wiegand initialization failed: %s", e)

    def _reconnect(self):
        """Reset the serial connection after an error, with retries and backoff.

        After a USB disconnect the device may re-enumerate on a different ACM
        port (e.g. ttyACM1 → ttyACM0), so we use whatever discover_devices()
        returns rather than insisting on the original port.
        """
        try:
            self._authenticator.disconnect()
        except Exception:
            pass

        delay = 1
        attempt = 0
        while True:
            time.sleep(delay)
            attempt += 1
            devices = []
            try:
                devices = rsid_py.discover_devices()
            except Exception:
                pass

            if not devices:
                log.warning("Reconnect attempt %d: no devices found (retry in %ds)", attempt, min(delay * 2, 16))
                delay = min(delay * 2, 16)
                continue

            new_port = devices[0]
            if new_port != self.port:
                log.info("Device re-enumerated on %s (was %s)", new_port, self.port)
                self.port = new_port

            try:
                self._authenticator.connect(self.port)
                log.info("FaceAuthenticator reconnected on %s after %d attempt(s)", self.port, attempt)
                self._error_backoff_until = 0.0
                if self.on_reconnect:
                    self.on_reconnect()
                return
            except Exception as e:
                log.error("Reconnect attempt %d failed: %s (retry in %ds)", attempt, e, min(delay * 2, 16))
                delay = min(delay * 2, 16)

    def authenticate_with_card(self, card_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate using a Wiegand card ID combined with a live face scan.

        Looks up the card ID in the local DB, extracts a live faceprint from the
        camera, and matches it against the stored faceprint. On success, fires
        send_w32 and optionally opens the relay.

        Returns:
            (success, user_name, permission_level_or_error_message)
        """
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            return False, None, "Card not registered"

        result = [None]

        def on_fp_auth_result(status, new_prints):
            if status != rsid_py.AuthenticateStatus.Success or not new_prints:
                result[0] = (False, None, f"Face extraction failed: {status}")
                return

            fp = user_info.get('faceprints')
            if not fp:
                result[0] = (False, None, "No faceprints on file")
                return

            db_faceprints = rsid_py.Faceprints()
            db_faceprints.version = fp['version']
            db_faceprints.features_type = fp['features_type']
            db_faceprints.flags = fp['flags']
            db_faceprints.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
            db_faceprints.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
            db_faceprints.enroll_descriptor = fp['enroll_descriptor']

            updated_faceprints = rsid_py.Faceprints()
            match_result = self._authenticator.match_faceprints(
                new_prints, db_faceprints, updated_faceprints
            )

            if match_result.success or (match_result.score is not None and match_result.score >= CUSTOM_THRESHOLD):
                send_w32(card_id)
                if RUN_WITH_RELAY:
                    threading.Thread(target=open_door, args=(3.0,), daemon=True).start()
                result[0] = (True, user_info['name'], user_info['permission_level'])
            else:
                result[0] = (False, None, f"Face match failed (score: {match_result.score})")

        try:
            self._authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
            if result[0] is None:
                return False, None, "Authentication callback not invoked"
            return result[0]
        except Exception as e:
            self._reconnect()
            return False, None, str(e)

    def authenticate_all_users(self) -> tuple[bool, Optional[str], Optional[str]]:
        """Extract a live faceprint and match it against every user in the DB.

        The highest-scoring match above CUSTOM_THRESHOLD is selected. On
        success, fires send_w32 with the winning user's numeric ID and
        optionally opens the relay.

        Returns:
            (success, user_name, permission_level_or_error_message)
        """
        remaining = self._error_backoff_until - time.monotonic()
        if remaining > 0:
            log.debug("Serial backoff active — skipping auth (%.0fs remaining)", remaining)
            return False, None, "Device recovering"

        all_users = self.user_db.get_all_users()
        if not all_users:
            return False, None, "No users in database"

        result = [None]

        def on_fp_auth_result(status, new_prints):
            if status != rsid_py.AuthenticateStatus.Success or not new_prints:
                result[0] = (False, None, f"Face extraction failed: {status}")
                return

            max_score = -100
            selected_user_id = None
            selected_user_info = None

            for user_id, user_info in all_users.items():
                fp = user_info.get('faceprints')
                if not fp:
                    continue

                try:
                    db_faceprints = rsid_py.Faceprints()
                    db_faceprints.version = fp['version']
                    db_faceprints.features_type = fp['features_type']
                    db_faceprints.flags = fp['flags']
                    db_faceprints.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
                    db_faceprints.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
                    db_faceprints.enroll_descriptor = fp['enroll_descriptor']

                    updated_faceprints = rsid_py.Faceprints()
                    match_result = self._authenticator.match_faceprints(
                        new_prints, db_faceprints, updated_faceprints
                    )
                except Exception as e:
                    log.warning("Skipping user %s: bad faceprints (%s)", user_id, e)
                    continue

                is_match = match_result.success or (
                    match_result.score is not None and match_result.score >= CUSTOM_THRESHOLD
                )
                if is_match and match_result.score > max_score:
                    max_score = match_result.score
                    selected_user_id = user_id
                    selected_user_info = user_info

            if selected_user_id:
                try:
                    send_w32(int(selected_user_id))
                except (ValueError, TypeError):
                    pass  # non-numeric user ID (e.g. MongoDB ObjectId), skip Wiegand send
                if RUN_WITH_RELAY:
                    threading.Thread(target=open_door, args=(3.0,), daemon=True).start()
                result[0] = (True, selected_user_info['name'], selected_user_info['permission_level'])
            else:
                result[0] = (False, None, "No match found")

        try:
            self._authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
            if result[0] is None:
                return False, None, "Authentication callback not invoked"
            return result[0]
        except Exception as e:
            log.exception("authenticate_all_users error")
            # Block further auth attempts for 20s while reconnect runs in background
            self._error_backoff_until = time.monotonic() + 20.0
            threading.Thread(target=self._reconnect, daemon=True).start()
            return False, None, str(e)

    def reload_db(self):
        """Reload the user database from disk.

        Called after a remote sync writes updated records so that the next
        authentication attempt uses fresh data.
        """
        self.user_db.load()
        log.info("Auth DB reloaded (%d users)", self.user_db.count())

    def cleanup(self):
        """Disconnect from the device and release card-reader / Wiegand resources."""
        try:
            self._authenticator.disconnect()
        except Exception:
            pass
        try:
            if RUN_WITH_CARD_READER:
                disconnect_card_reader()
                close_wiegand_tx()
        except Exception:
            pass




class GUI(tk.Tk):
    """Main Tkinter GUI window"""

    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        """Build the Tkinter window, wire up all services, and start background loops.

        Args:
            port: Serial port of the RealSense ID device.
            camera_index: UVC camera index (-1 for auto-detect).
            device_type: Device variant used to configure the preview stream.
        """
        super().__init__(className=WINDOW_NAME)

        self.port = port
        self.image = None
        self.scaled_image = None
        self.video_update_handle = None
        self.result_hide_handle = None
        self.auto_auth_handle = None
        self.running = True
        self.db_sync_in_progress = False
        self.auth_in_progress = False


        # Initialize services
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        self.host_service.on_reconnect = self.preview_controller.restart

        # Authentication state
        self.auth_in_progress = False

        # Card reader thread
        self.card_reader_thread = None
        if RUN_WITH_CARD_READER:
            self.card_reader_thread = threading.Thread(target=self._card_reader_loop, daemon=True)

        # Window setup
        if RUN_ON_REAL_DEVICE:
            self.config(cursor="none")
            self._place_on_small_display_strict()
        else:
            max_w = 720
            max_h = 900
            self.geometry(f"{max_w}x{max_h}")
            self.minsize(500, 600)

        self.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.bind('<Escape>', lambda e: self.exit_app())

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        # Video canvas
        self.canvas = tk.Canvas(self, bg='black', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))

        self.canvas_image_id = None
        self.canvas_result_ids = []

        # Button frame (only in WITH_BUTTON mode)
        self.auth_button = None
        if WITH_BUTTON:
            button_frame = ttk.Frame(self)
            button_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(5, 10))
            button_frame.grid_columnconfigure(0, weight=1)

            style = ttk.Style(self)
            if sys.platform.startswith('win'):
                style.theme_use('vista')
            else:
                style.theme_use('clam')

            if RUN_ON_REAL_DEVICE:
                style.configure('Big.TButton', font=('Arial', 20, 'bold'), padding=(20, 30))
            else:
                style.configure('Big.TButton', font=('Arial', 28, 'bold'), padding=(30, 40))

            self.auth_button = ttk.Button(
                button_frame,
                text="Authenticate",
                command=self.authenticate,
                style='Big.TButton'
            )
            if RUN_ON_REAL_DEVICE:
                self.auth_button.grid(row=0, column=0, sticky="ew", ipady=20)
            else:
                self.auth_button.grid(row=0, column=0, sticky="ew", ipady=30)

        # --- DB Sync ---
        self._start_db_sync()
        self.schedule_db_sync()

        # Start preview
        self.preview_controller.start()

        # Start card reader thread if enabled
        if RUN_WITH_CARD_READER and self.card_reader_thread:
            self.card_reader_thread.start()
            log.info("Card reader monitoring started")

        # Start loops
        self.after(50, self.update_video)
        self.after(200, self.update_app_icon)

        # Re-assert placement after window exists and keep asserting it so
        # the WM or an HDMI reconnect can't push the window to the primary.
        if RUN_ON_REAL_DEVICE:
            self.after(300, self._keep_on_small_display)

        # Start auto-auth loop if button is disabled
        if not WITH_BUTTON:
            self.schedule_auto_auth()
    # =====================================================
    # DB SYNC
    # =====================================================

    def schedule_db_sync(self):
        """Queue the next DB sync tick after DB_SYNC_INTERVAL_SEC seconds."""
        if not self.running:
            return

        self.after(DB_SYNC_INTERVAL_SEC * 1000, self._db_sync_tick)

    def _db_sync_tick(self):
        """Timer callback: kick off a sync if no auth is running, then reschedule."""
        if self.auth_in_progress:
            log.debug("Skipping DB sync (authentication running)")
        elif not self.db_sync_in_progress:
            self._start_db_sync()

        self.schedule_db_sync()

    def _start_db_sync(self):
        """Spawn a background thread for a DB sync if one is not already running."""
        if self.db_sync_in_progress:
            return

        threading.Thread(target=self._run_db_sync, daemon=True).start()

    def _run_db_sync(self):
        """Background thread body: pull remote users and reload auth DB if changed."""
        try:
            self.db_sync_in_progress = True
            log.info("DB sync started")

            updated = sync_remote_users_into_local(USER_DB_FILE)

            if updated > 0:
                log.info("Reloading DB (%d users updated)", updated)
                self.host_service.reload_db()

        except Exception as e:
            log.error("DB sync error: %s", e)

        finally:
            self.db_sync_in_progress = False

    def _wmctrl_force_move_resize(self, x: int, y: int, w: int, h: int):
        """Fallback: force move/resize using wmctrl if available."""
        if not _is_command_available("wmctrl"):
            return
        try:
            # Needs window to have a title and be mapped
            self.update_idletasks()
            time.sleep(0.05)
            title = self.title() or WINDOW_NAME
            # Remove maximized flags first (some WMs ignore move while maximized)
            subprocess.run(["wmctrl", "-r", title, "-b", "remove,maximized_vert,maximized_horz"],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Move/resize: gravity=0,x,y,w,h
            subprocess.run(["wmctrl", "-r", title, "-e", f"0,{x},{y},{w},{h}"],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.warning("wmctrl fallback failed: %s", e)

    def _place_on_small_display_strict(self):
        """
        Put GUI on 800x480 display whenever available.
        Works both with and without external big monitor connected.
        """
        pos = _find_small_display_xy(WINDOW_WIDTH, WINDOW_HEIGHT)

        # Always disable fullscreen (fullscreen tends to jump to primary display)
        try:
            self.attributes("-fullscreen", False)
        except Exception:
            pass

        if pos is not None:
            x, y, out_name = pos
            try:
                self.overrideredirect(KIOSK_BORDERLESS)
            except Exception:
                pass

            try:
                # set title (helps wmctrl target reliably)
                self.title(WINDOW_NAME)
            except Exception:
                pass

            # Set size + position directly
            self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
            self.update_idletasks()
            self.lift()
            log.info("GUI placed on small display %s at %d,%d size %dx%d", out_name, x, y, WINDOW_WIDTH, WINDOW_HEIGHT)

            # Fallback force (handles stubborn WM)
            self._wmctrl_force_move_resize(x, y, WINDOW_WIDTH, WINDOW_HEIGHT)

        else:
            # Portrait display not found — check if it's still in landscape (rotation failed).
            landscape = _find_small_display_xy(WINDOW_HEIGHT, WINDOW_WIDTH)
            if landscape is not None:
                lx, ly, lname = landscape
                log.warning(
                    "Display %s still in landscape (%dx%d) — filling it as-is.",
                    lname, WINDOW_HEIGHT, WINDOW_WIDTH,
                )
                try:
                    self.overrideredirect(KIOSK_BORDERLESS)
                except Exception:
                    pass
                self.geometry(f"{WINDOW_HEIGHT}x{WINDOW_WIDTH}+{lx}+{ly}")
                self.update_idletasks()
                self.lift()
            else:
                # Not found at all — center on primary display.
                log.info("Small display not detected -> centering on primary display.")
                try:
                    self.overrideredirect(KIOSK_BORDERLESS)
                except Exception:
                    pass
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                x = (sw - WINDOW_WIDTH) // 2
                y = (sh - WINDOW_HEIGHT) // 2
                self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
                self.update_idletasks()

    def update_app_icon(self):
        """Generate a minimal 50×50 window icon and apply it to the title bar."""
        icon = Image.new("RGB", (50, 50))
        op = ImageDraw.Draw(icon)
        # PIL text signature in your env may not accept font_size kwarg
        try:
            op.text((10, 0), "R", fill="white")
        except Exception:
            pass
        self.icon = ImageTk.PhotoImage(icon)
        self.wm_iconphoto(False, self.icon)

    def schedule_auto_auth(self):
        """Schedule next automatic authentication (only when WITH_BUTTON=False)."""
        if not self.running or WITH_BUTTON:
            return
        if self.auto_auth_handle:
            self.after_cancel(self.auto_auth_handle)
        self.auto_auth_handle = self.after(int(AUTO_AUTH_INTERVAL_SEC * 1000), self._auto_auth_tick)

    def _auto_auth_tick(self):
        """Called by timer to trigger auth periodically."""
        if not self.running or WITH_BUTTON:
            return
        if not self.auth_in_progress:
            self.authenticate()
        self.schedule_auto_auth()

    def _keep_on_small_display(self):
        """Re-assert window position on the round display every 3 s.

        Uses a lightweight geometry-only nudge so there is no unmap/remap
        flicker. overrideredirect is set once at startup and left alone here.
        """
        if not self.running:
            return
        pos = _find_small_display_xy(WINDOW_WIDTH, WINDOW_HEIGHT)
        if pos is not None:
            x, y, _ = pos
            if self.winfo_x() != x or self.winfo_y() != y:
                self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
                self.update_idletasks()
        self.after(3000, self._keep_on_small_display)

    def update_video(self):
        """Drain the latest frame from image_queue, scale it to fit the canvas, and display it.

        Schedules itself every 30 ms (~33 fps) while the preview thread is running.
        Older frames are dropped so the display always shows the most recent one.
        """
        self.update_idletasks()

        if not self.preview_controller.image_queue.empty() and self.preview_controller.running:
            array2d = None
            while not self.preview_controller.image_queue.empty():
                array2d = self.preview_controller.image_queue.get()
            if array2d is not None:
                try:
                    self.image = Image.fromarray(array2d, mode="RGB")
                except Exception:
                    pass

        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if self.image is not None and canvas_w > 1 and canvas_h > 1:
            image = self.image.copy()

            ZOOM_OUT = 0.2
            img = image
            if ZOOM_OUT < 1.0:
                w, h = img.size
                nw, nh = int(w * ZOOM_OUT), int(h * ZOOM_OUT)
                img = img.resize((nw, nh), Image.BICUBIC)

            scaled_image = ImageOps.fit(
                img,
                size=(canvas_w, canvas_h),
                method=Image.BICUBIC,
                centering=(0.5, 0.5)
            ).transpose(Image.FLIP_LEFT_RIGHT)

            self.scaled_image = ImageTk.PhotoImage(image=scaled_image)

            if self.canvas_image_id is None:
                self.canvas_image_id = self.canvas.create_image(
                    canvas_w // 2, canvas_h // 2,
                    anchor=tk.CENTER,
                    image=self.scaled_image
                )
            else:
                self.canvas.itemconfig(self.canvas_image_id, image=self.scaled_image)
                self.canvas.coords(self.canvas_image_id, canvas_w // 2, canvas_h // 2)

        if self.preview_controller.running:
            self.video_update_handle = self.after(30, self.update_video)

    def show_result(self, success: bool, name: Optional[str] = None):
        """Show result overlay for 3 seconds.
        On success with a name: shows WELCOME / FULL NAME.
        Otherwise: shows ✓ or ✗.
        """
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        for item_id in self.canvas_result_ids:
            self.canvas.delete(item_id)
        self.canvas_result_ids = []

        cx = canvas_w // 2
        cy = canvas_h // 2

        if success and name:
            box_w = int(canvas_w * 0.88)
            box_h = 180 if RUN_ON_REAL_DEVICE else 220
            x1 = (canvas_w - box_w) // 2
            y1 = (canvas_h - box_h) // 2
            x2 = x1 + box_w
            y2 = y1 + box_h

            bg = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill='black', stipple='gray50', outline=''
            )

            welcome_size = 28 if RUN_ON_REAL_DEVICE else 36
            name_size = 40 if RUN_ON_REAL_DEVICE else 64
            color = '#4CAF50'
            gap = 38 if RUN_ON_REAL_DEVICE else 48

            welcome_text = self.canvas.create_text(
                cx, cy - gap,
                text="WELCOME",
                font=('Arial', welcome_size, 'bold'),
                fill=color
            )
            name_text = self.canvas.create_text(
                cx, cy + gap,
                text=name.upper(),
                font=('Arial', name_size, 'bold'),
                fill=color
            )
            self.canvas_result_ids = [bg, welcome_text, name_text]

        else:
            box_size = 300 if RUN_ON_REAL_DEVICE else 400
            x1 = (canvas_w - box_size) // 2
            y1 = (canvas_h - box_size) // 2
            x2 = x1 + box_size
            y2 = y1 + box_size

            bg = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill='black', stipple='gray50', outline=''
            )

            symbol = "✓" if success else "✗"
            color = '#4CAF50' if success else '#F44336'
            font_size = 150 if RUN_ON_REAL_DEVICE else 200

            sym = self.canvas.create_text(
                cx, cy,
                text=symbol,
                font=('Arial', font_size, 'bold'),
                fill=color
            )
            self.canvas_result_ids = [bg, sym]

        if self.result_hide_handle:
            self.after_cancel(self.result_hide_handle)
        duration_ms = WELCOME_DURATION_MS if success else FAIL_DURATION_MS
        self.result_hide_handle = self.after(duration_ms, self.hide_result)

    def hide_result(self):
        """Remove all result overlay canvas items (called automatically after the display duration)."""
        for item_id in self.canvas_result_ids:
            self.canvas.delete(item_id)
        self.canvas_result_ids = []

    def authenticate(self):
        """Kick off an authentication cycle in a background thread.

        Guards against concurrent calls via auth_in_progress. Disables the
        button (if visible) for the duration.
        """
        if self.auth_in_progress:
            return

        self.auth_in_progress = True
        if self.auth_button is not None:
            self.auth_button.state(['disabled'])

        auth_thread = threading.Thread(target=self._run_authentication, daemon=True)
        auth_thread.start()

    def _run_authentication(self):
        """Background thread body: pause preview, run face auth, then resume preview.

        Posts the result back to the main thread via self.after().
        """
        self.preview_controller.pause()
        try:
            success, name, permission = self.host_service.authenticate_all_users()
            if success:
                log.info("Access granted: %s (%s)", name, permission)
            else:
                log.warning("Access denied: %s", permission)

            self.after(0, lambda: self._on_auth_complete(success, name))

        except Exception as e:
            log.error("Authentication error: %s", e)
            self.after(0, lambda: self._on_auth_complete(False, None))
        finally:
            self.preview_controller.resume()

    def _on_auth_complete(self, success: bool, name: Optional[str] = None):
        """Update UI after authentication finishes (always called on the main thread).

        Re-enables the auth button and shows the result overlay on success
        (or on any outcome when WITH_BUTTON mode is active).
        """
        self.auth_in_progress = False
        if self.auth_button is not None:
            self.auth_button.state(['!disabled'])
        if WITH_BUTTON:
            self.show_result(success, name)
        else:
            if success:
                self.show_result(True, name)

    def _card_reader_loop(self):
        """Background thread: poll the Wiegand card reader and trigger authentication on a new card tap.

        Enforces a 2-second cooldown between consecutive reads of the same card
        to avoid duplicate auth attempts. Skips reads while an auth is already
        in progress.
        """
        log.info("Card reader monitoring active")
        last_card_id = None
        card_cooldown = 2.0
        last_read_time = 0

        while self.running:
            try:
                card_id = get_card_id(timeout=0.5)
                if RUN_WITH_CARD_READER and SIMULATE_HW:
                    log.debug("[Card Reader] Read card ID: %s", card_id)
                    card_id = 1230007405

                if card_id is not None:
                    current_time = time.time()

                    if card_id == last_card_id and (current_time - last_read_time) < card_cooldown:
                        continue

                    if self.auth_in_progress:
                        continue

                    log.info("Card detected: %s", card_id)
                    self.auth_in_progress = True
                    self.preview_controller.pause()

                    success, user_name, permission = self.host_service.authenticate_with_card(card_id)

                    self.preview_controller.resume()
                    if SIMULATE_HW:
                        time.sleep(5)

                    if success:
                        log.info("Access granted to %s (%s)", user_name, permission)
                    else:
                        log.warning("Access denied for card %s: %s", card_id, permission)

                    self.after(0, lambda s=success, n=user_name: self._on_auth_complete(s, n))

                    last_card_id = card_id
                    last_read_time = current_time

            except Exception as e:
                log.error("Card reader error: %s", e)
                time.sleep(1)

        log.info("Card reader monitoring stopped")

    def exit_app(self):
        """Gracefully shut down all threads and services, then destroy the window."""
        self.running = False

        if self.video_update_handle:
            self.after_cancel(self.video_update_handle)
        if self.result_hide_handle:
            self.after_cancel(self.result_hide_handle)
        if self.auto_auth_handle:
            self.after_cancel(self.auto_auth_handle)
            self.auto_auth_handle = None

        self.preview_controller.stop()
        self.host_service.cleanup()

        if RUN_WITH_RELAY:
            try:
                disconnect_relay()
            except Exception:
                pass

        self.quit()


def main():
    """Entry point: parse CLI args, discover and configure the device, then run the GUI."""
    parser = argparse.ArgumentParser(prog='host_mode_gui_tk', description='RealSense ID Host Mode GUI (Tkinter)')
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
            config = copy.copy(f.query_device_config())
            config.dump_mode = rsid_py.DumpMode.Disable
            f.set_device_config(config)
            log.info("Device configured successfully")
        except Exception as e:
            log.exception("Device configuration error: %s", e)
            os._exit(1)
        finally:
            f.disconnect()

    log.info("Using port: %s (%s)", port, device_type)
    log.info("Using camera index: %d", camera_index)

    # Ensure the Waveshare display is rotated to portrait before the GUI starts.
    if RUN_ON_REAL_DEVICE:
        _setup_display_rotation()

    # Initialize card reader
    if RUN_WITH_CARD_READER:
        initialize_card_reader()
        log.info("Card reader initialized")

    if RUN_WITH_RELAY:
        initialize_relay(relay_pin=RELAY_PIN, active_low=RELAY_ACTIVE_LOW, default_off=RELAY_DEFAULT_OFF)
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
