"""The narrow view interface a front-end must implement for the session.

Kept deliberately small (FR-UI-01 screen states) so any front-end -- the web
UI today, anything later -- can drive the same ``SessionController`` by
implementing these methods. All methods are called on the UI thread.
"""

from typing import Optional, Protocol


class SessionView(Protocol):
    """Screen-transition surface the controller drives.

    Implementations forward to the concrete UI (e.g. ``window.deviceUI`` in the
    web front-end). Methods must be cheap and non-blocking; any hold timing is
    owned by the controller via the injected scheduler, not by the view.
    """

    def show_camera(self) -> None:
        """Switch to the live-camera / session screen."""

    def show_success(self, name: Optional[str]) -> None:
        """Show the success ("Welcome") screen, with the user's name if known."""

    def show_failure(self, hold_ms: int) -> None:
        """Show the generic "not authorized" failure screen for ``hold_ms``."""

    def show_unavailable(self, hold_ms: int) -> None:
        """Show the distinct "temporarily unavailable" screen (FR-UI-12).

        Default front-ends may alias this to ``show_failure`` until the
        dedicated screen lands (T9); it is defined here so the controller can
        already distinguish the biometric-backoff outcome from a face mismatch.
        """

    def show_idle(self) -> None:
        """Return to the idle screen (screensaver in card modes)."""

    def show_overlay(self, text: str) -> None:
        """Show the status overlay (provisioning / init-mode messages)."""

    def hide_overlay(self) -> None:
        """Hide the status overlay."""
