"""
Main PySide6 (Qt6) GUI window for RealSense ID Host Mode.

Session-based flow: the camera preview is OFF while idle and only turns on
for a bounded auth "session" -- triggered by a click anywhere on the window
(AUTH_ONLY_ON_CARD=False) or a valid card tap (AUTH_ONLY_ON_CARD=True).
During a session, face-match is retried every AUTH_RETRY_INTERVAL_SEC until
either a match succeeds or AUTH_SESSION_TIMEOUT_SEC elapses, at which point
the preview stops and the UI silently returns to idle. This avoids the
periodic camera-restart stutter of a fixed-interval always-on auto-auth
design (RealSense hardware can't stream preview and authenticate at once,
so any auth attempt briefly restarts the UVC stream -- restricting attempts
to actual user/card-initiated sessions keeps the idle screen glitch-free).

Reuses the same business/hardware layers (HostModeService, PreviewController,
config) unchanged.
"""

import logging
import threading
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QPixmap, QFont, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QSizePolicy,
)

import config
from face_auth import HostModeService
from hardware.camera_preview import PreviewController
from provisioning.binding import BindingManager
from qr_scanner import QRScanner

from .display_utils_qt import find_small_display_geometry

from observability import events
from observability import storage_monitor
from observability.logging_setup import get_logger

log = get_logger("gui")

WINDOW_NAME = "RealSenseID Host Mode (Qt)"

class _SignalBridge(QObject):
    """Marshals callbacks from background threads (HostModeService,
    PreviewController) onto the Qt main thread via signals."""
    auth_result = Signal(bool, object)  # success, name
    card_detected = Signal(object)      # card_id
    card_rejected = Signal(object)      # card_id (unregistered)
    binding_result = Signal(bool, str)  # success, message (device provisioning)
    device_revoked = Signal()           # server removed this device

class ResultOverlay(QWidget):
    """Semi-transparent overlay drawn on top of the video canvas showing
    WELCOME/name on success or a check/X symbol otherwise."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._visible_content = False
        self._success = False
        self._name: Optional[str] = None
        self.hide()

    def show_result(self, success: bool, name: Optional[str] = None):
        self._success = success
        self._name = name
        self._visible_content = True
        self.show()
        self.raise_()
        self.update()

    def hide_result(self):
        self._visible_content = False
        self.hide()

    def paintEvent(self, event):
        if not self._visible_content:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        if self._success and self._name:
            box_w = int(w * 0.88)
            box_h = 180 if config.RUN_ON_REAL_SCREEN else 220
            x1 = (w - box_w) // 2
            y1 = (h - box_h) // 2
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y1, box_w, box_h)

            welcome_size = 28 if config.RUN_ON_REAL_SCREEN else 36
            name_size = 40 if config.RUN_ON_REAL_SCREEN else 64
            gap = 38 if config.RUN_ON_REAL_SCREEN else 48
            color = QColor("#4CAF50")
            painter.setPen(color)

            painter.setFont(QFont("Arial", welcome_size, QFont.Weight.Bold))
            painter.drawText(0, cy - gap - welcome_size, w, welcome_size * 2,
                              Qt.AlignmentFlag.AlignCenter, "WELCOME")

            painter.setFont(QFont("Arial", name_size, QFont.Weight.Bold))
            painter.drawText(0, cy + gap - name_size, w, name_size * 2,
                              Qt.AlignmentFlag.AlignCenter, self._name.upper())
        else:
            box_size = 300 if config.RUN_ON_REAL_SCREEN else 400
            x1 = (w - box_size) // 2
            y1 = (h - box_size) // 2
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y1, box_size, box_size)

            symbol = "OK" if self._success else "X"
            color = QColor("#4CAF50") if self._success else QColor("#F44336")
            font_size = 150 if config.RUN_ON_REAL_SCREEN else 200

            painter.setPen(color)
            painter.setFont(QFont("Arial", font_size, QFont.Weight.Bold))
            painter.drawText(0, cy - font_size, w, font_size * 2,
                              Qt.AlignmentFlag.AlignCenter, symbol)

class StatusOverlay(QWidget):
    """Simple centered text overlay used for init-mode / maintenance-mode
    status messages ("Init Mode", "Configuring...", etc.)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text = ""
        self.hide()

    def show_text(self, text: str):
        self._text = text
        self.show()
        self.raise_()
        self.update()

    def hide_text(self):
        self._text = ""
        self.hide()

    def paintEvent(self, event):
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        box_w = int(w * 0.9)
        box_h = 120 if config.RUN_ON_REAL_SCREEN else 160
        x1 = (w - box_w) // 2
        y1 = (h - box_h) // 2
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(x1, y1, box_w, box_h)

        font_size = 22 if config.RUN_ON_REAL_SCREEN else 30
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Arial", font_size, QFont.Weight.Bold))
        painter.drawText(0, y1, w, box_h, Qt.AlignmentFlag.AlignCenter, self._text)

class GUIQt(QMainWindow):
    """Main PySide6 window."""

    def __init__(self, port: str, camera_index: int, device_type):
        super().__init__()
        self.setWindowTitle(WINDOW_NAME)

        self.port = port
        self.auth_in_progress = False
        self.running = True
        self._shutting_down = False
        self._auth_thread = None
        self._cleaned_up = False

        self._bridge = _SignalBridge()
        self._bridge.auth_result.connect(self._on_auth_complete)
        self._bridge.card_detected.connect(self._on_card_detected)
        self._bridge.card_rejected.connect(self._on_card_rejected)
        self._bridge.binding_result.connect(self._on_binding_result)
        self._bridge.device_revoked.connect(self._on_device_revoked)

        # Services (shared, GUI-agnostic layers)
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        self.host_service.on_reconnect = self.preview_controller.restart

        # Session state: the preview only runs while an auth session is
        # active (started by a click or a valid card read), never while
        # idle -- avoids the periodic camera-restart stutter of a fixed
        # interval always-on preview/auto-auth design.
        self._session_active = False
        self._session_card_id = None
        self.retry_timer = None
        self.session_timeout_timer = None

        # Init mode: brief technician-QR scanning window shown on startup,
        # before falling into the normal idle/session flow. See config.py's
        # INIT_MODE_ENABLED/INIT_MODE_DURATION_SEC.
        self._init_mode_active = False
        self.init_mode_timer = None
        self._qr_scanner = QRScanner()
        # Unlike gui_web (which stops its scan timer), QR scanning here runs
        # inside _update_video and keeps firing, so this guards against a second
        # frame starting a second registration with the same one-time token.
        self._binding_in_progress = False

        # Central widget / layout
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)

        self.video_label = QLabel(central)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_label, stretch=1)

        self.result_overlay = ResultOverlay(self.video_label)
        self.status_overlay = StatusOverlay(self.video_label)

        # Window placement
        if config.RUN_ON_REAL_SCREEN:
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._place_on_small_display()
        else:
            self.resize(720, 900)
            self.setMinimumSize(500, 600)

        # Video render timer -- only meaningful while preview is running
        # (paints nothing when the queue is empty, negligible cost while idle).
        self.video_timer = QTimer(self)
        self.video_timer.timeout.connect(self._update_video)
        self.video_timer.start(30)

        # Periodic disk-space check (independent of the heartbeat interval).
        # Emits storage_low/storage_ok events on threshold crossings; the
        # latest reading also rides every heartbeat via _collect_metadata().
        self._storage_timer = QTimer(self)
        self._storage_timer.timeout.connect(storage_monitor.check_storage)
        self._storage_timer.start(int(config.STORAGE_CHECK_INTERVAL_SEC * 1000))
        storage_monitor.check_storage()  # baseline check at startup

        # Start the preview controller's background thread once (it must stay
        # alive to service pause()/resume() requests). If init mode is
        # enabled, kick off the QR-scanning window with the preview running;
        # otherwise pause immediately and fall straight into the normal idle
        # baseline. Sessions resume()/pause() around this idle baseline --
        # never start()/stop() again until app shutdown.
        self.preview_controller.start()

        if config.AUTH_ONLY_ON_CARD:
            self.host_service.start_card_monitoring(
                on_card_detected=lambda cid: self._bridge.card_detected.emit(cid),
                on_card_rejected=lambda cid: self._bridge.card_rejected.emit(cid),
            )

        # If this device was bound on an earlier run, resume reporting to the
        # dashboard right away -- no QR rescan needed after a reboot.
        self.binding = BindingManager(
            device_type=device_type,
            metadata_fn=self._collect_metadata,
            # Marshalled onto the Qt thread; the heartbeat thread fires this
            # after the identity has already been dropped.
            on_revoked=lambda: self._bridge.device_revoked.emit(),
        )
        self.binding.start_if_bound()

        # Defer until after the window is shown/laid out: video_label.rect()
        # is still a default placeholder size here (0,0 100x30) since Qt
        # hasn't done its first real layout pass yet, so overlay geometry set
        # now would be wrong (pinned tiny in the top-left corner).
        if config.INIT_MODE_ENABLED:
            QTimer.singleShot(0, self.start_init_mode)
        else:
            self.preview_controller.pause()
            QTimer.singleShot(0, self._show_idle_text)

    # =====================================================
    # INIT MODE (technician QR scan on startup)
    # =====================================================

    def start_init_mode(self):
        """Show a brief live preview and scan for a technician QR code.
        Falls back to normal idle behavior if nothing is found in time."""
        self._init_mode_active = True
        events.emit("init_mode_entered")
        self.video_label.clear()
        self.status_overlay.setGeometry(self.video_label.rect())
        self.status_overlay.show_text("Init Mode")
        self.preview_controller.resume()

        self.init_mode_timer = QTimer(self)
        self.init_mode_timer.setSingleShot(True)
        self.init_mode_timer.timeout.connect(self._end_init_mode)
        self.init_mode_timer.start(int(config.INIT_MODE_DURATION_SEC * 1000))

    def _end_init_mode(self):
        if not self._init_mode_active:
            return
        self._init_mode_active = False
        if self.init_mode_timer:
            self.init_mode_timer.stop()
            self.init_mode_timer = None
        self.preview_controller.pause()
        self.video_label.clear()
        self._show_idle_text()
        log.info("Init mode ended -- resuming normal operation")

    def _show_idle_text(self):
        msg = "Tap your card to authenticate" if config.AUTH_ONLY_ON_CARD else "Tap anywhere to authenticate"
        self.status_overlay.setGeometry(self.video_label.rect())
        self.status_overlay.show_text(msg)

    def _collect_metadata(self) -> dict:
        """Snapshot of device state, sent with every heartbeat.

        Called from the heartbeat thread, so it only reads simple attributes --
        no Qt calls, no blocking work.
        """
        return {
            "app_version": getattr(config, "APP_VERSION", "face-guard"),
            "device_type": str(self.preview_controller.device_type),
            "serial_port": self.port,
            "user_count": self.host_service.user_db.count(),
            "camera_available": bool(self.preview_controller.running),
            "relay_available": bool(config.RUN_WITH_RELAY),
            "session_active": bool(self._session_active),
            "init_mode_active": bool(self._init_mode_active),
            "auth_in_progress": bool(self.auth_in_progress),
            "storage": storage_monitor.get_storage_metadata(),
        }


    def _on_binding_result(self, ok: bool, message: str):
        """Binding finished (marshalled onto the Qt thread by _SignalBridge)."""
        self._binding_in_progress = False
        self.status_overlay.show_text(message if ok else f"Registration failed\n{message}")
        # Leave a failure up longer -- an installer needs time to read why.
        QTimer.singleShot(3000 if ok else 6000, self._end_init_mode)

    def _on_device_revoked(self):
        """The dashboard removed this device (marshalled onto the Qt thread).

        The identity has already been dropped by BindingManager; here we just
        tell the operator. Face auth from the local DB keeps working; the device
        can be re-enrolled by scanning a fresh QR.
        """
        log.info("Device was removed from the server; now unbound")
        self.status_overlay.setGeometry(self.video_label.rect())
        self.status_overlay.show_text("Device removed\nRescan a QR to re-enroll")

    def _on_qr_detected(self, payload: dict):
        """A verified provisioning QR was found during init mode -- bind this
        device to the server named in the payload."""
        if not self._init_mode_active or self._binding_in_progress:
            return
        self._binding_in_progress = True
        log.info(
            "Provisioning QR detected during init mode: door_id=%s site_id=%s customer_id=%s",
            payload.get("door_id"), payload.get("site_id"), payload.get("customer_id"),
        )
        # Stop the init-mode timeout immediately so a second frame can't start a
        # second registration with the same (single-use) token.
        if self.init_mode_timer:
            self.init_mode_timer.stop()
            self.init_mode_timer = None

        self.status_overlay.show_text("Binding to server...")
        self.binding.bind_async(
            payload,
            lambda ok, message: self._bridge.binding_result.emit(ok, message),
        )

    # =====================================================
    # DISPLAY PLACEMENT
    # =====================================================

    def _place_on_small_display(self):
        geo = find_small_display_geometry(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        if config.KIOSK_BORDERLESS:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        # Force the window to exactly WINDOW_WIDTH x WINDOW_HEIGHT regardless
        # of layout size hints -- setGeometry() alone can get overridden by
        # layout/window-manager sizing once the window is shown.
        self.setFixedSize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        if geo is not None:
            x, y = geo
            self.move(x, y)
            log.info("Qt GUI placed on small display at %d,%d size %dx%d",
                     x, y, config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        else:
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - config.WINDOW_WIDTH) // 2
            y = (screen.height() - config.WINDOW_HEIGHT) // 2
            self.move(x, y)
            log.info("Small display not detected -> centering Qt GUI on primary display.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.result_overlay.setGeometry(self.video_label.rect())
        self.status_overlay.setGeometry(self.video_label.rect())

    # =====================================================
    # VIDEO
    # =====================================================

    def _update_video(self):
        if not self.preview_controller.running or not (self._session_active or self._init_mode_active):
            return
        array2d = None
        q = self.preview_controller.image_queue
        while not q.empty():
            array2d = q.get()
        if array2d is None:
            return

        if self._init_mode_active and not self._binding_in_progress:
            payload = self._qr_scanner.scan(array2d)
            if payload is not None:
                self._on_qr_detected(payload)

        h, w, ch = array2d.shape
        # Mirror horizontally to match the Tkinter version's FLIP_LEFT_RIGHT.
        array2d = np.ascontiguousarray(array2d[:, ::-1, :])
        qimage = QImage(array2d.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimage)

        label_size = self.video_label.size()
        scaled = pixmap.scaled(
            label_size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        self.video_label.setPixmap(scaled)

    # =====================================================
    # SESSION MANAGEMENT
    # =====================================================

    def mousePressEvent(self, event):
        if not config.AUTH_ONLY_ON_CARD and not self._init_mode_active:
            self.start_session()
        super().mousePressEvent(event)

    def _on_card_detected(self, card_id):
        # Only relevant in AUTH_ONLY_ON_CARD mode -- start_card_monitoring()
        # already filters unregistered cards, so any card_id reaching here
        # is valid and ready for a face-match session.
        self.start_session(card_id=card_id)

    def _on_card_rejected(self, card_id):
        """An unregistered card was tapped: no session/camera is started --
        just show a brief failure message, then return to idle."""
        if self._session_active or self._init_mode_active:
            return
        self.status_overlay.hide_text()
        self.result_overlay.setGeometry(self.video_label.rect())
        self.result_overlay.show_result(False)
        QTimer.singleShot(config.FAIL_DURATION_MS, self.result_overlay.hide_result)
        QTimer.singleShot(config.FAIL_DURATION_MS, self._show_idle_text)

    def start_session(self, card_id=None):
        """Begin a bounded auth session: start the preview, retry face-match
        on an interval, and time out back to idle if nothing matches."""
        if self._session_active or self._shutting_down:
            return
        self._session_active = True
        self._session_card_id = card_id

        if card_id is not None:
            self.host_service.mark_card_session_active()

        self.status_overlay.hide_text()
        self.video_label.clear()
        self.preview_controller.resume()

        self.retry_timer = QTimer(self)
        self.retry_timer.timeout.connect(self._session_auth_tick)
        self.retry_timer.start(int(config.AUTH_RETRY_INTERVAL_SEC * 1000))
        # Fire the first attempt immediately rather than waiting one interval.
        self._session_auth_tick()

        self.session_timeout_timer = QTimer(self)
        self.session_timeout_timer.setSingleShot(True)
        self.session_timeout_timer.timeout.connect(self._session_timeout)
        self.session_timeout_timer.start(int(config.AUTH_SESSION_TIMEOUT_SEC * 1000))

    def _session_auth_tick(self):
        if not self.auth_in_progress and not self._shutting_down:
            self.authenticate()

    def _session_timeout(self):
        if not self._session_active:
            return
        log.info("Auth session timed out with no match -- returning to idle")
        self._end_session()
        self._show_idle_text()

    def _end_session(self):
        if not self._session_active:
            return
        self._session_active = False
        if self.retry_timer:
            self.retry_timer.stop()
            self.retry_timer = None
        if self.session_timeout_timer:
            self.session_timeout_timer.stop()
            self.session_timeout_timer = None
        self.preview_controller.pause()
        self.video_label.clear()
        if self._session_card_id is not None:
            self.host_service.mark_card_session_done()
        self._session_card_id = None

    # =====================================================
    # AUTHENTICATION
    # =====================================================

    def authenticate(self):
        if self.auth_in_progress or self._shutting_down:
            return
        self.auth_in_progress = True

        self._auth_thread = threading.Thread(target=self._run_authentication, daemon=True)
        self._auth_thread.start()

    def _run_authentication(self):
        self.preview_controller.pause()
        try:
            if self._session_card_id is not None:
                success, name, permission = self.host_service.authenticate_with_card(self._session_card_id)
            else:
                success, name, permission = self.host_service.authenticate_face_only()
            if success:
                log.info("Access granted: %s (%s)", name, permission)
            else:
                log.warning("Access denied: %s", permission)
            self._bridge.auth_result.emit(success, name)
        except Exception as e:
            log.error("Authentication error: %s", e)
            self._bridge.auth_result.emit(False, None)
        finally:
            if self._session_active:
                self.preview_controller.resume()

    def _on_auth_complete(self, success: bool, name: Optional[str]):
        self.auth_in_progress = False

        if success:
            # Stop retrying immediately -- otherwise the still-running
            # retry_timer/session_timeout_timer fire again during the welcome
            # hold below (before the delayed _end_session() gets a chance to
            # stop them), triggering a second, unwanted auth attempt.
            if self.retry_timer:
                self.retry_timer.stop()
                self.retry_timer = None
            if self.session_timeout_timer:
                self.session_timeout_timer.stop()
                self.session_timeout_timer = None

            self.result_overlay.setGeometry(self.video_label.rect())
            self.result_overlay.show_result(success, name)
            QTimer.singleShot(config.WELCOME_DURATION_MS, self.result_overlay.hide_result)
            # Successful match ends the session (after the welcome hold), then
            # shows the idle text again so the screen doesn't go black.
            QTimer.singleShot(config.WELCOME_DURATION_MS, self._end_session)
            QTimer.singleShot(config.WELCOME_DURATION_MS, self._show_idle_text)
        elif self._session_card_id is not None:
            # Card session with a non-matching face: don't keep retrying for
            # the full session timeout -- a card is either yours or it isn't.
            # Show the failure once and return to idle immediately.
            if self.retry_timer:
                self.retry_timer.stop()
                self.retry_timer = None
            if self.session_timeout_timer:
                self.session_timeout_timer.stop()
                self.session_timeout_timer = None

            self.result_overlay.setGeometry(self.video_label.rect())
            self.result_overlay.show_result(False)
            QTimer.singleShot(config.FAIL_DURATION_MS, self.result_overlay.hide_result)
            QTimer.singleShot(config.FAIL_DURATION_MS, self._end_session)
            QTimer.singleShot(config.FAIL_DURATION_MS, self._show_idle_text)

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):
        """Ordered, idempotent teardown. Safe to call from closeEvent or a
        signal handler.

        Ordering matters: stop timers and block new auth, wait for any
        in-flight auth to finish, fully stop the preview (blocking join), and
        only THEN disconnect the device. Doing the device disconnect while the
        native preview stream is still stopping causes the C++ library to
        abort the process ("terminate called without an active exception").
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._shutting_down = True
        self.running = False

        if self.video_timer:
            self.video_timer.stop()
        if self.retry_timer:
            self.retry_timer.stop()
        if self.session_timeout_timer:
            self.session_timeout_timer.stop()
        if self.init_mode_timer:
            self.init_mode_timer.stop()

        # Wait for any in-flight authentication to complete before we touch
        # the device from the shutdown path.
        auth_thread = self._auth_thread
        if auth_thread is not None and auth_thread.is_alive():
            auth_thread.join(timeout=6.0)

        # Fully stop the preview stream (blocks until the native thread ends)
        # BEFORE disconnecting the authenticator.
        self.binding.shutdown()
        self.preview_controller.stop()
        self.host_service.cleanup()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)