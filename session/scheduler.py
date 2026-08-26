"""Timer / cross-thread scheduling abstraction for the session controller.

The controller needs two capabilities that would otherwise pull in Qt:
  * delayed, cancellable callbacks (session retry ticks, timeouts, result
    holds, the init-mode window);
  * a way to marshal a value produced on a worker thread back onto the UI
    thread (the auth result).

Both are expressed here as a small Protocol so ``SessionController`` stays
pure Python. The web front-end backs ``call_later``/``cancel`` with ``QTimer``
and ``post_to_ui`` with a Qt signal.
"""

from typing import Any, Callable, Protocol


class Scheduler(Protocol):
    """Delayed-callback and UI-thread marshalling surface.

    All ``call_later`` callbacks run on the UI thread. ``post_to_ui`` is the
    only method safe to call from a background thread.
    """

    def call_later(self, delay_ms: int, fn: Callable[[], None]) -> Any:
        """Run ``fn`` on the UI thread after ``delay_ms`` (single-shot).

        Returns an opaque handle usable with ``cancel``.
        """

    def call_interval(self, interval_ms: int, fn: Callable[[], None]) -> Any:
        """Run ``fn`` on the UI thread every ``interval_ms`` until cancelled.

        Returns an opaque handle usable with ``cancel``.
        """

    def cancel(self, handle: Any) -> None:
        """Cancel a pending ``call_later``/``call_interval`` handle (idempotent)."""

    def post_to_ui(self, fn: Callable[[], None]) -> None:
        """Marshal ``fn`` onto the UI thread. Safe to call off-thread."""
