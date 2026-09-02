"""
Web-based main window for RealSense ID Host Mode.

Session-based flow: the camera preview is OFF while idle and only turns on
for a bounded auth "session" -- triggered by a tap anywhere on the page
(DEMO_FACE_ONLY) or a valid card tap (the three door modes).
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

The session state machine itself lives in session/controller.py; this class is
its view adapter (SessionView + Scheduler) plus Qt/platform glue. The shared
business/hardware layers (AuthService, PreviewController, config) carry no
UI dependency.
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
from face_auth import AuthService
from hardware.camera_preview import PreviewController
from provisioning.binding import BindingManager
from qr_scanner import QRScanner
from session import SessionController

from gui_web.display_utils import find_small_display_geometry
from .frame_server import CameraStreamer, WebServer

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
    """Marshals background-thread callbacks onto the Qt main thread."""
    card_detected = Signal(object)      # card_id
    card_rejected = Signal(object)      # card_id (unregistered)
    binding_result = Signal(bool, str)  # success, message (device provisioning)
    device_revoked = Signal()           # server removed this device
    return_to_init = Signal()           # revocation cleanup done -> re-enter init mode

class QtScheduler:
    """Backs session.Scheduler with QTimer + a Qt signal for UI marshalling.

    ``call_later`` / ``call_interval`` return the QTimer as the opaque handle;
    ``cancel`` stops it. ``post_to_ui`` emits a queued signal so a worker-thread
    auth result is executed on the Qt main thread.
    """

    class _Marshaller(QObject):
        run = Signal(object)  # callable to invoke on the UI thread

    def __init__(self, parent: QObject):
        self._parent = parent
        self._marshaller = self._Marshaller()
        self._marshaller.run.connect(lambda fn: fn())

    def call_later(self, delay_ms, fn):
        timer = QTimer(self._parent)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.start(int(delay_ms))
        return timer

    def call_interval(self, interval_ms, fn):
        timer = QTimer(self._parent)
        timer.timeout.connect(fn)
        timer.start(int(interval_ms))
        return timer

    def cancel(self, handle):
        if handle is not None:
            handle.stop()

    def post_to_ui(self, fn):
        self._marshaller.run.emit(fn)

class WebSessionView:
    """Adapts the page's DeviceUI + status overlay to session.SessionView."""

    def __init__(self, device_ui: "DeviceUI", page):
        self._device_ui = device_ui
        self._page = page

    def show_camera(self):
        self._device_ui.camera()

    def show_success(self, name):
        if name:
            self._device_ui.success(str(name))
        else:
            self._device_ui.success()

    def show_failure(self, hold_ms):
        self._device_ui.failed(hold=hold_ms)

    def show_unavailable(self, hold_ms):
        # No dedicated screen yet (arrives with T9/FR-UI-12); alias to failure
        # so behaviour is unchanged in B1.
        self._device_ui.failed(hold=hold_ms)

    def show_scanning(self):
        # Preview is paused for the SDK call; the stream serves a neutral
        # placeholder meanwhile. Keep the camera screen (no dedicated
        # "scanning" screen exists yet) so the session view doesn't flicker.
        self._device_ui.camera()

    def show_idle(self):
        self._device_ui.screensaver()

    def show_overlay(self, text):
        self._page.runJavaScript(_status_overlay_show_js(text))

    def hide_overlay(self):
        self._page.runJavaScript(STATUS_OVERLAY_HIDE_JS)

class GUIWeb(QMainWindow):
    """Main window hosting the web UI in a QWebEngineView."""

    def __init__(self, port: str, camera_index: int, device_type):
        super().__init__()
        self.setWindowTitle(WINDOW_NAME)

        self.port = port
        self.running = True
        self._page_ready = False
        self._small_display_origin = None

        self._bridge = _SignalBridge()
        self._bridge.card_detected.connect(self._on_card_detected)
        self._bridge.card_rejected.connect(self._on_card_rejected)
        self._bridge.binding_result.connect(self._on_binding_result)
        self._bridge.device_revoked.connect(self._on_device_revoked)
        self._bridge.return_to_init.connect(self._return_to_init_mode)

        # Shared, GUI-agnostic layers.
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = AuthService(port)
        self.host_service.on_reconnect = self.preview_controller.restart

        # QR scanner used by the session controller's init-mode window.
        self._qr_scanner = QRScanner()
        self._scheduler = QtScheduler(self)

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
        self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.js_bridge)
        page.setWebChannel(self.channel)
        page.loadFinished.connect(self._on_load_finished)

        # Session state machine (extracted, UI-agnostic). The web window is now
        # only a view adapter + platform glue (T1 / NFR-19).
        self._session_view = WebSessionView(self.device_ui, page)
        self.controller = SessionController(
            host_service=self.host_service,
            preview_controller=self.preview_controller,
            view=self._session_view,
            scheduler=self._scheduler,
            run_in_thread=lambda fn: threading.Thread(target=fn, daemon=True).start(),
            relay=self._pulse_door if config.RUN_WITH_RELAY else None,
            qr_scanner=self._qr_scanner,
            latest_frame=self._latest_frame,
            on_qr_payload=self._begin_binding,
            is_page_ready=lambda: self._page_ready,
        )
        self.js_bridge.tap_detected.connect(self.controller.on_user_tapped)

        # Window placement.
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

        if config.mode_uses_card_reader():
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
            "session_active": bool(self.controller.session_active),
            "init_mode_active": bool(self.controller.init_mode_active),
            "auth_in_progress": bool(self.controller.auth_in_progress),
            "storage": storage_monitor.get_storage_metadata(),
        }


    def _on_binding_result(self, ok: bool, message: str):
        """Binding finished (marshalled onto the Qt thread by _SignalBridge)."""
        text = message if ok else f"Registration failed\n{message}"
        self.view.page().runJavaScript(_status_overlay_show_js(text))
        # Leave a failure up longer -- an installer needs time to read why.
        QTimer.singleShot(3000 if ok else 6000, self.controller.end_init_mode)
        if ok:
            # The device is now bound: wire up remote DB sync and pull the door
            # user set immediately (FR-DB-07) instead of waiting for a reboot.
            # The first fetch is a blocking HTTP call, so run it off the UI
            # thread.
            threading.Thread(
                target=self.host_service.enable_remote_sync,
                name="post-bind-db-sync",
                daemon=True,
            ).start()

    def _on_device_revoked(self):
        """Fail-secure revocation handler (FR-HB-10 / FR-STATE-02), marshalled
        onto the Qt thread by ``_SignalBridge`` after BindingManager has already
        flushed final events and dropped the identity.

        The GUI owns the two collaborators BindingManager can't reach, so it
        finishes the teardown here:

          * stop remote DB sync + wipe the local user DB **including faceprints**
            (they all live in the one JSON file), and
          * return the running app to the "first start, no identity" init state
            by calling ``start_init_mode()`` -- the exact same entry point used
            at boot, so no reboot is needed. With no identity and init mode
            active, all auth is denied (deny-all) until a fresh QR re-enrolls
            the device.

        The DB teardown touches disk/network, so it runs on a worker thread; the
        init-mode re-entry is marshalled back onto the UI thread.
        """
        log.warning("Device revoked -- wiping local data and returning to init mode")
        self.view.page().runJavaScript(
            _status_overlay_show_js("Device removed\nRescan a QR to re-enroll")
        )

        def _wipe_and_reset():
            try:
                self.host_service.disable_remote_sync()
            except Exception as exc:
                log.error("disable_remote_sync failed (ignored): %s", exc)
            try:
                self.host_service.user_db.clear()
                log.info("Local user DB wiped on revocation (faceprints cleared)")
            except Exception as exc:
                log.error("Local user DB wipe failed (ignored): %s", exc)
            # Return to init mode on the UI thread (start_init_mode is Qt-bound).
            self._bridge.return_to_init.emit()

        threading.Thread(
            target=_wipe_and_reset, name="revoke-cleanup", daemon=True
        ).start()

    def _return_to_init_mode(self):
        """Re-enter the QR-scan init state after a revocation wipe (UI thread).

        Ends any in-flight session/init window first so re-entry is clean, then
        starts init mode -- the same path a freshly-booted, unbound device takes.
        """
        try:
            self.controller.end_init_mode()
        except Exception:
            pass
        self.controller.start_init_mode()

    # =====================================================
    # INIT MODE GLUE (scanning/binding owned by SessionController)
    # =====================================================

    def _latest_frame(self):
        """Decode the streamer's newest JPEG into an RGB ndarray for the
        controller's init-mode QR scanner. Returns None if unavailable."""
        jpeg_bytes = self.streamer.latest()
        if jpeg_bytes is None:
            return None
        try:
            return np.array(Image.open(io.BytesIO(jpeg_bytes)).convert("RGB"))
        except Exception:
            return None

    def _pulse_door(self) -> bool:
        """Access Output Service for the controller: pulse the door strike and
        report whether it opened. Runs on the controller's auth worker thread
        (the ~3 s pulse must never block the UI). ``open_door`` returns False
        on a relay failure so the controller can emit access_output_failed."""
        from hardware.relay_api import open_door
        return open_door(3.0)

    def _begin_binding(self, payload: dict):
        """A verified provisioning QR was decoded -- bind this device to the
        server named in the payload (the controller already stopped scanning)."""
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
        hint = "Tap the display to enter" if config.DEMO_FACE_ONLY else "Tap your card to enter"
        self.device_ui.set_hint_text(hint)
        # UI rests on its native screensaver; the user wakes it by tapping
        # (no-card mode) or the card monitor detects a registered card
        # (card mode). Either path calls start_session(). On first load,
        # init mode is always the entry state (T16 / rev 1.3): the controller
        # enters it unconditionally, then hands off to normal operation --
        # immediately when INIT_MODE_ENABLED is False (zero-length window).
        self.controller.start_init_mode()

    # =====================================================
    # SESSION ENTRY POINTS (state machine owned by SessionController)
    # =====================================================

    def _on_card_detected(self, card_id):
        # start_card_monitoring() already filters unregistered cards, so any
        # card_id reaching here is valid and ready for a face-match session.
        self.controller.on_card_detected(card_id)

    def _on_card_rejected(self, card_id):
        """An unregistered card was tapped: no session/camera is started --
        just show a brief failure message, then return to the screensaver."""
        self.controller.on_card_rejected(card_id)

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
        self.controller.cancel_all_timers()
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