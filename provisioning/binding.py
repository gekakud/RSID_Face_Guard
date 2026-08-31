"""The binding flow, kept out of the front-end.

Holds everything a UI needs but should not own: register when a QR is scanned,
persist the result, and keep a heartbeat running. The caller supplies a metadata
callback and a completion callback; nothing Qt-specific lives here.
"""

import threading
from typing import Callable, Optional

from observability import events
from observability.logging_setup import get_logger
from provisioning import client, identity as identity_store
from provisioning.heartbeat import HeartbeatWorker
from provisioning.identity import DeviceIdentity

log = get_logger("provision")


class BindingManager:
    """Owns this device's identity and its heartbeat for the app's lifetime."""

    def __init__(
        self,
        device_type=None,
        metadata_fn: Optional[Callable[[], dict]] = None,
        status_fn: Optional[Callable[[], str]] = None,
        on_revoked: Optional[Callable[[], None]] = None,
    ):
        self.device_type = device_type
        self._metadata_fn = metadata_fn
        self._status_fn = status_fn
        # GUI hook fired (on the heartbeat thread) after the device has been
        # removed server-side and its identity dropped, so the window can return
        # to the unbound "show a QR to enroll" state. Callers marshal to the UI
        # thread themselves (both GUIs use their _SignalBridge).
        self._on_revoked_ui = on_revoked
        self.identity: Optional[DeviceIdentity] = None
        self._heartbeat: Optional[HeartbeatWorker] = None

    # ------------------------------------------------------------------ boot

    def start_if_bound(self) -> bool:
        """Resume heartbeating if this device was bound on an earlier run.

        Called at startup so an already-provisioned device comes back online
        without anyone having to rescan a QR.
        """
        saved = identity_store.load()
        if saved is None:
            log.info("Device is not bound to a server yet (no identity file)")
            return False

        self.identity = saved
        log.info(
            "Device already bound: device_id=%s door_id=%s server_url=%s",
            saved.device_id, saved.door_id, saved.server_url,
        )
        self._start_heartbeat()
        return True

    # --------------------------------------------------------------- binding

    def bind_async(self, payload: dict, on_done: Callable[[bool, str], None]) -> None:
        """Register in the background, then report back via on_done(ok, message).

        Registration is a blocking HTTP call, so it must not run on the UI
        thread. on_done fires on the worker thread -- callers that touch the UI
        are responsible for marshalling it back (both GUIs use their
        _SignalBridge for exactly this).
        """
        threading.Thread(
            target=self._bind, args=(payload, on_done), name="binding", daemon=True
        ).start()

    def _bind(self, payload: dict, on_done: Callable[[bool, str], None]) -> None:
        try:
            if self.identity is not None:
                # An installer moving a device to a new door just rescans; the
                # old binding is replaced rather than refused.
                log.info(
                    "Re-binding: replacing existing identity device_id=%s (door_id=%s)",
                    self.identity.device_id, self.identity.door_id,
                )

            new_identity = client.register(payload, device_type=self.device_type)

            if not identity_store.save(new_identity):
                # (heartbeat for any previous binding is left running -- the old
                # identity is still the one on disk, so it is still the truth)
                # Registration succeeded server-side but we can't persist it, so
                # the next boot would silently come back unbound. Say so rather
                # than showing a success message that won't survive a reboot.
                on_done(False, "Registered, but could not save credentials")
                return

            self._stop_heartbeat()
            self.identity = new_identity
            self._start_heartbeat()

            door = new_identity.door_id or new_identity.device_id[:8]
            on_done(True, f"Device registered\n{door}")

        except client.RegistrationError as exc:
            log.error("Registration failed: %s", exc)
            on_done(False, str(exc))
        except Exception as exc:
            log.exception("Unexpected error during registration")
            on_done(False, f"Registration error: {exc}")

    # ------------------------------------------------------------- heartbeat

    def _start_heartbeat(self) -> None:
        if self.identity is None:
            return
        self._heartbeat = HeartbeatWorker(
            self.identity,
            metadata_fn=self._metadata_fn,
            status_fn=self._status_fn,
            on_revoked=self._handle_revoked,
        )
        self._heartbeat.start()

    def _handle_revoked(self) -> None:
        """Fail-secure revocation (FR-HB-10). The server removed this device, so
        we tear the binding down and hand off to the GUI to return to init mode.

        Runs on the heartbeat thread. The order is deliberate and must not change:

          1. Emit ``device_revoked`` and *flush* it (plus any still-buffered
             events) to the server while the credential is STILL valid --
             ``_flush_events`` early-returns once ``self.identity`` is None, so
             this has to happen before step 3.
          2. Stop the bounded-device services (this heartbeat worker). We are
             running on that worker's own thread, so we only drop the reference
             and let the loop return; joining ourselves would deadlock.
          3. Delete the on-disk identity so a reboot can't silently rebind, and
             forget it in memory.
          4. Notify the GUI (marshalled to the UI thread by the caller), which
             wipes the local user DB incl. faceprints and re-enters init mode --
             the exact same "first start, no identity" path used at boot.

        Every step is defensive: revocation must complete even if the network,
        disk, or UI callback misbehaves. This runs on the heartbeat thread and
        must never raise back into the worker loop.
        """
        log.warning("Device revoked on the server -- fail-secure teardown, returning to init mode")

        # 1. Best-effort final notification while the token is still valid.
        try:
            events.emit("device_revoked")
            self._flush_events()
        except Exception as exc:
            log.error("Revoke flush failed (ignored): %s", exc)

        # 2. Stop bounded-device services. We are on the heartbeat thread itself,
        #    so just drop the reference; the loop returns after we do.
        self._heartbeat = None

        # 3. Destroy the credential.
        try:
            identity_store.clear()
        except Exception as exc:
            log.error("Clearing identity failed (ignored): %s", exc)
        self.identity = None

        # 4. Hand off to the GUI: wipe local user data + re-enter init mode.
        if self._on_revoked_ui is not None:
            try:
                self._on_revoked_ui()
            except Exception as exc:
                log.error("on_revoked UI callback failed: %s", exc)

    def _stop_heartbeat(self, timeout: Optional[float] = None) -> None:
        if self._heartbeat is not None:
            self._heartbeat.stop(timeout)
            self._heartbeat = None

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """Stop heartbeating, flushing any buffered events one last time.

        The heartbeat thread is what normally delivers events, so on the way
        down we emit device_shutdown and try a single best-effort synchronous
        status POST to hand off whatever is still buffered before the thread
        stops. Never raises -- shutdown must not be blocked by the network.
        """
        events.emit("device_shutdown")
        self._flush_events()
        self._stop_heartbeat(timeout)

    def _flush_events(self) -> None:
        """Best-effort final delivery of buffered events (no heartbeat thread)."""
        if self.identity is None:
            return
        pending = events.snapshot()
        if not pending:
            return
        try:
            metadata = {"events": pending}
            if self._metadata_fn:
                try:
                    metadata = {**self._metadata_fn(), "events": pending}
                except Exception:
                    pass
            status = "shutting_down"
            if self._status_fn:
                try:
                    status = self._status_fn()
                except Exception:
                    pass
            if client.post_status(self.identity, status, metadata):
                events.ack([e["event_id"] for e in pending])
        except Exception as exc:
            log.debug("Final event flush failed (ignored): %s", exc)
