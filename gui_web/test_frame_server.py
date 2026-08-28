"""Staleness tests for the MJPEG frame streamer.

The camera preview is stopped for the duration of every SDK auth call (the
RealSense firmware needs exclusive UVC access). The encode loop then simply
starves, so the last encoded JPEG must NOT keep being served as if it were
live -- that is what made the kiosk screen look frozen on a card tap.
"""

import os
import sys

import numpy as np
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from gui_web.frame_server import CameraStreamer, _placeholder_jpeg  # noqa: E402


class _FakePreview:
    """Stands in for PreviewController -- only image_queue is used here."""

    def __init__(self):
        self.image_queue = None


def _streamer():
    return CameraStreamer(_FakePreview(), mirror=False, max_width=0)


def _frame():
    return np.zeros((8, 8, 3), dtype=np.uint8)


def test_fresh_frame_is_served():
    s = _streamer()
    s._encode_store(_frame())
    assert s.latest() is not None
    assert s.live is True


def test_stale_frame_is_withheld(monkeypatch):
    """A frame older than STALE_AFTER_SEC must not be served as live."""
    s = _streamer()
    s._encode_store(_frame())

    real = s._latest_ts
    # Pretend the frame was encoded well before the staleness cutoff.
    s._latest_ts = real - (CameraStreamer.STALE_AFTER_SEC + 0.1)

    assert s.latest() is None
    assert s.live is False


def test_no_frame_yet_is_not_live():
    s = _streamer()
    assert s.latest() is None
    assert s.live is False


def test_new_frame_clears_staleness():
    """Resuming the preview makes the stream live again."""
    s = _streamer()
    s._encode_store(_frame())
    s._latest_ts -= CameraStreamer.STALE_AFTER_SEC + 0.1
    assert s.latest() is None

    s._encode_store(_frame())  # preview resumed
    assert s.latest() is not None


def test_placeholder_is_valid_jpeg():
    """The paused-camera placeholder must be a real JPEG the browser can paint."""
    data = _placeholder_jpeg()
    assert data.startswith(b"\xff\xd8")  # JPEG SOI marker
    assert data.endswith(b"\xff\xd9")    # JPEG EOI marker
