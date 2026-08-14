"""
Local HTTP + MJPEG server for the web UI (modeled on the proven Windows POC).

Serves two things on 127.0.0.1:
  * the static web UI files (demo_ui/) so the page loads over http:// — the
    same origin as the stream (avoids QtWebEngine cross-origin/mixed-content
    blocking of a file:// page loading an http:// stream);
  * an MJPEG endpoint at /stream.mjpg fed by the RealSense camera.

Frames come from the existing PreviewController (RGB NumPy arrays); they are
JPEG-encoded with Pillow (OpenCV isn't available on the Pi) and served as
multipart/x-mixed-replace. The web UI's <img id="camera"> points at
/stream.mjpg.

Loopback-only: binds to 127.0.0.1 so nothing is exposed off-device.
"""

import io
import logging
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

from observability.logging_setup import get_logger

log = get_logger("gui")

class CameraStreamer:
    """Keeps the latest JPEG frame from the PreviewController for the MJPEG
    endpoint. The PreviewController runs its own capture thread and pushes RGB
    frames onto image_queue; this class drains that queue, JPEG-encodes the
    newest frame, and stores the bytes for HTTP handlers to serve."""

    def __init__(self, preview_controller, jpeg_quality: int = 80, mirror: bool = True):
        self.preview = preview_controller
        self.jpeg_quality = jpeg_quality
        self.mirror = mirror
        self._latest = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    @property
    def available(self) -> bool:
        return self._running

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="cam-encoder", daemon=True)
        self._thread.start()

    def _loop(self):
        log.info("Camera streamer encode loop started")
        q = self.preview.image_queue
        while self._running:
            frame = None
            try:
                frame = q.get(timeout=0.5)
                # Drain to the newest frame to minimize latency.
                while not q.empty():
                    frame = q.get_nowait()
            except Exception:
                if frame is None:
                    continue
            try:
                self._encode_store(frame)
            except Exception:
                log.exception("Camera streamer: frame encode failed")
        log.info("Camera streamer encode loop exited")

    def _encode_store(self, frame: np.ndarray):
        if self.mirror:
            frame = frame[:, ::-1, :]
        channels = frame.shape[2] if frame.ndim == 3 else 1
        if channels == 4:
            frame = frame[:, :, :3]
        mode = "L" if channels == 1 else "RGB"
        img = Image.fromarray(np.ascontiguousarray(frame), mode=mode)
        if mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality)
        with self._lock:
            self._latest = buf.getvalue()

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False


def make_handler(directory, streamer, stream_fps):
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
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            interval = 1.0 / stream_fps
            try:
                while True:
                    frame = streamer.latest()
                    if frame is None:
                        time.sleep(0.05)
                        continue
                    self.wfile.write(b"--FRAME\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError):
                pass  # client (the <img>) navigated away / reloaded

        def log_message(self, *args):
            pass  # keep the console quiet

    return Handler


class WebServer:
    """Serves `directory` (the web UI root) + the MJPEG stream on 127.0.0.1."""

    def __init__(self, directory: str, streamer: CameraStreamer,
                 host: str = "127.0.0.1", port: int = 8791, stream_fps: int = 20):
        self.directory = os.path.abspath(directory)
        self.streamer = streamer
        self.host = host
        self.port = port
        self.stream_fps = stream_fps
        self._server = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def index_url(self, page: str) -> str:
        return f"{self.base_url}/{page}"

    def start(self):
        self._server = ThreadingHTTPServer(
            (self.host, self.port),
            make_handler(self.directory, self.streamer, self.stream_fps),
        )
        threading.Thread(target=self._server.serve_forever, name="web-http", daemon=True).start()
        log.info("Web server serving %s at %s", self.directory, self.base_url)

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        log.info("Web server stopped")