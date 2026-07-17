"""
Web-based main window — recreated from the proven Windows POC
(windows_working_ui/main.py), adapted to the Pi/RealSense stack.

Structure mirrors the Windows version:
  * WebServer + CameraStreamer serve demo_ui and an MJPEG feed on 127.0.0.1
    (page loads over http:// so the <img> stream is same-origin).
  * DeviceUI  -> Python->JS wrapper over the page's window.deviceUI API.
  * Bridge    -> JS->Python via QWebChannel (keypad code submissions).
  * BRIDGE_SETUP_JS wires window.deviceUI.onSubmitCode -> pyBridge.codeSubmitted
    and installs the camera <img> shim once the page has loaded.

The shared business/hardware layers (HostModeService, PreviewController,
config) are reused unchanged, exactly like gui_qt.GUIQt.
"""

import json
import logging
import os
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

import config
from face_auth import HostModeService
from hardware.camera_preview import PreviewController

from gui_qt.display_utils_qt import find_small_display_geometry
from .frame_server import CameraStreamer, WebServer

log = logging.getLogger("face_guard")

WINDOW_NAME = "RealSenseID Host Mode (Web)"

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

# Runs once index.html has loaded: sets up the JS<->Python channel and swaps the
# demo_ui <video> camera for an <img> fed by our MJPEG stream.
BRIDGE_SETUP_JS = """
(function() {
  // 1. Swap the getUserMedia <video> for our MJPEG <img> (our demo_ui uses a
  //    <video id="camera">; the browser can't reach the RealSense UVC camera).
  var cam = document.getElementById('camera');
  var err = document.getElementById('camera-error');
  if (err) { err.hidden = true; }
  if (cam) {
    cam.style.display = 'none';
    var img = document.getElementById('rsid-camera-img');
    if (!img) {
      img = document.createElement('img');
      img.id = 'rsid-camera-img';
      img.style.position = 'absolute';
      img.style.inset = '0';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.objectFit = 'cover';
      img.style.zIndex = '0';
      img.style.background = '#05040a';
      cam.parentNode.insertBefore(img, cam);
    }
    img.src = '/stream.mjpg';
  }
  // Keep the camera-error overlay hidden even if getUserMedia rejects later.
  if (err) {
    new MutationObserver(function() { err.hidden = true; })
      .observe(err, { attributes: true, attributeFilter: ['hidden'] });
  }

  // 2. Wire the QWebChannel bridge (JS -> Python) for keypad submissions.
  var script = document.createElement('script');
  script.src = 'qrc:///qtwebchannel/qwebchannel.js';
  script.onload = function() {
    new QWebChannel(qt.webChannelTransport, function(channel) {
      window.pyBridge = channel.objects.pyBridge;
      if (window.deviceUI) {
        window.deviceUI.onSubmitCode = function(code) {
          window.pyBridge.codeSubmitted(code);
        };
      }
    });
  };
  document.head.appendChild(script);
})();
"""

class DeviceUI:
    """Python -> JS. Thin wrapper over the page's window.deviceUI API."""

    def __init__(self, page):
        self._page = page

    def _call(self, method, *args):
        js_args = ", ".join(json.dumps(a) for a in args if a is not None)
        self._page.runJavaScript(f"window.deviceUI && deviceUI.{method}({js_args})")

    def success(self, name=None, hold=None):
        self._call("success", name, hold)

    def failed(self, hold=None):
        self._call("failed", hold)

    def idle(self):
        self._call("idle")

    def screensaver(self):
        self._call("screensaver")

    def camera(self):
        self._call("camera")

    def code_entry(self):
        self._call("codeEntry")

    def code_approved(self, hold=None):
        self._call("codeApproved", hold)

    def code_rejected(self, hold=None):
        self._call("codeRejected", hold)

    def set_expected_code(self, code):
        self._call("setExpectedCode", str(code))

class Bridge(QObject):
    """JS -> Python. Keypad code submissions from the web UI.

    Per project decision, keypad validation stays demo-only (the built-in
    check), so this just mirrors the Windows POC placeholder."""

    def __init__(self, device_ui: DeviceUI):
        super().__init__()
        self._device_ui = device_ui

    @Slot(str)
    def codeSubmitted(self, code):
        log.info("Keypad code submitted: %r", code)
        if code == "1234":
            self._device_ui.code_approved(hold=4000)
        else:
            self._device_ui.code_rejected(hold=3000)

class _SignalBridge(QObject):
    """Marshals background-thread auth callbacks onto the Qt main thread."""
    auth_result = Signal(bool, object)  # success, name

class GUIWeb(QMainWindow):
    """Main window hosting the web UI in a QWebEngineView."""

    def __init__(self, port: str, camera_index: int, device_type):
        super().__init__()
        self.setWindowTitle(WINDOW_NAME)

        self.port = port
        self.auth_in_progress = False
        self.running = True
        self._page_ready = False
        self._small_display_origin = None

        self._bridge = _SignalBridge()
        self._bridge.auth_result.connect(self._on_auth_complete)

        # Shared, GUI-agnostic layers (same as GUIQt).
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        self.host_service.on_reconnect = self.preview_controller.restart
        self.host_service.on_before_card_auth = self.preview_controller.pause
        self.host_service.on_after_card_auth = self.preview_controller.resume

        # Camera streamer + web/MJPEG server (serve the UI dir over http://).
        self.streamer = CameraStreamer(self.preview_controller)
        self.server = WebServer(
            directory=os.path.join(PROJECT_ROOT, config.WEB_UI_DIR),
            streamer=self.streamer,
            port=config.WEB_FRAME_PORT,
        )

        # Web view + QWebChannel bridge.
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)
        page = self.view.page()

        self.device_ui = DeviceUI(page)
        self.js_bridge = Bridge(self.device_ui)
        self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.js_bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._on_load_finished)

        # Window placement (same logic as GUIQt).
        if config.RUN_ON_REAL_DEVICE:
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._place_on_small_display()
        else:
            self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        # Auto-auth timer (no on-screen button in the web UI).
        self.auto_auth_timer = None
        if not config.WITH_BUTTON:
            self.auto_auth_timer = QTimer(self)
            self.auto_auth_timer.timeout.connect(self._auto_auth_tick)
            self.auto_auth_timer.start(int(config.AUTO_AUTH_INTERVAL_SEC * 1000))

        # Start hardware, streamer, server, then load the page over http://.
        self.preview_controller.start()
        self.streamer.start()
        self.server.start()

        url = self.server.index_url("index.html")
        log.info("Loading web UI: %s", url)
        self.view.load(QUrl(url))

        if config.RUN_WITH_CARD_READER:
            self.host_service.start_card_monitoring(
                on_result=lambda s, n, p: self._bridge.auth_result.emit(s, n)
            )

    # =====================================================
    # DISPLAY PLACEMENT
    # =====================================================

    def _place_on_small_display(self):
        geo = find_small_display_geometry(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        # Kiosk mode: frameless so the WM adds no title bar / border (which
        # otherwise eats vertical space and breaks the round 720x720 layout).
        # When KIOSK_BORDERLESS is False, keep a normal bordered window for
        # editor-side debugging.
        if config.KIOSK_BORDERLESS:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setFixedSize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        if geo is not None:
            x, y = geo
            self.move(x, y)
            self._small_display_origin = (x, y)
            log.info("Web GUI placed on small display at %d,%d size %dx%d",
                     x, y, config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        else:
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - config.WINDOW_WIDTH) // 2
            y = (screen.height() - config.WINDOW_HEIGHT) // 2
            self.move(x, y)
            log.info("Small display not detected -> centering Web GUI on primary display.")

    def show_appropriately(self):
        """Show the window. On the real device in kiosk mode, go fullscreen on
        the small display (no title bar / border, fills the whole 720x720
        panel). When KIOSK_BORDERLESS is False, show a normal bordered window
        (positioned on the small display) for editor-side debugging.

        Called from main_web.py in place of a bare .show()."""
        if not config.RUN_ON_REAL_DEVICE:
            self.show()
            return

        # Ensure we're on the right screen before going fullscreen: Qt sends a
        # fullscreen window to the screen its geometry currently overlaps.
        if self._small_display_origin is not None:
            x, y = self._small_display_origin
            self.move(x, y)
            handle = self.windowHandle()
            if handle is not None:
                for screen in QApplication.screens():
                    if screen.geometry().contains(x, y):
                        handle.setScreen(screen)
                        break

        if config.KIOSK_BORDERLESS:
            self.showFullScreen()
        else:
            self.show()

    # =====================================================
    # PAGE READY
    # =====================================================

    def _on_load_finished(self, ok: bool):
        log.info("Web UI loaded ok=%s", ok)
        if not ok:
            return
        self._page_ready = True
        self.view.page().runJavaScript(BRIDGE_SETUP_JS)
        # Leave the UI on its native screensaver; the user wakes it by tapping
        # (or, later, the physical button). Auto-auth only runs on the live
        # camera state (see _auto_auth_tick).

    # =====================================================
    # AUTHENTICATION
    # =====================================================

    def _auto_auth_tick(self):
        if self.auth_in_progress or not self._page_ready:
            return
        # Only authenticate while the UI is on the live-camera ("idle") state.
        self.view.page().runJavaScript(
            "document.body.dataset.state", self._maybe_authenticate_for_state
        )

    def _maybe_authenticate_for_state(self, state):
        if state == "idle" and not self.auth_in_progress:
            self.authenticate()

    def authenticate(self):
        if self.auth_in_progress:
            return
        self.auth_in_progress = True
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

    def _on_auth_complete(self, success: bool, name):
        self.auth_in_progress = False
        if success and name:
            self.device_ui.success(str(name))
        elif success:
            self.device_ui.success()
        else:
            if config.WITH_BUTTON:
                self.device_ui.failed()

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def closeEvent(self, event):
        self.running = False
        if self.auto_auth_timer:
            self.auto_auth_timer.stop()
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.streamer.stop()
        except Exception:
            pass
        self.preview_controller.stop()
        self.host_service.cleanup()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)