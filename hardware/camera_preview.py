"""
Camera preview hardware controller.

Wraps the rsid_py UVC preview stream in a background thread, exposing a
thread-safe image queue for the GUI to consume, plus pause/resume
around authentication (the RealSense firmware needs exclusive access
to the camera while authenticating).
"""

import logging
import queue
import threading
import time

import numpy as np
import rsid_py

from observability.logging_setup import get_logger

log = get_logger("preview")

class PreviewController(threading.Thread):
    """Handles camera preview in a separate thread."""

    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        """Initialise the preview thread.

        Args:
            port: Serial port of the RealSense ID device (unused by preview
                  itself but kept for symmetry with the auth service).
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
        self._stopped_ack = threading.Event()
        self._preview_lock = threading.Lock()

    def on_image(self, image):
        """Callback invoked by rsid_py for each decoded preview frame.

        Converts the raw buffer to a NumPy RGB array and drops it into
        image_queue (max depth 2 -- oldest frame is discarded if the GUI
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
        releases the camera. Guarded by a lock + idempotency check so a
        concurrent call (e.g. GUI resume() racing the thread's own startup)
        can't open the UVC stream twice and hit "uvc_open ... Busy".
        """
        with self._preview_lock:
            if self.preview is not None:
                return
            preview_cfg = rsid_py.PreviewConfig()
            preview_cfg.device_type = self.device_type
            preview_cfg.camera_number = self.camera_index
            preview_cfg.preview_mode = rsid_py.PreviewMode.MJPEG_1080P

            self.preview = rsid_py.Preview(preview_cfg)
            self.preview.start(preview_callback=self.on_image, snapshot_callback=None)

    # Previews whose USB device disconnected mid-stream: calling stop() on them
    # crashes (libusb mutex already destroyed by the disconnect handler). We keep
    # a reference here so Python's GC never calls __del__ -> stop() on them.
    # The OS reclaims the resources when the process exits.
    _dead_previews: list = []

    def run(self):
        """Thread entry point -- starts the preview stream then idles until stop() is called."""
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
                # If we're shutting down, don't restart the stream -- just fall
                # through so the loop exits and tears the preview down once.
                if not self.running:
                    break
                if self.preview:
                    # Do NOT call self.preview.stop() -- the USB device already
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
            try:
                self.preview.stop()
            except Exception as e:
                log.warning("Preview stop on exit failed: %s", e)
            self.preview = None
        self._stopped_ack.set()
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
        """Restart the UVC stream after authentication.

        start_preview() is idempotent (locked + no-op if already open), so
        it's safe to call this even if the thread's own startup or another
        resume() call is racing this one.
        """
        self._paused = False
        if self.running:
            try:
                self.start_preview()
                log.info("Preview resumed after authentication")
            except Exception as e:
                log.error("Preview resume failed: %s", e)

    def stop(self):
        """Signal the preview thread to exit its run loop, stop the UVC stream,
        and block until the native stream is fully torn down.

        Blocking here is essential: the RealSense/UVC native stop must complete
        *before* the FaceAuthenticator is disconnected during shutdown,
        otherwise the two native calls race and the C++ library aborts the
        process ("terminate called without an active exception").
        """
        if not self.running and self._stopped_ack.is_set():
            return  # already stopped (idempotent)
        self.running = False
        self._paused_ack.set()   # unblock any pause() call waiting on ack
        # NOTE: do NOT set _restart_event here -- that branch would try to
        # re-open the UVC stream ("uvc_open ... Busy") during shutdown and
        # crash. The run loop polls `running` every 50 ms and will exit on its
        # own; we just wait for it below.
        # Wait for run() to finish its final preview.stop() and set the ack.
        if self.is_alive():
            self._stopped_ack.wait(timeout=4.0)
            self.join(timeout=4.0)
