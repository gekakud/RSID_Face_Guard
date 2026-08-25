"""
Web-based main window for RealSense ID Host Mode.

Session-based flow: the camera preview is OFF while idle and only turns on
for a bounded auth "session" -- triggered by a tap anywhere on the page
(AUTH_ONLY_ON_CARD=False) or a valid card tap (AUTH_ONLY_ON_CARD=True).
During a session, face-match is retried every AUTH_RETRY_INTERVAL_SEC until
either a match succeeds or AUTH_SESSION_TIMEOUT_SEC elapses, at which point
the preview stops and the UI silently returns to its resting screensaver.
This avoids the periodic camera-restart stutter of a fixed-interval
always-on auto-auth design.

Structure:
  * WebServer + CameraStreamer serve demo_ui and an MJPEG feed on 127.0.0.1
    (page loads over http:// so the <img> stream is same-origin).
  * DeviceUI  -> Python->JS wrapper over the page's window.deviceUI API.
  * Bridge    -> JS->Python via QWebChannel (keypad code submissions, tap-to-wake).
  * BRIDGE_SETUP_JS wires window.deviceUI.onSubmitCode -> pyBridge.codeSubmitted,
    a document-wide tap listener -> pyBridge.userTapped, and installs the
    camera <img> shim once the page has loaded.

The shared business/hardware layers (HostModeService, PreviewController,
config) are reused unchanged, exactly like gui_qt.GUIQt.
"""

import io
import json
import logging
import os
import threading

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

import config
from face_auth import HostModeService
from hardware.camera_preview import PreviewController
from provisioning.binding import BindingManager
from qr_scanner import QRScanner

from gui_qt.display_utils_qt import find_small_display_geometry
from .frame_server import CameraStreamer, WebServer

from observability import events
from observability import storage_monitor
from observability.logging_setup import get_logger

log = get_logger("gui")

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
    // Live MJPEG stream served by our own loopback HTTP server (frame_server.py)
    // -- same origin as the page, so no CORS/mixed-content issues. Chromium
    // decodes multipart/x-mixed-replace natively, so this is as fast as the
    // Qt UI's own preview path (no JS/base64 involved).
    img.src = '/stream.mjpg';
    // Watchdog: if the stream stalls (e.g. after a brief Wi-Fi flap wedges
    // the connection), the <img> stops progressing but doesn't fire an error
    // event on its own. Track how long it's been since the browser last
    // reported new bytes ('progress' fires repeatedly while an
    // x-mixed-replace stream is flowing); if too long, force a reconnect by
    // resetting img.src.
    img.dataset.rsidLastProgress = Date.now();
    if (!img.dataset.rsidWatchdogWired) {
      img.dataset.rsidWatchdogWired = '1';
      img.addEventListener('progress', function() {
        img.dataset.rsidLastProgress = Date.now();
      });
      img.addEventListener('error', function() {
        console.log('rsid-camera-img stream error, reconnecting');
        setTimeout(function() { img.src = '/stream.mjpg?t=' + Date.now(); }, 250);
      });
      setInterval(function() {
        var last = parseInt(img.dataset.rsidLastProgress || '0', 10);
        if (Date.now() - last > 4000) {
          console.log('rsid-camera-img stream stalled, reconnecting');
          img.dataset.rsidLastProgress = Date.now();
          img.src = '/stream.mjpg?t=' + Date.now();
        }
      }, 2000);
    }
  }
  // Keep the camera-error overlay hidden even if getUserMedia rejects later.
  if (err) {
    new MutationObserver(function() { err.hidden = true; })
      .observe(err, { attributes: true, attributeFilter: ['hidden'] });
  }

  // 2. Wire the QWebChannel bridge (JS -> Python) for keypad submissions and
  //    tap-to-wake (no-card mode -- a tap anywhere while resting starts an
  //    auth session; ignored while a session/keypad/result is already showing).
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
      document.addEventListener('click', function() {
        var state = document.body.dataset.state;
        if (state === 'screensaver' || state === 'screensaver-basic') {
          window.pyBridge.userTapped();
        }
      });
    });
  };
  document.head.appendChild(script);
})();
"""

# Injected once to create a simple full-page text overlay used for init-mode /
# maintenance-mode status messages, independent of demo_ui's own state machine.
STATUS_OVERLAY_SETUP_JS = """
(function() {
  if (document.getElementById('rsid-status-overlay')) return;
  var div = document.createElement('div');
  div.id = 'rsid-status-overlay';
  div.style.position = 'fixed';
  div.style.inset = '0';
  div.style.display = 'none';
  div.style.alignItems = 'center';
  div.style.justifyContent = 'center';
  div.style.zIndex = '9999';
  div.style.background = 'rgba(0,0,0,0.55)';
  div.style.color = '#fff';
  div.style.fontFamily = 'Arial, sans-serif';
  div.style.fontSize = '28px';
  div.style.fontWeight = 'bold';
  div.style.textAlign = 'center';
  document.body.appendChild(div);
})();
"""

def _status_overlay_show_js(text: str) -> str:
    escaped = json.dumps(text)
    return (
        "(function(){var d=document.getElementById('rsid-status-overlay');"
        "if(d){d.textContent=" + escaped + ";d.style.display='flex';}})();"
    )

STATUS_OVERLAY_HIDE_JS = (
    "(function(){var d=document.getElementById('rsid-status-overlay');"
    "if(d){d.style.display='none';}})();"
)

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

    def set_hint_text(self, text):
        self._call("setHintText", text)

class Bridge(QObject):
    """JS -> Python. Keypad code submissions and tap-to-wake from the web UI."""

    # Note: named differently from the userTapped() slot below (JS calls
    # pyBridge.userTapped()) to avoid the slot definition shadowing this
    # Signal class attribute.
    tap_detected = Signal()

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

    @Slot()
    def userTapped(self):
        self.tap_detected.emit()

class _SignalBridge(QObject):
    """Marshals background-thread auth callbacks onto the Qt main thread."""
    auth_result = Signal(bool, object)  # success, name
    card_detected = Signal(object)      # card_id
    card_rejected = Signal(object)      # card_id (unregistered)
    binding_result = Signal(bool, str)  # success, message (device provisioning)
    device_revoked = Signal()           # server removed this device

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
        self._bridge.card_detected.connect(self._on_card_detected)
        self._bridge.card_rejected.connect(self._on_card_rejected)
        self._bridge.binding_result.connect(self._on_binding_result)
        self._bridge.device_revoked.connect(self._on_device_revoked)

        # Shared, GUI-agnostic layers (same as GUIQt).
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        self.host_service.on_reconnect = self.preview_controller.restart

        # Session state (see module docstring).
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
        self._qr_scan_timer = None

        # Camera streamer + web/MJPEG server (serve the UI dir over http://).
        # The <img> in demo_ui points straight at /stream.mjpg (see
        # BRIDGE_SETUP_JS); the streamer's encoded JPEGs are also reused for
        # on-device QR scanning during init mode.
        self.streamer = CameraStreamer(self.preview_controller)
        self.server = WebServer(
            directory=os.path.join(PROJECT_ROOT, config.WEB_UI_DIR),
            streamer=self.streamer,
            port=config.WEB_FRAME_PORT,
            stream_fps=10,
        )
        # Periodic disk-space check (independent of the heartbeat interval).
        # Emits storage_low/storage_ok events on threshold crossings; the
        # latest reading also rides every heartbeat via _collect_metadata().
        self._storage_timer = QTimer(self)
        self._storage_timer.timeout.connect(storage_monitor.check_storage)
        self._storage_timer.start(int(config.STORAGE_CHECK_INTERVAL_SEC * 1000))
        storage_monitor.check_storage()  # baseline check at startup

        # Web view + QWebChannel bridge.
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)
        page = self.view.page()

        self.device_ui = DeviceUI(page)
        self.js_bridge = Bridge(self.device_ui)
        self.js_bridge.tap_detected.connect(self._on_user_tapped)
        self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.js_bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._on_load_finished)

        # Window placement (same logic as GUIQt).
        if config.RUN_ON_REAL_SCREEN:
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._place_on_small_display()
        else:
            self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        # Start hardware, streamer, server, then load the page over http://.
        # Preview thread stays alive for the whole app lifetime; pause it
        # immediately so the camera is off while idle -- sessions
        # resume()/pause() around this baseline.
        self.preview_controller.start()
        self.preview_controller.pause()
        self.streamer.start()
        self.server.start()

        url = self.server.index_url("index.html")
        log.info("Loading web UI: %s", url)
        self.view.load(QUrl(url))

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
            # See gui_qt/main_window_qt.py's identical wiring for why this is
            # safe to call without marshalling to the Qt thread.
            on_bound=self.host_service.on_binding_changed,
        )
        self.binding.start_if_bound()

    # =====================================================
    # DEVICE BINDING (dashboard server -- see server/README.md)
    # =====================================================

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
            "camera_available": bool(self.streamer.available),
            "relay_available": bool(config.RUN_WITH_RELAY),
            "session_active": bool(self._session_active),
            "init_mode_active": bool(self._init_mode_active),
            "auth_in_progress": bool(self.auth_in_progress),
            "storage": storage_monitor.get_storage_metadata(),
        }


    def _on_binding_result(self, ok: bool, message: str):
        """Binding finished (marshalled onto the Qt thread by _SignalBridge)."""
        text = message if ok else f"Registration failed\n{message}"
        self.view.page().runJavaScript(_status_overlay_show_js(text))
        # Leave a failure up longer -- an installer needs time to read why.
        QTimer.singleShot(3000 if ok else 6000, self._end_init_mode)

    def _on_device_revoked(self):
        """The dashboard removed this device (marshalled onto the Qt thread).

        BindingManager has already dropped the identity; here we just tell the
        operator. Face auth from the local DB keeps working; the device can be
        re-enrolled by scanning a fresh QR.
        """
        log.info("Device was removed from the server; now unbound")
        self.view.page().runJavaScript(
            _status_overlay_show_js("Device removed\nRescan a QR to re-enroll")
        )

    # =====================================================
    # INIT MODE (technician QR scan on startup)
    # =====================================================

    def start_init_mode(self):
        """Show a brief live preview and scan for a technician QR code.
        Falls back to normal idle behavior if nothing is found in time."""
        self._init_mode_active = True
        events.emit("init_mode_entered")
        self.device_ui.camera()
        self.view.page().runJavaScript(_status_overlay_show_js("Init Mode"))
        self.preview_controller.resume()

        self._qr_scan_timer = QTimer(self)
        self._qr_scan_timer.timeout.connect(self._qr_scan_tick)
        self._qr_scan_timer.start(200)

        self.init_mode_timer = QTimer(self)
        self.init_mode_timer.setSingleShot(True)
        self.init_mode_timer.timeout.connect(self._end_init_mode)
        self.init_mode_timer.start(int(config.INIT_MODE_DURATION_SEC * 1000))

    def _qr_scan_tick(self):
        if not self._init_mode_active:
            return
        jpeg_bytes = self.streamer.latest()
        if jpeg_bytes is None:
            return
        try:
            frame = np.array(Image.open(io.BytesIO(jpeg_bytes)).convert("RGB"))
        except Exception:
            return
        payload = self._qr_scanner.scan(frame)
        if payload is not None:
            self._on_qr_detected(payload)

    def _end_init_mode(self):
        if not self._init_mode_active:
            return
        self._init_mode_active = False
        if self.init_mode_timer:
            self.init_mode_timer.stop()
            self.init_mode_timer = None
        if self._qr_scan_timer:
            self._qr_scan_timer.stop()
            self._qr_scan_timer = None
        self.view.page().runJavaScript(STATUS_OVERLAY_HIDE_JS)
        self.preview_controller.pause()
        self.device_ui.screensaver()
        log.info("Init mode ended -- resuming normal operation")

    def _on_qr_detected(self, payload: dict):
        """A verified provisioning QR was found during init mode -- bind this
        device to the server named in the payload."""
        if not self._init_mode_active:
            return
        log.info(
            "Provisioning QR detected during init mode: door_id=%s site_id=%s customer_id=%s",
            payload.get("door_id"), payload.get("site_id"), payload.get("customer_id"),
        )
        # Stop scanning immediately so a second frame can't start a second
        # registration with the same (single-use) token.
        if self.init_mode_timer:
            self.init_mode_timer.stop()
            self.init_mode_timer = None
        if self._qr_scan_timer:
            self._qr_scan_timer.stop()
            self._qr_scan_timer = None

        self.view.page().runJavaScript(_status_overlay_show_js("Binding to server..."))
        self.binding.bind_async(
            payload,
            lambda ok, message: self._bridge.binding_result.emit(ok, message),
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
        if not config.RUN_ON_REAL_SCREEN:
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
        self.view.page().runJavaScript(STATUS_OVERLAY_SETUP_JS)
        hint = "Tap your card to enter" if config.AUTH_ONLY_ON_CARD else "Tap the display to enter"
        self.device_ui.set_hint_text(hint)
        # UI rests on its native screensaver; the user wakes it by tapping
        # (no-card mode) or the card monitor detects a registered card
        # (card mode). Either path calls start_session(). On first load,
        # kick off init mode (technician QR scan window) if enabled.
        if config.INIT_MODE_ENABLED:
            self.start_init_mode()
        else:
            self.device_ui.screensaver()

    # =====================================================
    # SESSION MANAGEMENT
    # =====================================================

    def _on_user_tapped(self):
        if not config.AUTH_ONLY_ON_CARD and not self._init_mode_active:
            self.start_session()

    def _on_card_detected(self, card_id):
        # start_card_monitoring() already filters unregistered cards, so any
        # card_id reaching here is valid and ready for a face-match session.
        self.start_session(card_id=card_id)

    def _on_card_rejected(self, card_id):
        """An unregistered card was tapped: no session/camera is started --
        just show a brief failure message, then return to the screensaver."""
        if self._session_active or self._init_mode_active or not self._page_ready:
            return
        self.device_ui.failed(hold=config.FAIL_DURATION_MS)
        QTimer.singleShot(config.FAIL_DURATION_MS, self.device_ui.screensaver)

    def start_session(self, card_id=None):
        """Begin a bounded auth session: show the live camera state, start the
        preview, retry face-match on an interval, and time out back to the
        screensaver if nothing matches."""
        if self._session_active or not self._page_ready:
            return
        self._session_active = True
        self._session_card_id = card_id

        if card_id is not None:
            self.host_service.mark_card_session_active()

        self.device_ui.camera()  # switch to the live-camera ("idle") state
        self.preview_controller.resume()

        self.retry_timer = QTimer(self)
        self.retry_timer.timeout.connect(self._session_auth_tick)
        self.retry_timer.start(int(config.AUTH_RETRY_INTERVAL_SEC * 1000))
        self._session_auth_tick()  # fire the first attempt immediately

        self.session_timeout_timer = QTimer(self)
        self.session_timeout_timer.setSingleShot(True)
        self.session_timeout_timer.timeout.connect(self._session_timeout)
        self.session_timeout_timer.start(int(config.AUTH_SESSION_TIMEOUT_SEC * 1000))

    def _session_auth_tick(self):
        if not self.auth_in_progress:
            self.authenticate()

    def _session_timeout(self):
        if not self._session_active:
            return
        log.info("Auth session timed out with no match -- returning to screensaver")
        self._end_session()
        self.device_ui.screensaver()

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
        if self._session_card_id is not None:
            self.host_service.mark_card_session_done()
        self._session_card_id = None

    # =====================================================
    # AUTHENTICATION
    # =====================================================

    def authenticate(self):
        if self.auth_in_progress:
            return
        self.auth_in_progress = True
        threading.Thread(target=self._run_authentication, daemon=True).start()

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

    def _on_auth_complete(self, success: bool, name):
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

            if name:
                self.device_ui.success(str(name))
            else:
                self.device_ui.success()
            # The web UI's built-in hold+then auto-dismiss defaults to the
            # live-camera ("idle") screen, not the resting screensaver, so
            # explicitly drive it back to the screensaver ourselves once the
            # welcome hold ends, alongside tearing down the camera session.
            QTimer.singleShot(config.WELCOME_DURATION_MS, self._end_session)
            QTimer.singleShot(config.WELCOME_DURATION_MS, self.device_ui.screensaver)
        elif self._session_card_id is not None:
            # Card session with a non-matching face: don't keep retrying for
            # the full session timeout -- a card is either yours or it isn't.
            # Show the failure once and return to the screensaver immediately.
            if self.retry_timer:
                self.retry_timer.stop()
                self.retry_timer = None
            if self.session_timeout_timer:
                self.session_timeout_timer.stop()
                self.session_timeout_timer = None

            self.device_ui.failed(hold=config.FAIL_DURATION_MS)
            QTimer.singleShot(config.FAIL_DURATION_MS, self._end_session)
            QTimer.singleShot(config.FAIL_DURATION_MS, self.device_ui.screensaver)

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):
        """Ordered, idempotent teardown (safe from closeEvent or a signal).

        Stop timers/servers, fully stop the preview (blocking) BEFORE
        disconnecting the device, so the native preview-stop doesn't race the
        authenticator disconnect (which aborts the process).
        """
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True
        self.running = False
        if self.retry_timer:
            self.retry_timer.stop()
        if self.session_timeout_timer:
            self.session_timeout_timer.stop()
        if self.init_mode_timer:
            self.init_mode_timer.stop()
        if self._qr_scan_timer:
            self._qr_scan_timer.stop()
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.streamer.stop()
        except Exception:
            pass
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