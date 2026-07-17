"""
Main PySide6 (Qt6) GUI window for RealSense ID Host Mode.

Mirrors gui/main_window.py's responsibilities (video canvas, result
overlay, optional auth button, auto-auth timer) using Qt widgets and
signals instead of Tkinter. Reuses the same business/hardware layers
(HostModeService, PreviewController, config) unchanged.
"""

import logging
import sys
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QPixmap, QFont, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout, QPushButton,
    QSizePolicy,
)

import config
from face_auth import HostModeService
from hardware.camera_preview import PreviewController

from .display_utils_qt import find_small_display_geometry

log = logging.getLogger("face_guard")

WINDOW_NAME = "RealSenseID Host Mode (Qt)"

class _SignalBridge(QObject):
    """Marshals callbacks from background threads (HostModeService,
    PreviewController) onto the Qt main thread via signals."""
    auth_result = Signal(bool, object)  # success, name


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
            box_h = 180 if config.RUN_ON_REAL_DEVICE else 220
            x1 = (w - box_w) // 2
            y1 = (h - box_h) // 2
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y1, box_w, box_h)

            welcome_size = 28 if config.RUN_ON_REAL_DEVICE else 36
            name_size = 40 if config.RUN_ON_REAL_DEVICE else 64
            gap = 38 if config.RUN_ON_REAL_DEVICE else 48
            color = QColor("#4CAF50")
            painter.setPen(color)

            painter.setFont(QFont("Arial", welcome_size, QFont.Weight.Bold))
            painter.drawText(0, cy - gap - welcome_size, w, welcome_size * 2,
                              Qt.AlignmentFlag.AlignCenter, "WELCOME")

            painter.setFont(QFont("Arial", name_size, QFont.Weight.Bold))
            painter.drawText(0, cy + gap - name_size, w, name_size * 2,
                              Qt.AlignmentFlag.AlignCenter, self._name.upper())
        else:
            box_size = 300 if config.RUN_ON_REAL_DEVICE else 400
            x1 = (w - box_size) // 2
            y1 = (h - box_size) // 2
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x1, y1, box_size, box_size)

            symbol = "OK" if self._success else "X"
            color = QColor("#4CAF50") if self._success else QColor("#F44336")
            font_size = 150 if config.RUN_ON_REAL_DEVICE else 200

            painter.setPen(color)
            painter.setFont(QFont("Arial", font_size, QFont.Weight.Bold))
            painter.drawText(0, cy - font_size, w, font_size * 2,
                              Qt.AlignmentFlag.AlignCenter, symbol)


class GUIQt(QMainWindow):
    """Main PySide6 window."""

    def __init__(self, port: str, camera_index: int, device_type):
        super().__init__()
        self.setWindowTitle(WINDOW_NAME)

        self.port = port
        self.auth_in_progress = False
        self.running = True

        self._bridge = _SignalBridge()
        self._bridge.auth_result.connect(self._on_auth_complete)

        # Services (shared, GUI-agnostic layers)
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        self.host_service.on_reconnect = self.preview_controller.restart
        self.host_service.on_before_card_auth = self.preview_controller.pause
        self.host_service.on_after_card_auth = self.preview_controller.resume

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

        self.auth_button: Optional[QPushButton] = None
        if config.WITH_BUTTON:
            self.auth_button = QPushButton("Authenticate", central)
            font = QFont("Arial", 20 if config.RUN_ON_REAL_DEVICE else 28, QFont.Weight.Bold)
            self.auth_button.setFont(font)
            self.auth_button.setMinimumHeight(70 if config.RUN_ON_REAL_DEVICE else 100)
            self.auth_button.clicked.connect(self.authenticate)
            layout.addWidget(self.auth_button)

        # Window placement
        if config.RUN_ON_REAL_DEVICE:
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._place_on_small_display()
        else:
            self.resize(720, 900)
            self.setMinimumSize(500, 600)

        # Timers (replace Tkinter's self.after loops)
        self.video_timer = QTimer(self)
        self.video_timer.timeout.connect(self._update_video)
        self.video_timer.start(30)

        self.auto_auth_timer: Optional[QTimer] = None
        if not config.WITH_BUTTON:
            self.auto_auth_timer = QTimer(self)
            self.auto_auth_timer.timeout.connect(self._auto_auth_tick)
            self.auto_auth_timer.start(int(config.AUTO_AUTH_INTERVAL_SEC * 1000))

        self.preview_controller.start()

        if config.RUN_WITH_CARD_READER:
            self.host_service.start_card_monitoring(
                on_result=lambda s, n, p: self._bridge.auth_result.emit(s, n)
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

    # =====================================================
    # VIDEO
    # =====================================================

    def _update_video(self):
        if not self.preview_controller.running:
            return
        array2d = None
        q = self.preview_controller.image_queue
        while not q.empty():
            array2d = q.get()
        if array2d is None:
            return

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
    # AUTHENTICATION
    # =====================================================

    def authenticate(self):
        if self.auth_in_progress:
            return
        self.auth_in_progress = True
        if self.auth_button is not None:
            self.auth_button.setEnabled(False)

        import threading
        threading.Thread(target=self._run_authentication, daemon=True).start()

    def _run_authentication(self):
        self.preview_controller.pause()
        try:
            success, name, permission = self.host_service.authenticate_all_users()
            if success:
                log.info("Access granted: %s (%s)", name, permission)
            else:
                log.warning("Access denied: %s", permission)
            self._bridge.auth_result.emit(success, name)
        except Exception as e:
            log.error("Authentication error: %s", e)
            self._bridge.auth_result.emit(False, None)
        finally:
            self.preview_controller.resume()

    def _auto_auth_tick(self):
        if not self.auth_in_progress:
            self.authenticate()

    def _on_auth_complete(self, success: bool, name: Optional[str]):
        self.auth_in_progress = False
        if self.auth_button is not None:
            self.auth_button.setEnabled(True)

        show = success or config.WITH_BUTTON
        if show:
            self.result_overlay.setGeometry(self.video_label.rect())
            self.result_overlay.show_result(success, name)
            duration = config.WELCOME_DURATION_MS if success else config.FAIL_DURATION_MS
            QTimer.singleShot(duration, self.result_overlay.hide_result)

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def closeEvent(self, event):
        self.running = False
        if self.video_timer:
            self.video_timer.stop()
        if self.auto_auth_timer:
            self.auto_auth_timer.stop()
        self.preview_controller.stop()
        self.host_service.cleanup()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)