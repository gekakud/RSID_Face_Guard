"""The binding flow, shared by both GUIs.

gui_qt and gui_web have separate (near-identical) window classes, so this holds
the logic they both need: register when a QR is scanned, persist the result, and
keep a heartbeat running. The GUIs supply a metadata callback and a completion
callback; nothing Qt-specific lives here.
"""

import threading
from typing import Callable, Optional

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
    ):
        self.device_type = device_type
        self._metadata_fn = metadata_fn
        self._status_fn = status_fn
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
            self.identity, metadata_fn=self._metadata_fn, status_fn=self._status_fn
        )
        self._heartbeat.start()

    def _stop_heartbeat(self, timeout: Optional[float] = None) -> None:
        if self._heartbeat is not None:
            self._heartbeat.stop(timeout)
            self._heartbeat = None

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """Stop heartbeating. Pass a timeout to wait for the thread to finish."""
        self._stop_heartbeat(timeout)
