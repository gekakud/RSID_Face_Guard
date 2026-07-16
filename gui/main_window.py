"""
Main Tkinter GUI window for RealSense ID Host Mode.

Contains only display/video/result-overlay logic and wiring to the
hardware / business-logic services -- no device or hardware code lives
here directly.
"""

import logging
import sys
import threading
import time
from typing import Optional

from PIL import Image, ImageDraw, ImageOps, ImageTk
import tkinter as tk
import tkinter.ttk as ttk

import config
from face_auth import HostModeService
from hardware.camera_preview import PreviewController
from hardware.card_reader_api import get_card_id

from .display_utils import (
    find_small_display_xy,
    wmctrl_force_move_resize,
)

log = logging.getLogger("face_guard")

WINDOW_NAME = 'RealSenseID Host Mode'


class GUI(tk.Tk):
    """Main Tkinter GUI window."""

    def __init__(self, port: str, camera_index: int, device_type):
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
        if config.RUN_WITH_CARD_READER:
            self.card_reader_thread = threading.Thread(target=self._card_reader_loop, daemon=True)

        # Window setup
        if config.RUN_ON_REAL_DEVICE:
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
        if config.WITH_BUTTON:
            button_frame = ttk.Frame(self)
            button_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(5, 10))
            button_frame.grid_columnconfigure(0, weight=1)

            style = ttk.Style(self)
            if sys.platform.startswith('win'):
                style.theme_use('vista')
            else:
                style.theme_use('clam')

            if config.RUN_ON_REAL_DEVICE:
                style.configure('Big.TButton', font=('Arial', 20, 'bold'), padding=(20, 30))
            else:
                style.configure('Big.TButton', font=('Arial', 28, 'bold'), padding=(30, 40))

            self.auth_button = ttk.Button(
                button_frame,
                text="Authenticate",
                command=self.authenticate,
                style='Big.TButton'
            )
            if config.RUN_ON_REAL_DEVICE:
                self.auth_button.grid(row=0, column=0, sticky="ew", ipady=20)
            else:
                self.auth_button.grid(row=0, column=0, sticky="ew", ipady=30)

        # --- DB Sync ---
        self._start_db_sync()
        self.schedule_db_sync()

        # Start preview
        self.preview_controller.start()

        # Start card reader thread if enabled
        if config.RUN_WITH_CARD_READER and self.card_reader_thread:
            self.card_reader_thread.start()
            log.info("Card reader monitoring started")

        # Start loops
        self.after(50, self.update_video)
        self.after(200, self.update_app_icon)

        # Re-assert placement after window exists and keep asserting it so
        # the WM or an HDMI reconnect can't push the window to the primary.
        if config.RUN_ON_REAL_DEVICE:
            self.after(300, self._keep_on_small_display)

        # Start auto-auth loop if button is disabled
        if not config.WITH_BUTTON:
            self.schedule_auto_auth()

    # =====================================================
    # DB SYNC
    # =====================================================

    def schedule_db_sync(self):
        """Queue the next DB sync tick after DB_SYNC_INTERVAL_SEC seconds."""
        if not self.running:
            return

        self.after(config.DB_SYNC_INTERVAL_SEC * 1000, self._db_sync_tick)

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

            updated = self.host_service.sync_db_from_remote()

            if updated > 0:
                log.info("Reloading DB (%d users updated)", updated)
                self.host_service.reload_db()

        except Exception as e:
            log.error("DB sync error: %s", e)

        finally:
            self.db_sync_in_progress = False

    # =====================================================
    # DISPLAY PLACEMENT
    # =====================================================

    def _place_on_small_display_strict(self):
        """
        Put GUI on 800x480 display whenever available.
        Works both with and without external big monitor connected.
        """
        pos = find_small_display_xy(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        # Always disable fullscreen (fullscreen tends to jump to primary display)
        try:
            self.attributes("-fullscreen", False)
        except Exception:
            pass

        if pos is not None:
            x, y, out_name = pos
            try:
                self.overrideredirect(config.KIOSK_BORDERLESS)
            except Exception:
                pass

            try:
                # set title (helps wmctrl target reliably)
                self.title(WINDOW_NAME)
            except Exception:
                pass

            # Set size + position directly
            self.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}+{x}+{y}")
            self.update_idletasks()
            self.lift()
            log.info("GUI placed on small display %s at %d,%d size %dx%d",
                      out_name, x, y, config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

            # Fallback force (handles stubborn WM)
            wmctrl_force_move_resize(self, x, y, config.WINDOW_WIDTH, config.WINDOW_HEIGHT, WINDOW_NAME)

        else:
            # Not found at all -- center on primary display.
            log.info("Small display not detected -> centering on primary display.")
            try:
                self.overrideredirect(config.KIOSK_BORDERLESS)
            except Exception:
                pass
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = (sw - config.WINDOW_WIDTH) // 2
            y = (sh - config.WINDOW_HEIGHT) // 2
            self.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}+{x}+{y}")
            self.update_idletasks()

    def update_app_icon(self):
        """Generate a minimal 50x50 window icon and apply it to the title bar."""
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
        if not self.running or config.WITH_BUTTON:
            return
        if self.auto_auth_handle:
            self.after_cancel(self.auto_auth_handle)
        self.auto_auth_handle = self.after(int(config.AUTO_AUTH_INTERVAL_SEC * 1000), self._auto_auth_tick)

    def _auto_auth_tick(self):
        """Called by timer to trigger auth periodically."""
        if not self.running or config.WITH_BUTTON:
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
        pos = find_small_display_xy(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        if pos is not None:
            x, y, _ = pos
            if self.winfo_x() != x or self.winfo_y() != y:
                self.geometry(f"{config.WINDOW_WIDTH}x{config.WINDOW_HEIGHT}+{x}+{y}")
                self.update_idletasks()
        self.after(3000, self._keep_on_small_display)

    # =====================================================
    # VIDEO / RESULT OVERLAY
    # =====================================================

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
        Otherwise: shows checkmark or X.
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
            box_h = 180 if config.RUN_ON_REAL_DEVICE else 220
            x1 = (canvas_w - box_w) // 2
            y1 = (canvas_h - box_h) // 2
            x2 = x1 + box_w
            y2 = y1 + box_h

            bg = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill='black', stipple='gray50', outline=''
            )

            welcome_size = 28 if config.RUN_ON_REAL_DEVICE else 36
            name_size = 40 if config.RUN_ON_REAL_DEVICE else 64
            color = '#4CAF50'
            gap = 38 if config.RUN_ON_REAL_DEVICE else 48

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
            box_size = 300 if config.RUN_ON_REAL_DEVICE else 400
            x1 = (canvas_w - box_size) // 2
            y1 = (canvas_h - box_size) // 2
            x2 = x1 + box_size
            y2 = y1 + box_size

            bg = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill='black', stipple='gray50', outline=''
            )

            symbol = "OK" if success else "X"
            color = '#4CAF50' if success else '#F44336'
            font_size = 150 if config.RUN_ON_REAL_DEVICE else 200

            sym = self.canvas.create_text(
                cx, cy,
                text=symbol,
                font=('Arial', font_size, 'bold'),
                fill=color
            )
            self.canvas_result_ids = [bg, sym]

        if self.result_hide_handle:
            self.after_cancel(self.result_hide_handle)
        duration_ms = config.WELCOME_DURATION_MS if success else config.FAIL_DURATION_MS
        self.result_hide_handle = self.after(duration_ms, self.hide_result)

    def hide_result(self):
        """Remove all result overlay canvas items (called automatically after the display duration)."""
        for item_id in self.canvas_result_ids:
            self.canvas.delete(item_id)
        self.canvas_result_ids = []

    # =====================================================
    # AUTHENTICATION
    # =====================================================

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
        if config.WITH_BUTTON:
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
                if config.SIMULATE_HW:
                    log.debug("[Card Reader] Read card ID: %s", card_id)

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
                    if config.SIMULATE_HW:
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

        if config.RUN_WITH_RELAY:
            try:
                from hardware.relay_api import disconnect_relay
                disconnect_relay()
            except Exception:
                pass

        self.quit()