"""SessionController -- the UI-agnostic kiosk session state machine.

Lifted out of ``gui_web/web_window.py`` (T1). Owns:
  * the bounded auth *session* lifecycle (preview on, retry cadence, timeout);
  * auth dispatch to a worker thread and result handling (grant / card
    mismatch / face-only timeout);
  * the result holds and the explicit return to the idle screen (FR-UI-03);
  * the init-mode technician-QR scan window.

It talks to the front-end only through ``SessionView`` and ``Scheduler`` and
to the business layer through the injected ``AuthService``-like object and
callables, so it carries no Qt / rsid_py import and behaves identically to the
pre-extraction web flow (FR-SESS-01..08, BR-05).

Threading: every method here runs on the UI thread *except* the body of the
worker passed to ``run_in_thread``; that worker calls back via
``scheduler.post_to_ui`` only. State flags are therefore only mutated on the UI
thread.
"""

import config
from observability import events
from observability.logging_setup import get_logger

log = get_logger("session")


class SessionController:
    def __init__(
        self,
        *,
        host_service,
        preview_controller,
        view,
        scheduler,
        run_in_thread,
        relay=None,
        qr_scanner=None,
        latest_frame=None,
        on_qr_payload=None,
        is_page_ready=lambda: True,
    ):
        """Wire the controller to its collaborators.

        Args:
            host_service: the shared ``AuthService`` (authenticate_*,
                mark_card_session_active/done).
            preview_controller: object with ``resume()`` / ``pause()``.
            view: a ``SessionView``.
            scheduler: a ``Scheduler``.
            run_in_thread: ``callable(fn)`` running ``fn`` on a daemon worker.
            relay: optional Access Output Service ``callable() -> bool`` that
                pulses the door strike and returns whether it opened. ``None``
                (or ``RUN_WITH_RELAY`` off) means no physical door -- grants
                still show the welcome screen.
            qr_scanner: optional ``QRScanner`` for init mode.
            latest_frame: optional ``callable() -> np.ndarray | None``.
            on_qr_payload: optional ``callable(payload)`` to perform binding.
            is_page_ready: ``callable() -> bool`` guarding until UI is loaded.
        """
        self._host = host_service
        self._preview = preview_controller
        self._view = view
        self._sched = scheduler
        self._run_in_thread = run_in_thread
        self._relay = relay
        self._qr_scanner = qr_scanner
        self._latest_frame = latest_frame
        self._on_qr_payload = on_qr_payload
        self._is_page_ready = is_page_ready

        self._session_active = False
        self._session_card_id = None
        self._auth_in_progress = False
        self._retry_handle = None
        self._timeout_handle = None
        self._lead_in_handle = None
        # Set when the welcome was painted early (at match time, before the
        # relay pulse returned); the hold timers it armed live alongside it so
        # a failed pulse can retract both.
        self._match_shown = False
        self._welcome_handles = []

        self._init_mode_active = False
        self._init_timer_handle = None
        self._qr_scan_handle = None

    # --- introspection (heartbeat metadata snapshot) ------------------- #

    @property
    def session_active(self) -> bool:
        return self._session_active

    @property
    def auth_in_progress(self) -> bool:
        return self._auth_in_progress

    @property
    def init_mode_active(self) -> bool:
        return self._init_mode_active


    # --- entry points -------------------------------------------------- #

    def on_user_tapped(self) -> None:
        """Screen tap: wakes a session only in the demo face-only config."""
        if not config.AUTH_ONLY_ON_CARD and not self._init_mode_active:
            self.start_session()

    def on_card_detected(self, card_id) -> None:
        """A registered card was tapped (monitor already filtered unknowns)."""
        self.start_session(card_id=card_id)

    def on_card_rejected(self, card_id) -> None:
        """An unregistered card: brief failure, no camera/session (FR-UI-04)."""
        if self._session_active or self._init_mode_active or not self._is_page_ready():
            return
        self._view.show_failure(hold_ms=config.FAIL_DURATION_MS)
        self._sched.call_later(config.FAIL_DURATION_MS, self._view.show_idle)

    # --- session lifecycle --------------------------------------------- #

    def start_session(self, card_id=None) -> None:
        """Begin a bounded auth session: live camera, retry face-match on an
        interval, time out back to the idle screen if nothing matches."""
        if self._session_active or not self._is_page_ready():
            return
        self._session_active = True
        self._session_card_id = card_id

        if card_id is not None:
            self._host.mark_card_session_active()

        self._view.show_camera()
        self._preview.resume()

        self._retry_handle = self._sched.call_interval(
            int(config.AUTH_RETRY_INTERVAL_SEC * 1000), self._session_auth_tick
        )

        # Every auth attempt pauses the preview (the SDK needs exclusive UVC
        # access), so firing one immediately killed the camera within
        # milliseconds of starting it -- the user never saw a live frame on a
        # valid badge. Give the preview a short lead-in so real frames reach
        # the screen first, then attempt. PREVIEW_LEAD_IN_MS = 0 restores the
        # original fire-immediately behaviour (NFR-03).
        lead_in_ms = int(getattr(config, "PREVIEW_LEAD_IN_MS", 0))
        if lead_in_ms > 0:
            self._lead_in_handle = self._sched.call_later(
                lead_in_ms, self._lead_in_elapsed
            )
        else:
            self._session_auth_tick()  # first attempt immediately (NFR-03)

        self._timeout_handle = self._sched.call_later(
            int(config.AUTH_SESSION_TIMEOUT_SEC * 1000), self._session_timeout
        )

    def _lead_in_elapsed(self) -> None:
        """Fire the first auth attempt once the preview has been visible."""
        self._lead_in_handle = None
        if not self._session_active:
            return  # session torn down during the lead-in
        self._session_auth_tick()

    def _session_auth_tick(self) -> None:
        if not self._auth_in_progress:
            self._authenticate()

    def _session_timeout(self) -> None:
        if not self._session_active:
            return
        log.info("Auth session timed out with no match -- returning to idle")
        self._end_session()
        self._view.show_idle()

    def _end_session(self) -> None:
        if not self._session_active:
            return
        self._session_active = False
        self._cancel_session_timers()
        self._preview.pause()
        if self._session_card_id is not None:
            self._host.mark_card_session_done()
        self._session_card_id = None

    def _cancel_session_timers(self) -> None:
        if self._retry_handle is not None:
            self._sched.cancel(self._retry_handle)
            self._retry_handle = None
        if self._timeout_handle is not None:
            self._sched.cancel(self._timeout_handle)
            self._timeout_handle = None
        if self._lead_in_handle is not None:
            self._sched.cancel(self._lead_in_handle)
            self._lead_in_handle = None

    # --- authentication ------------------------------------------------ #

    def _authenticate(self) -> None:
        if self._auth_in_progress:
            return
        self._auth_in_progress = True
        self._run_in_thread(self._run_authentication)

    def _run_authentication(self) -> None:
        """Runs on a worker thread; marshals the result back via post_to_ui.

        On a match the door is actuated here (off the UI thread, since the
        relay pulse blocks ~3 s). The controller -- not the auth service --
        owns actuation and emits access_granted only after a successful pulse
        (T2 / FR-ACCESS); a failed pulse yields access_output_failed.

        The SDK releases the camera as soon as the match call returns, so the
        preview is resumed and the verdict screen shown *at that moment* rather
        than after the pulse -- otherwise the user stares at a frozen/black
        pane for the whole 3 s strike. Only the visual feedback moves early:
        access_granted / access_output_failed telemetry is still emitted from
        _on_auth_complete once the pulse outcome is known (T2 unchanged).
        """
        self._preview.pause()
        # The preview is now stopped for the duration of the SDK call. Tell the
        # view (on the UI thread) so a front-end can show explicit feedback
        # instead of a paused camera pane. Optional hook -- older/simpler views
        # need not implement it.
        if hasattr(self._view, "show_scanning"):
            self._sched.post_to_ui(self._view.show_scanning)
        method = "card" if self._session_card_id is not None else "face"
        resumed = False
        try:
            if self._session_card_id is not None:
                success, name, permission = self._host.authenticate_with_card_and_face(
                    self._session_card_id
                )
            else:
                success, name, permission = self._host.authenticate_face_only()

            # The SDK is done with the camera here, so give the pane back
            # immediately instead of holding it black across the relay pulse.
            if self._session_active:
                self._preview.resume()
                resumed = True

            pulsed = False
            if success:
                # Show the welcome now, together with the strike firing, rather
                # than 3 s later when the blocking pulse returns.
                self._sched.post_to_ui(lambda: self._on_match_shown(name))
                pulsed = self._open_access_point()
                if pulsed:
                    log.info("Access granted: %s (%s)", name, permission)
                else:
                    log.error("Access output FAILED after match: %s (%s)", name, permission)
            else:
                log.warning("Access denied: %s", permission)
            self._sched.post_to_ui(
                lambda: self._on_auth_complete(success, pulsed, name, method)
            )
        except Exception as exc:
            log.error("Authentication error: %s", exc)
            self._sched.post_to_ui(
                lambda: self._on_auth_complete(False, False, None, method)
            )
        finally:
            if not resumed and self._session_active:
                self._preview.resume()

    def _open_access_point(self) -> bool:
        """Actuate the door via the injected Access Output Service.

        Returns True when the strike pulsed successfully. When no relay is
        wired (``relay`` is None / ``RUN_WITH_RELAY`` off) this is a no-op that
        reports success so demo grants still show the welcome screen.
        """
        if self._relay is None:
            return True
        try:
            return bool(self._relay())
        except Exception as exc:
            log.error("Access output error: %s", exc)
            return False

    def _on_match_shown(self, name) -> None:
        """Runs on the UI thread the instant the face matched, before the relay
        pulse returns, so the welcome appears together with the strike firing.

        Only paints the screen and stops the retry loop; the grant telemetry
        stays in _on_auth_complete, which knows the pulse outcome (T2). If the
        pulse then fails, _on_auth_complete replaces this with the failure
        screen, so the early welcome is never the final word.
        """
        if not self._session_active:
            return
        self._match_shown = True
        # Stop retrying immediately so no further attempt fires during the
        # welcome hold (FR-SESS-06).
        self._cancel_session_timers()
        self._view.show_success(str(name) if name else None)
        # Web UI auto-dismiss defaults to the live-camera screen; drive back to
        # idle explicitly (FR-UI-03) as the session is torn down.
        self._welcome_handles = [
            self._sched.call_later(config.WELCOME_DURATION_MS, self._end_session),
            self._sched.call_later(config.WELCOME_DURATION_MS, self._view.show_idle),
        ]

    def _cancel_welcome_timers(self) -> None:
        """Drop the early-welcome hold timers (used when the pulse failed)."""
        for handle in self._welcome_handles:
            if handle is not None:
                self._sched.cancel(handle)
        self._welcome_handles = []

    def _on_auth_complete(self, success: bool, pulsed: bool, name, method) -> None:
        self._auth_in_progress = False
        if success and pulsed:
            # Door opened: only now is it a grant (T2 -- access_granted never
            # precedes actuation).
            events.emit("access_granted", user_id=self._host.last_user_id,
                        method=method)
            if not self._match_shown:
                # No early welcome (e.g. session ended mid-pulse): show it here.
                self._cancel_session_timers()
                self._view.show_success(str(name) if name else None)
                self._sched.call_later(config.WELCOME_DURATION_MS, self._end_session)
                self._sched.call_later(config.WELCOME_DURATION_MS, self._view.show_idle)
            self._match_shown = False
        elif success and not pulsed:
            # The early welcome is now wrong -- retract it and its hold timers.
            self._cancel_welcome_timers()
            self._match_shown = False
            # Matched but the door would not open: fail secure. Distinct from a
            # denial -- surfaced as access_output_failed, shown as a failure.
            events.emit("access_output_failed", user_id=self._host.last_user_id,
                        method=method)
            self._cancel_session_timers()
            self._view.show_failure(hold_ms=config.FAIL_DURATION_MS)
            self._sched.call_later(config.FAIL_DURATION_MS, self._end_session)
            self._sched.call_later(config.FAIL_DURATION_MS, self._view.show_idle)
        elif self._session_card_id is not None:
            # Card session, non-matching face: show denial once, return to idle
            # -- a card is either yours or it isn't (BR-05).
            self._cancel_session_timers()
            self._view.show_failure(hold_ms=config.FAIL_DURATION_MS)
            self._sched.call_later(config.FAIL_DURATION_MS, self._end_session)
            self._sched.call_later(config.FAIL_DURATION_MS, self._view.show_idle)
        # Face-only (demo) mismatch: keep retrying until session timeout
        # (FR-UI-06 returns silently), so no action here.

    # --- init mode (technician QR scan on startup) --------------------- #

    def start_init_mode(self) -> None:
        """Enter init mode -- the entry state on every start (T16 / FR-PROV-01).

        Init mode is *always* entered so provisioning is reachable via a single
        code path. ``INIT_MODE_ENABLED`` no longer gates entry: it merely picks
        the window length. When enabled (and the configured duration is > 0) we
        run a real QR-scan window that falls back to idle on timeout; otherwise
        the window has zero length -- we enter, emit the same lifecycle event,
        then schedule ``end_init_mode`` at delay 0 so we immediately hand off to
        normal operation. ``end_init_mode`` is idempotent, so an enter-then-end
        with no user-visible lingering overlay is safe.
        """
        self._init_mode_active = True
        events.emit("init_mode_entered")

        duration_ms = int(config.INIT_MODE_DURATION_SEC * 1000)
        if config.INIT_MODE_ENABLED and duration_ms > 0:
            self._view.show_camera()
            self._view.show_overlay("Init Mode")
            self._preview.resume()
            self._qr_scan_handle = self._sched.call_interval(200, self._qr_scan_tick)
            self._init_timer_handle = self._sched.call_later(
                duration_ms, self.end_init_mode
            )
        else:
            # Zero-length window: enter then immediately end (single path).
            self._init_timer_handle = self._sched.call_later(0, self.end_init_mode)

    def _qr_scan_tick(self) -> None:
        if not self._init_mode_active or self._qr_scanner is None:
            return
        frame = self._latest_frame() if self._latest_frame else None
        if frame is None:
            return
        payload = self._qr_scanner.scan(frame)
        if payload is not None:
            self._on_qr_detected(payload)

    def _cancel_init_timers(self) -> None:
        if self._init_timer_handle is not None:
            self._sched.cancel(self._init_timer_handle)
            self._init_timer_handle = None
        if self._qr_scan_handle is not None:
            self._sched.cancel(self._qr_scan_handle)
            self._qr_scan_handle = None

    def end_init_mode(self) -> None:
        if not self._init_mode_active:
            return
        self._init_mode_active = False
        self._cancel_init_timers()
        self._view.hide_overlay()
        self._preview.pause()
        self._view.show_idle()
        log.info("Init mode ended -- resuming normal operation")

    def _on_qr_detected(self, payload: dict) -> None:
        """A verified provisioning QR was found during init mode."""
        if not self._init_mode_active:
            return
        log.info(
            "Provisioning QR detected during init mode: door_id=%s site_id=%s customer_id=%s",
            payload.get("door_id"), payload.get("site_id"), payload.get("customer_id"),
        )
        # Stop scanning immediately so a second frame can't start a second
        # registration with the same single-use token.
        self._cancel_init_timers()
        if self._on_qr_payload is not None:
            self._on_qr_payload(payload)

    def cancel_all_timers(self) -> None:
        """Idempotent teardown helper for shutdown."""
        self._cancel_session_timers()
        self._cancel_init_timers()

