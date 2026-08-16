"""Background heartbeat: tells the dashboard this device is alive.

A daemon thread so it never holds up shutdown, and every failure is swallowed
and logged. Losing the network must never affect door access -- the kiosk keeps
authenticating faces from the local database regardless of what this thread is
doing.
"""

import threading
from typing import Callable, Optional

from observability import events
from observability.logging_setup import get_logger
from provisioning import client
from provisioning.identity import DeviceIdentity

log = get_logger("provision")

# Backoff after a failed heartbeat, so a server that is down or asleep isn't
# hammered every interval. Reset to the normal interval on the first success.
_MAX_BACKOFF_SEC = 300


class HeartbeatWorker:
    """Posts device status on a fixed interval until stopped."""

    def __init__(
        self,
        identity: DeviceIdentity,
        metadata_fn: Optional[Callable[[], dict]] = None,
        status_fn: Optional[Callable[[], str]] = None,
    ):
        """
        Args:
            identity: credentials from registration.
            metadata_fn: called on each beat for the current metadata dict.
                A callback (rather than a snapshot) keeps this module free of
                any GUI/hardware imports.
            status_fn: called on each beat for the status string.
        """
        self.identity = identity
        self._metadata_fn = metadata_fn or (lambda: {})
        self._status_fn = status_fn or (lambda: "online")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="heartbeat", daemon=True
        )
        self._thread.start()
        log.info(
            "Heartbeat started: every %ss to %s",
            self.identity.heartbeat_interval_sec, self.identity.server_url,
        )

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal the thread to stop, optionally waiting for it to finish.

        Without a timeout this returns immediately and a beat already in flight
        may still complete -- fine at shutdown, but callers that need the worker
        to be truly done (re-binding, tests) should pass one. Allow for
        config.REMOTE_TIMEOUT_SEC, since the thread can be blocked in a POST.
        """
        self._stop.set()
        if timeout is not None and self._thread is not None:
            self._thread.join(timeout)

    def _collect(self) -> tuple:
        """Gather status + metadata, tolerating a broken callback."""
        try:
            return self._status_fn(), self._metadata_fn()
        except Exception as exc:
            log.warning("Heartbeat metadata callback failed: %s", exc)
            return "online", {}

    def _run(self) -> None:
        interval = max(5, self.identity.heartbeat_interval_sec)
        delay = 0.0  # first beat goes out immediately

        while not self._stop.wait(delay):
            status, metadata = self._collect()

            # Attach buffered device events (guaranteed delivery): snapshot now,
            # and only ack() -- dropping them from the buffer -- once the POST
            # is confirmed 2xx below. A failed beat leaves them for the next one.
            pending = events.snapshot()
            if pending:
                metadata = {**metadata, "events": pending}

            try:
                ok = client.post_status(self.identity, status, metadata)
            except Exception as exc:
                # post_status already handles the expected failures; this is the
                # last line of defence so the thread can never die silently.
                log.error("Unexpected heartbeat error: %s", exc)
                ok = False

            if ok:
                events.ack(len(pending))
                delay = interval
            else:
                delay = min(max(interval, delay * 2 or interval), _MAX_BACKOFF_SEC)
                log.debug("Heartbeat retry in %ss", delay)

        log.info("Heartbeat stopped")
