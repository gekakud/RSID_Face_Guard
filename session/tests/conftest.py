"""Off-device test doubles + fixtures for SessionController.

The controller is pure Python (no Qt, no rsid_py), so these fakes let the whole
session state machine run deterministically in-process: a manual-clock
``FakeScheduler`` (no real timers/threads), a recording ``FakeView``, and a
scripted ``FakeHost``.
"""

import os
import sys

import pytest

# Repo root on sys.path so ``import config`` / ``import session`` resolve when
# pytest is pointed straight at session/tests.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config  # noqa: E402
from session import SessionController  # noqa: E402


class FakeScheduler:
    """A manual-clock scheduler: nothing fires until the test advances time.

    ``call_later`` / ``call_interval`` register callbacks against a virtual
    millisecond clock; ``advance(ms)`` fires everything due in order. This makes
    holds, retries and timeouts fully deterministic without real QTimers.

    ``run_in_thread`` and ``post_to_ui`` both execute synchronously, so an auth
    attempt resolves inline (the real Qt/thread marshalling is exercised
    separately in the web layer, not here).
    """

    def __init__(self):
        self.now_ms = 0
        self._seq = 0
        # handle -> [due_ms, fn, interval_or_None, cancelled]
        self._timers = {}

    # --- Scheduler protocol ---
    def call_later(self, delay_ms, fn):
        return self._add(delay_ms, fn, interval=None)

    def call_interval(self, interval_ms, fn):
        return self._add(interval_ms, fn, interval=int(interval_ms))

    def cancel(self, handle):
        t = self._timers.get(handle)
        if t is not None:
            t[3] = True
            self._timers.pop(handle, None)

    def post_to_ui(self, fn):
        fn()

    # --- test helpers ---
    def _add(self, delay_ms, fn, interval):
        self._seq += 1
        handle = self._seq
        self._timers[handle] = [self.now_ms + int(delay_ms), fn, interval, False]
        return handle

    def run_in_thread(self, fn):
        """Synchronous stand-in for a daemon worker thread."""
        fn()

    def advance(self, ms):
        """Advance the virtual clock by ``ms``, firing due callbacks in order.

        Re-evaluates after each callback so a callback that (re)schedules or
        cancels timers is honoured -- matching real timer semantics.
        """
        target = self.now_ms + int(ms)
        for _ in range(10000):  # guard against zero-interval loops
            due = [
                (h, t) for h, t in self._timers.items()
                if not t[3] and t[0] <= target
            ]
            if not due:
                break
            handle, timer = min(due, key=lambda ht: (ht[1][0], ht[0]))
            self.now_ms = timer[0]
            interval = timer[2]
            if interval is None:
                self._timers.pop(handle, None)
            else:
                timer[0] = self.now_ms + interval  # reschedule the interval
            timer[1]()
        self.now_ms = target

    @property
    def pending(self):
        return [h for h, t in self._timers.items() if not t[3]]


class FakeView:
    """Records every screen transition the controller drives, in order."""

    def __init__(self):
        self.calls = []

    def show_camera(self):
        self.calls.append(("camera",))

    def show_success(self, name):
        self.calls.append(("success", name))

    def show_failure(self, hold_ms):
        self.calls.append(("failure", hold_ms))

    def show_unavailable(self, hold_ms):
        self.calls.append(("unavailable", hold_ms))

    def show_scanning(self):
        self.calls.append(("scanning",))

    def show_idle(self):
        self.calls.append(("idle",))

    def show_overlay(self, text):
        self.calls.append(("overlay", text))

    def hide_overlay(self):
        self.calls.append(("hide_overlay",))

    def names(self):
        return [c[0] for c in self.calls]


class FakePreview:
    def __init__(self):
        self.resumed = 0
        self.paused = 0

    def resume(self):
        self.resumed += 1

    def pause(self):
        self.paused += 1


class FakeHost:
    """Scripted authenticator. ``result`` is (success, name, permission).

    ``raises`` forces the worker's except-path. Records card-session marks and
    which auth entry point was used.
    """

    def __init__(self, result=(True, "Alice", "employee"), raises=False, user_id=42):
        self.result = result
        self.raises = raises
        self.card_calls = []
        self.face_only_calls = 0
        self.session_active_marks = 0
        self.session_done_marks = 0
        # Mirrors AuthenticationService.last_user_id: the neutral user_id of the
        # most recent decision, which the controller emits (never name/card_id).
        self._user_id = user_id
        self.last_user_id = None
        self.resolve_calls = []
        # card_only lookup result; overridden per-test.
        self.cardholder = {"user_id": user_id, "name": "Emma Stone", "active": True}

    def resolve_cardholder(self, card_id):
        self.resolve_calls.append(card_id)
        return self.cardholder

    def authenticate_with_card_and_face(self, card_id):
        self.card_calls.append(card_id)
        self.last_user_id = None
        if self.raises:
            raise RuntimeError("boom")
        if self.result[0]:
            self.last_user_id = self._user_id
        return self.result

    def authenticate_face_only(self):
        self.face_only_calls += 1
        self.last_user_id = None
        if self.raises:
            raise RuntimeError("boom")
        if self.result[0]:
            self.last_user_id = self._user_id
        return self.result

    def mark_card_session_active(self):
        self.session_active_marks += 1

    def mark_card_session_done(self):
        self.session_done_marks += 1


class FakeRelay:
    """Scriptable Access Output Service. Records pulses; ``opens`` controls
    whether each pulse reports success. ``raises`` forces the except-path."""

    def __init__(self, opens=True, raises=False):
        self.opens = opens
        self.raises = raises
        self.pulses = 0

    def __call__(self):
        self.pulses += 1
        if self.raises:
            raise RuntimeError("relay boom")
        return self.opens


class FakeQRScanner:
    """Yields ``payload`` once, then None (so a second frame decodes nothing)."""

    def __init__(self, payload=None):
        self._payload = payload
        self.scan_calls = 0

    def scan(self, frame):
        self.scan_calls += 1
        result, self._payload = self._payload, None
        return result


@pytest.fixture()
def sched():
    return FakeScheduler()


@pytest.fixture()
def view():
    return FakeView()


@pytest.fixture()
def preview():
    return FakePreview()


@pytest.fixture()
def host():
    return FakeHost()


@pytest.fixture()
def make_controller(sched, view, preview, host):
    """Factory building a SessionController wired to the fakes.

    Keeps ``config`` deterministic: snapshots and restores anything a test
    overrides via keyword ``config_overrides``.
    """
    created = []

    def _make(host_service=None, page_ready=True, qr_payload=None,
              frame=object(), on_qr_payload=None, relay=None, **config_overrides):
        # Most tests assert on the FIRST auth attempt synchronously, so they
        # opt out of the preview lead-in by default (0 == the original
        # fire-immediately behaviour, NFR-03). Tests covering the lead-in pass
        # an explicit PREVIEW_LEAD_IN_MS.
        config_overrides.setdefault("PREVIEW_LEAD_IN_MS", 0)
        saved = {k: getattr(config, k) for k in config_overrides}
        for k, v in config_overrides.items():
            setattr(config, k, v)
        created.append(saved)

        return SessionController(
            host_service=host_service or host,
            preview_controller=preview,
            view=view,
            scheduler=sched,
            run_in_thread=sched.run_in_thread,
            relay=relay,
            qr_scanner=FakeQRScanner(qr_payload),
            latest_frame=lambda: frame,
            on_qr_payload=on_qr_payload,
            is_page_ready=lambda: page_ready,
        )

    yield _make

    for saved in created:
        for k, v in saved.items():
            setattr(config, k, v)

