#!/usr/bin/env python3
"""
QtWebEngine smoke test for the ITSU web UI (demo_ui/).

Purpose
-------
Verify that PySide6's QtWebEngine can render the designer-provided web UI
(`demo_ui/index.html`) at 720x720 on this machine, and that Python can drive
the UI's public `window.deviceUI` JavaScript API via runJavaScript().

Use this to validate a fresh Raspberry Pi 5 (or any new machine) before wiring
the real web front-end. See howto.md section "QtWebEngine (web UI) setup" for
the one-time system dependencies this test relies on (libwebp/libtiff symlinks,
--no-sandbox, software OpenGL).

Run
---
    DISPLAY=:0 .venv/bin/python test_webengine_ui.py

Expected: a 720x720 window shows the web UI, cycling through
screensaver -> camera -> success -> failed every 3s. A results log is also
written to /tmp/webengine_ui_test.txt. The line
    typeof window.deviceUI = object
confirms the JS integration API is present, and
    after success() body.dataset.state = success
confirms Python successfully drove the UI.

Note: a `Camera could not start: DOMException` message in the console is
EXPECTED here — the web UI's browser getUserMedia() cannot access the
RealSense UVC camera. In the real app the camera frames are pushed from
Python (rsid_py PreviewController) instead; this test does not exercise that.
"""

import os
import sys

# --- One-time runtime requirements (see howto.md) --------------------------
# 1. Chromium refuses to run as root with its sandbox on; the kiosk runs as
#    root, so disable the sandbox.
# 2. Force software OpenGL: the Pi's GL stack under X cannot back Chromium's
#    GPU process, which otherwise silently stalls page loads (loadFinished
#    never fires). The gbm_wrapper/dma_buf console errors under software GL
#    are harmless.
# These MUST be set before any QtWebEngine import / QApplication init.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-gpu-compositing --in-process-gpu",
)
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "demo_ui", "index.html")
LOG = "/tmp/webengine_ui_test.txt"


def _log(line):
    print(f"[webengine-test] {line}", flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def main():
    open(LOG, "w").close()  # reset log
    app = QApplication(sys.argv)

    view = QWebEngineView()
    view.setFixedSize(720, 720)
    view.setWindowTitle("ITSU Web UI — WebEngine Smoke Test")

    def on_load(ok):
        _log(f"page loaded ok={ok}")
        # Confirm the JS API the backend will drive actually exists on the page.
        view.page().runJavaScript(
            "typeof window.deviceUI",
            lambda t: _log(f"typeof window.deviceUI = {t}"),
        )

        def after_success(_):
            view.page().runJavaScript(
                "document.body.dataset.state",
                lambda s: _log(f"after success() body.dataset.state = {s}"),
            )

        view.page().runJavaScript(
            "window.deviceUI.success('WebEngineTest')", after_success
        )

    view.loadFinished.connect(on_load)
    _log(f"loading {INDEX}")
    view.load(QUrl.fromLocalFile(INDEX))
    view.show()

    # Continuously cycle the UI states so the window is visually verifiable.
    seq = [
        ("window.deviceUI.screensaver()", "screensaver"),
        ("window.deviceUI.camera()", "camera/idle"),
        ("window.deviceUI.success('WebEngineTest')", "success"),
        ("window.deviceUI.failed()", "failed"),
    ]
    idx = {"i": 0}

    def tick():
        js, label = seq[idx["i"] % len(seq)]
        _log(f"driving: {label}")
        view.page().runJavaScript(js)
        idx["i"] += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(3000)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()