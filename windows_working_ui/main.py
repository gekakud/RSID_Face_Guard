"""PyQt6 skeleton that hosts the ITSU Device UI (index.html/app.js) in a
QWebEngineView and exposes Python callbacks to drive it.

Python owns the camera (RealSense via pyrealsense2, or a regular webcam as a
fallback), runs the capture loop, and serves the frames to the web UI as a local
MJPEG stream at /stream.mjpg. The UI's <img id="camera"> points at that stream.

POC only: face recognition / card-reader integration is not wired in yet. The
capture loop (CameraStreamer._run) is where recognition would run and then call
DeviceUI.success()/failed(); Bridge.codeSubmitted() is where real code validation
would replace the placeholder check.
"""

import json
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import cv2

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow

HERE = os.path.dirname(os.path.abspath(__file__))
# The RealSense ID binding (rsid_py*.pyd) and its rsid.dll live next to this file.
# Make both importable/loadable no matter what the working directory is.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if hasattr(os, "add_dll_directory") and os.path.isdir(HERE):
    os.add_dll_directory(HERE)

REPO_ROOT = os.path.dirname(HERE)
PORT = 8791
KIOSK = False  # flip to True on the Pi: frameless + fullscreen for the round display
JPEG_QUALITY = 80
STREAM_FPS = 20

# Sets up the JS<->Python channel once index.html has finished loading.
BRIDGE_SETUP_JS = """
(function() {
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


class CameraStreamer:
    """Provides the camera feed for the MJPEG endpoint. Prefers the RealSense ID
    F450 preview (via rsid_py) and falls back to a regular webcam (OpenCV) when the
    F450 / rsid_py isn't available. Frames are JPEG-encoded and the latest bytes are
    kept for the /stream.mjpg handler.

    F450 preview is callback-driven (the SDK delivers raw RGB frames on its own
    thread); the webcam uses a simple poll loop."""

    def __init__(self):
        self.mode = "none"
        self._latest = None
        self._lock = threading.Lock()
        # webcam
        self._cap = None
        self._thread = None
        self._running = False
        # f450
        self._preview = None

        if self._try_f450():
            self.mode = "f450"
        elif self._try_webcam():
            self.mode = "webcam"
        print(f"[camera] source: {self.mode}")

    def _try_f450(self):
        try:
            import rsid_py
        except ImportError:
            return False
        caps = rsid_py.discover_capture()
        if not caps:
            return False
        cfg = rsid_py.PreviewConfig()
        cfg.camera_number = caps[0]
        cfg.preview_mode = rsid_py.PreviewMode.MJPEG_720P
        self._preview = rsid_py.Preview(cfg)
        return True

    def _try_webcam(self):
        # CAP_DSHOW avoids slow MSMF startup on Windows; ignored elsewhere.
        backend = cv2.CAP_DSHOW if os.name == "nt" else 0
        cap = cv2.VideoCapture(0, backend)
        if cap.isOpened():
            self._cap = cap
            return True
        cap.release()
        return False

    @property
    def available(self):
        return self.mode != "none"

    def start(self):
        if self.mode == "f450":
            self._preview.start(self._on_f450_frame, self._on_f450_snapshot)
        elif self.mode == "webcam":
            self._running = True
            self._thread = threading.Thread(target=self._webcam_loop, daemon=True)
            self._thread.start()

    def _on_f450_frame(self, image):
        # MJPEG preview mode: the SDK decodes to a packed RGB buffer (w*h*3).
        import numpy as np
        rgb = np.frombuffer(image.get_buffer(), dtype=np.uint8).reshape(
            (image.height, image.width, 3)
        )
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        self._encode_store(bgr)

    def _on_f450_snapshot(self, image):
        pass  # unused; required positional arg for Preview.start

    def _webcam_loop(self):
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            self._encode_store(frame)

    def _encode_store(self, bgr):
        ok, buf = cv2.imencode(
            ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if ok:
            with self._lock:
                self._latest = buf.tobytes()

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False
        if self._preview is not None:
            try:
                self._preview.stop()
            except Exception:
                pass
        if self._cap is not None:
            self._cap.release()


def make_handler(directory, streamer):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self):
            if self.path.split("?")[0] == "/stream.mjpg":
                self._stream_mjpeg()
            else:
                super().do_GET()

        def _stream_mjpeg(self):
            if not streamer.available:
                self.send_error(503, "No camera")
                return
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()
            interval = 1.0 / STREAM_FPS
            try:
                while True:
                    frame = streamer.latest()
                    if frame is None:
                        time.sleep(0.05)
                        continue
                    self.wfile.write(b"--FRAME\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(
                        f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    )
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError):
                pass  # client (the <img>) navigated away / reloaded

        def log_message(self, *args):
            pass  # keep the console quiet

    return Handler


def start_server(directory, port, streamer):
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(directory, streamer)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class DeviceUI:
    """Python -> JS. Thin wrapper over window.deviceUI (see README.md)."""

    def __init__(self, page):
        self._page = page

    def _call(self, method, *args):
        js_args = ", ".join(json.dumps(a) for a in args if a is not None)
        self._page.runJavaScript(f"deviceUI.{method}({js_args})")

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

    def set_logo(self, src):
        self._call("setLogo", src)


class Bridge(QObject):
    """JS -> Python. Wire real card-reader/backend code validation here."""

    def __init__(self, device_ui):
        super().__init__()
        self._device_ui = device_ui

    @pyqtSlot(str)
    def codeSubmitted(self, code):
        print(f"[Bridge] code submitted: {code!r}")
        # TODO: replace with real validation (backend/card-reader system).
        if code == "1234":
            self._device_ui.code_approved(hold=4000)
        else:
            self._device_ui.code_rejected(hold=3000)


class MainWindow(QMainWindow):
    def __init__(self, url):
        super().__init__()
        self.setWindowTitle("ITSU Device UI - PyQt6 POC")
        self.resize(720, 720)
        if KIOSK:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        self.view = QWebEngineView()
        self.setCentralWidget(self.view)

        page = self.view.page()
        self.device_ui = DeviceUI(page)
        self.bridge = Bridge(self.device_ui)
        self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.bridge)
        page.setWebChannel(self.channel)

        page.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl(url))

        self.showFullScreen() if KIOSK else self.show()

    def _on_load_finished(self, ok):
        if ok:
            self.view.page().runJavaScript(BRIDGE_SETUP_JS)


def main():
    streamer = CameraStreamer()
    streamer.start()
    server = start_server(REPO_ROOT, PORT, streamer)

    app = QApplication(sys.argv)
    window = MainWindow(f"http://127.0.0.1:{PORT}/index.html")  # noqa: F841
    exit_code = app.exec()

    streamer.stop()
    server.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
