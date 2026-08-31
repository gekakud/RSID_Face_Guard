"""
Face authentication business logic (HostModeService).

Pure business logic -- no GUI dependency. Talks to the RealSense ID device via
rsid_py, the unified UserDatabase for user records, and
hardware.card_reader_api for Wiegand send.

This service *recognises*; it does not decide access. The relay is driven by
SessionController (session/controller.py) after it makes the access decision
(FR-FACE-04), so the only relay call here is disconnect_relay() at shutdown.
"""

import logging
import threading
import time
from typing import Callable, Optional, Tuple

import rsid_py

import config
from db import UserDatabase
from hardware.card_reader_api import (
    send_w32, initialize_wiegand_tx, disconnect_card_reader, close_wiegand_tx, get_card_id,
)
from hardware.relay_api import disconnect_relay

from observability import events
from observability.logging_setup import get_logger

log = get_logger("auth")

class HostModeService:
    """Business logic for host mode authentication."""

    def __init__(self, port: str):
        """Connect to the RealSense ID device and initialise the Wiegand transmitter.

        Args:
            port: Serial port path (e.g. '/dev/ttyACM0').
        """
        self.port = port
        identity = None
        if config.DB_MODE == "remote":
            from provisioning.identity import load as load_identity
            identity = load_identity()
            if identity is None:
                log.warning("DB_MODE=remote but device is not bound to a server yet -- "
                            "remote sync disabled until provisioning completes")
        self.user_db = UserDatabase(
            config.USER_DB_FILE,
            identity=identity,
            remote_timeout_sec=config.REMOTE_TIMEOUT_SEC,
        )
        use_remote = identity is not None
        self._error_backoff_until = 0.0  # epoch time; auth is blocked until this passes
        self.on_reconnect = None  # optional callback fired after successful reconnect
        # B6/Option-B: the neutral user_id of the most recent decision, so the
        # controller can emit access_granted/access_output_failed by user_id
        # (never name/card_id). None when the last attempt resolved no user.
        self.last_user_id = None

        self._card_monitor_thread: Optional[threading.Thread] = None
        self._card_monitor_stop_event = threading.Event()
        self._card_auth_in_progress = threading.Event()
        self.on_before_card_auth = None  # optional callback (e.g. pause camera preview)
        self.on_after_card_auth = None   # optional callback (e.g. resume camera preview)

        self._authenticator = rsid_py.FaceAuthenticator(port)
        try:
            self._authenticator.connect(self.port)
            log.info("FaceAuthenticator connected")
        except Exception as e:
            log.error("FaceAuthenticator connect failed: %s", e)
            events.emit("hardware_error", where="authenticator_connect", error=str(e))

        try:
            initialize_wiegand_tx()
            log.info("Wiegand transmitter initialized")
        except Exception as e:
            log.warning("Wiegand initialization failed: %s", e)
            events.emit("hardware_error", where="wiegand_tx_init", error=str(e))

        # The DB is fully responsible for keeping itself fresh; nothing
        # above this layer needs to know about sync scheduling. In "local"
        # mode there's no remote provider, so auto-sync is skipped entirely
        # (UserDatabase.sync_from_remote() would just no-op anyway, but
        # skipping avoids spinning up a pointless background thread).
        if config.DB_MODE == "remote":
            if use_remote:
                self._start_remote_sync()
            # else: booted unbound -- sync stays off until enable_remote_sync()
            # is called when provisioning completes (FR-DB-07).
        else:
            log.info("DB_MODE=local -- using local JSON file only, no remote sync")

    def _start_remote_sync(self) -> None:
        """Kick an immediate refresh, then start the periodic auto-sync."""
        self.user_db.start_auto_sync(
            config.DB_SYNC_INTERVAL_SEC,
            on_updated=lambda n: log.info("Auth DB refreshed (%d users)", self.user_db.count()),
        )

    def enable_remote_sync(self) -> bool:
        """Wire remote sync after the device is bound at runtime (FR-DB-07).

        Called once provisioning completes (see the GUI's binding-result
        handler): loads the freshly-saved identity, attaches the remote
        provider to the DB, and starts an immediate + periodic sync so the
        door DB is fetched right after pairing rather than only on the next
        reboot. No-op in local mode or if already remote-enabled. Returns True
        if remote sync is (now or already) active.

        Runs a blocking HTTP fetch on first sync, so callers must invoke it off
        the UI thread.
        """
        if config.DB_MODE != "remote":
            return False
        if self.user_db.is_remote_enabled():
            log.info("Remote sync already enabled -- binding refresh is a no-op")
            return True

        from provisioning.identity import load as load_identity
        identity = load_identity()
        if identity is None:
            log.warning("enable_remote_sync called but no device identity on disk yet")
            return False

        log.info("Provisioning complete -- enabling remote DB sync and fetching users")
        self.user_db.attach_remote(identity, config.REMOTE_TIMEOUT_SEC)
        try:
            fetched = self.user_db.sync_from_remote()
            log.info("Post-pairing DB sync: %d user(s) fetched", fetched)
        except Exception as e:
            log.error("Post-pairing DB sync failed: %s", e)
            events.emit("db_sync_failed", reason="exception", error=str(e))
        self._start_remote_sync()
        return True

    def disable_remote_sync(self) -> None:
        """Tear down remote DB sync on revocation (FR-HB-10).

        Stops the auto-sync thread and detaches the remote provider so the
        device stops pulling users from a server it's no longer bound to. Safe
        to call in local mode or when sync was never enabled. Idempotent.
        """
        if config.DB_MODE != "remote":
            return
        log.info("Disabling remote DB sync (device revoked)")
        try:
            self.user_db.stop_auto_sync()
        except Exception as e:
            log.error("stop_auto_sync failed (ignored): %s", e)
        self.user_db.detach_remote()

    def _reconnect(self):
        """Reset the serial connection after an error, with retries and backoff.

        After a USB disconnect the device may re-enumerate on a different ACM
        port (e.g. ttyACM1 -> ttyACM0), so we use whatever discover_devices()
        returns rather than insisting on the original port.
        """
        try:
            self._authenticator.disconnect()
        except Exception:
            pass

        delay = 1
        attempt = 0
        while True:
            time.sleep(delay)
            attempt += 1
            devices = []
            try:
                devices = rsid_py.discover_devices()
            except Exception:
                pass

            if not devices:
                log.warning("Reconnect attempt %d: no devices found (retry in %ds)", attempt, min(delay * 2, 16))
                delay = min(delay * 2, 16)
                continue

            new_port = devices[0]
            if new_port != self.port:
                log.info("Device re-enumerated on %s (was %s)", new_port, self.port)
                self.port = new_port

            try:
                self._authenticator.connect(self.port)
                log.info("FaceAuthenticator reconnected on %s after %d attempt(s)", self.port, attempt)
                self._error_backoff_until = 0.0
                if self.on_reconnect:
                    self.on_reconnect()
                return
            except Exception as e:
                log.error("Reconnect attempt %d failed: %s (retry in %ds)", attempt, e, min(delay * 2, 16))
                delay = min(delay * 2, 16)

    @staticmethod
    def _to_rsid_faceprints(fp: dict) -> rsid_py.Faceprints:
        db_faceprints = rsid_py.Faceprints()
        db_faceprints.version = fp['version']
        db_faceprints.features_type = fp['features_type']
        db_faceprints.flags = fp['flags']
        db_faceprints.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
        db_faceprints.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
        db_faceprints.enroll_descriptor = fp['enroll_descriptor']
        return db_faceprints

    def card_is_registered(self, card_id) -> bool:
        """Fast DB-only check -- no camera access -- used to reject unknown
        cards before starting an auth session (avoids spinning up the preview
        for a card that could never succeed)."""
        return self.user_db.get_user(str(card_id)) is not None

    def authenticate_with_card(self, card_id: int) -> Tuple[bool, Optional[str], Optional[str]]:
        """Authenticate using a Wiegand card ID combined with a live face scan.

        Looks up the card ID in the local DB, extracts a live faceprint from the
        camera, and matches it against the stored faceprint. On success, fires
        send_w32 and emits auth_matched; the caller actuates the door.

        Returns:
            (success, user_name, permission_level_or_error_message)
        """
        self.last_user_id = None
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            events.emit("card_unregistered")
            return False, None, "Card not registered"

        user_id = user_info.get("user_id")
        if not user_info.get("active", True):
            events.emit("access_denied", method="card", user_id=user_id,
                        reason="user_inactive")
            return False, None, "User inactive"
        self.last_user_id = user_id

        result = [None]

        def on_fp_auth_result(status, new_prints):
            if status != rsid_py.AuthenticateStatus.Success or not new_prints:
                events.emit("access_denied", method="card", user_id=user_id,
                            reason="face_extraction_failed", status=str(status))
                result[0] = (False, None, f"Face extraction failed: {status}")
                return

            fp = user_info.get('faceprints')
            if not fp:
                events.emit("access_denied", method="card", user_id=user_id,
                            reason="no_faceprints_on_file")
                result[0] = (False, None, "No faceprints on file")
                return

            db_faceprints = self._to_rsid_faceprints(fp)
            updated_faceprints = rsid_py.Faceprints()
            match_result = self._authenticator.match_faceprints(
                new_prints, db_faceprints, updated_faceprints
            )

            granted = match_result.success or (
                match_result.score is not None and match_result.score >= config.CUSTOM_THRESHOLD
            )
            # FR-FACE-03: the score-fallback value must be visible in the
            # decision log for every 1:1 decision, grant or deny.
            log.info(
                "1:1 decision: card=%s sdk_success=%s score=%s threshold=%s -> %s",
                card_id, match_result.success, match_result.score,
                config.CUSTOM_THRESHOLD, "GRANT" if granted else "DENY",
            )
            if granted:
                # Decision only (T2): recognition emits a low-level breadcrumb;
                # the controller actuates the door and emits access_granted
                # only after a successful relay pulse.
                events.emit("auth_matched", user_id=user_id, method="card",
                            score=match_result.score)
                result[0] = (True, user_info['name'], user_info['permission_level'])
            else:
                events.emit("access_denied", method="card", user_id=user_id, reason="face_mismatch")
                result[0] = (False, None, f"Face match failed (score: {match_result.score})")

        try:
            self._authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
            if result[0] is None:
                return False, None, "Authentication callback not invoked"
            return result[0]
        except Exception as e:
            log.exception("authenticate_with_card error")
            events.emit("hardware_error", where="authenticate_with_card", error=str(e))
            # Block further auth attempts for 20s while reconnect runs in background
            self._error_backoff_until = time.monotonic() + 20.0
            threading.Thread(target=self._reconnect, daemon=True).start()
            return False, None, str(e)


    def authenticate_face_only(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """Extract a live faceprint and match it against every user in the DB.

        The highest-scoring match above CUSTOM_THRESHOLD is selected. On
        success, fires send_w32 with the winning user's numeric ID and emits
        auth_matched; the caller actuates the door.

        Returns:
            (success, user_name, permission_level_or_error_message)
        """
        self.last_user_id = None
        remaining = self._error_backoff_until - time.monotonic()
        if remaining > 0:
            log.debug("Serial backoff active -- skipping auth (%.0fs remaining)", remaining)
            return False, None, "Device recovering"

        all_users = self.user_db.get_all_users()
        if not all_users:
            return False, None, "No users in database"

        result = [None]

        def on_fp_auth_result(status, new_prints):
            if status != rsid_py.AuthenticateStatus.Success or not new_prints:
                events.emit("access_denied", method="face",
                            reason="face_extraction_failed", status=str(status))
                result[0] = (False, None, f"Face extraction failed: {status}")
                return

            max_score = -100
            selected_badge_id = None
            selected_user_info = None

            for badge_id, user_info in all_users.items():
                if not user_info.get('active', True):
                    continue
                fp = user_info.get('faceprints')
                if not fp:
                    continue

                try:
                    db_faceprints = self._to_rsid_faceprints(fp)
                    updated_faceprints = rsid_py.Faceprints()
                    match_result = self._authenticator.match_faceprints(
                        new_prints, db_faceprints, updated_faceprints
                    )
                except Exception as e:
                    log.warning("Skipping user %s: bad faceprints (%s)", badge_id, e)
                    continue

                is_match = match_result.success or (
                    match_result.score is not None and match_result.score >= config.CUSTOM_THRESHOLD
                )
                if is_match and match_result.score > max_score:
                    max_score = match_result.score
                    selected_badge_id = badge_id
                    selected_user_info = user_info

            if selected_badge_id:
                selected_user_id = selected_user_info.get('user_id')
                self.last_user_id = selected_user_id
                # FR-FACE-03: log the winning score against the threshold.
                log.info(
                    "1:N decision: user=%s best_score=%s threshold=%s -> GRANT",
                    selected_user_id, max_score, config.CUSTOM_THRESHOLD,
                )
                events.emit("auth_matched", user_id=selected_user_id,
                            method="face", score=max_score)
                result[0] = (True, selected_user_info['name'], selected_user_info['permission_level'])
            else:
                log.info(
                    "1:N decision: no user reached threshold=%s -> DENY",
                    config.CUSTOM_THRESHOLD,
                )
                events.emit("access_denied", method="face", reason="no_match")
                result[0] = (False, None, "No match found")

        try:
            self._authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
            if result[0] is None:
                return False, None, "Authentication callback not invoked"
            return result[0]
        except Exception as e:
            log.exception("authenticate_face_only error")
            events.emit("hardware_error", where="authenticate_face_only", error=str(e))
            # Block further auth attempts for 20s while reconnect runs in background
            self._error_backoff_until = time.monotonic() + 20.0
            threading.Thread(target=self._reconnect, daemon=True).start()
            return False, None, str(e)

    def start_card_monitoring(self, on_card_detected: Callable[[object], None],
                               on_card_rejected: Optional[Callable[[object], None]] = None):
        """Start a background daemon thread polling the Wiegand card reader.

        This thread only *detects* card taps -- it does not touch the camera.
        Registered cards are reported once via on_card_detected(card_id) (from
        the monitoring thread -- caller must marshal back to the GUI thread if
        needed) so the GUI can drive a full auth session (preview on, retry
        loop, timeout). Unregistered cards are logged and ignored -- no camera
        spin-up for a card that could never succeed. Enforces a cooldown
        between consecutive reads of the same card, and skips reads while a
        session is already flagged in progress via mark_card_session_active()/
        mark_card_session_done() (called by the GUI around the session).
        """
        if self._card_monitor_thread is not None:
            return  # already running

        self._card_monitor_stop_event.clear()

        def _loop():
            log.info("Card reader monitoring active")
            last_card_id = None
            card_cooldown = 2.0
            last_read_time = 0.0

            while not self._card_monitor_stop_event.is_set():
                try:
                    card_id = get_card_id(timeout=0.5)
                    if config.SIMULATE_CARD_READER:
                        log.debug("[Card Reader] Read card ID: %s", card_id)

                    if card_id is not None:
                        current_time = time.time()

                        if card_id == last_card_id and (current_time - last_read_time) < card_cooldown:
                            continue

                        if self._card_auth_in_progress.is_set():
                            continue

                        if not self.card_is_registered(card_id):
                            log.warning("Card %s not registered -- ignoring", card_id)
                            # Audit telemetry: an unknown badge presented at the
                            # door is a real access attempt. Emitted here (once
                            # per de-dupe window, since the cooldown check above
                            # already dropped rapid repeats of the same card) so
                            # the same badge held on the reader can't flood the
                            # bounded event buffer during an outage.
                            events.emit("access_denied", method="card",
                                        reason="card_unregistered")
                            last_card_id = card_id
                            last_read_time = current_time
                            if on_card_rejected:
                                on_card_rejected(card_id)
                            continue

                        log.info("Card detected: %s", card_id)
                        last_card_id = card_id
                        last_read_time = current_time
                        on_card_detected(card_id)

                except Exception as e:
                    log.error("Card reader error: %s", e)
                    events.emit("hardware_error", where="card_monitor", error=str(e))
                    time.sleep(1)


            log.info("Card reader monitoring stopped")

        self._card_monitor_thread = threading.Thread(target=_loop, daemon=True)
        self._card_monitor_thread.start()

    def mark_card_session_active(self):
        """Call when a card-triggered auth session starts, to make the
        monitoring loop skip further reads until mark_card_session_done()."""
        self._card_auth_in_progress.set()

    def mark_card_session_done(self):
        """Call when a card-triggered auth session ends (success or timeout)."""
        self._card_auth_in_progress.clear()

    def stop_card_monitoring(self):
        """Stop the background card-reader monitoring thread (if running)."""
        if self._card_monitor_thread is None:
            return
        self._card_monitor_stop_event.set()
        self._card_monitor_thread.join(timeout=2)
        self._card_monitor_thread = None

    def cleanup(self):
        """Disconnect from the device and release card-reader / Wiegand / relay resources."""
        self.user_db.stop_auto_sync()
        self.stop_card_monitoring()
        try:
            self._authenticator.disconnect()
        except Exception:
            pass
        try:
            if config.AUTH_ONLY_ON_CARD:
                disconnect_card_reader()
                close_wiegand_tx()
        except Exception:
            pass
        if config.RUN_WITH_RELAY:
            try:
                disconnect_relay()
            except Exception:
                pass
