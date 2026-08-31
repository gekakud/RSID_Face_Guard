"""UI-agnostic session orchestration.

This package owns the kiosk *session state machine* -- the logic that was
previously embedded in ``gui_web/web_window.py``. Extracting it here satisfies
NFR-19: the business/session logic is free of any UI or transport concern,
driven only through the injected ``SessionView`` and ``Scheduler`` protocols.

The web front-end supplies:
  * a ``SessionView`` adapter that forwards to the page's ``window.deviceUI`` API;
  * a ``Scheduler`` backed by ``QTimer`` (delays/cancellation) and a Qt signal
    (``post_to_ui`` -- marshalling a worker-thread auth result back onto the UI
    thread).

``SessionController`` itself imports no Qt and no rsid_py, so it can be unit
tested off-device.
"""

from .controller import SessionController
from .scheduler import Scheduler
from .view import SessionView

__all__ = ["SessionController", "Scheduler", "SessionView"]
