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

    # A frame older than this is considered stale: the PreviewController has
    # stopped the UVC stream (auth pauses it -- the RealSense firmware needs
    # exclusive camera access), so the last encoded JPEG no longer reflects
    # what the camera sees. Serving it would leave the kiosk showing a frozen
    # picture that looks like a live view. Must comfortably exceed one frame
    # interval at the lowest expected capture rate.
    STALE_AFTER_SEC = 0.5

    def __init__(self, preview_controller, jpeg_quality: int = 55, mirror: bool = False,
                 max_width: int = 704):
        self.preview = preview_controller
        self.jpeg_quality = jpeg_quality
        self.mirror = mirror
        # Downscaling before JPEG encode cuts both encode time and the bytes
        # Chromium has to decode/paint per frame -- a big share of the web
        # UI's slowness vs the Qt UI's native QLabel path on Pi-class ARM.
        self.max_width = max_width
        self._latest = None
        self._latest_ts = 0.0
        self._seq = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
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
        if frame.ndim == 3 and frame.shape[2] == 1:
            frame = frame[:, :, 0]
        # Centre square crop: the UI shows a square/round area (object-fit:
        # cover), so the cropped-away pixels were never visible anyway.
        h, w = frame.shape[:2]
        side = min(h, w)
        top, left = (h - side) // 2, (w - side) // 2
        frame = frame[top:top + side, left:left + side]
        if self.mirror:
            frame = frame[:, ::-1]
        if frame.ndim == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]
        img = Image.fromarray(np.ascontiguousarray(frame))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if self.max_width and img.width > self.max_width:
            img = img.resize((self.max_width, self.max_width), Image.BILINEAR, reducing_gap=2.0)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality)
        with self._cond:
            self._latest = buf.getvalue()
            self._latest_ts = time.monotonic()
            self._seq += 1
            self._cond.notify_all()

    def wait_for_frame(self, last_seq: int, timeout: float = 1.0):
        """Block until a frame newer than last_seq exists (or timeout/stop).

        Returns (seq, jpeg); jpeg is None when no fresh frame (camera paused).
        """
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq != last_seq or not self._running, timeout
            )
            fresh = (
                self._latest is not None
                and time.monotonic() - self._latest_ts <= self.STALE_AFTER_SEC
            )
            return self._seq, (self._latest if fresh else None)

    def latest(self):
        """Newest JPEG, or None if it is stale (camera paused/stopped).

        Returning None rather than the last good frame is deliberate: a paused
        preview must not be presented as a live view (the encode loop simply
        starves when the stream stops, so ``_latest`` would otherwise be served
        forever).
        """
        with self._lock:
            if self._latest is None:
                return None
            if time.monotonic() - self._latest_ts > self.STALE_AFTER_SEC:
                return None
            return self._latest

    @property
    def live(self) -> bool:
        """True when a fresh frame is available (camera actually streaming)."""
        return self.latest() is not None

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify_all()


def _placeholder_jpeg(width: int = 320, height: int = 320) -> bytes:
    """A plain dark frame served while the camera is paused.

    The MJPEG connection stays open across a pause, and a browser keeps
    painting the last part it received. Skipping frames would therefore leave
    the previous (live-looking) image on screen forever, so we must actively
    push something neutral instead.
    """
    img = Image.new("RGB", (width, height), (16, 16, 18))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


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
            min_interval = 1.0 / stream_fps
            placeholder = _placeholder_jpeg()
            seq = -1
            last_sent = 0.0
            try:
                while streamer.available:
                    # Send on each new camera frame (no timer, no duplicates).
                    # While paused there is no fresh frame: push the neutral
                    # placeholder about once a second, so the browser doesn't
                    # keep the last live frame (looks frozen) and its stall
                    # watchdog still sees data.
                    seq, frame = streamer.wait_for_frame(seq, timeout=1.0)
                    now = time.monotonic()
                    if frame is not None and now - last_sent < min_interval:
                        continue  # over the fps cap: drop this frame
                    frame = frame or placeholder
                    self.wfile.write(b"--FRAME\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    last_sent = now
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