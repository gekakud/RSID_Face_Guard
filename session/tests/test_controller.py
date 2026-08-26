"""Behavioural tests for the extracted SessionController (B1 / T1).

Everything runs off-device: a manual-clock FakeScheduler drives holds, retries
and timeouts deterministically (see conftest.py). These lock in the FR-SESS-*
/ BR-05 behaviour the extraction had to preserve.
"""

import config
from session.tests.conftest import FakeHost


# --------------------------------------------------------------------------- #
# Card session -- grant
# --------------------------------------------------------------------------- #

def test_card_grant_shows_success_then_returns_to_idle(make_controller, sched, view, preview, host):
    c = make_controller(WELCOME_DURATION_MS=3000)

    c.on_card_detected("card-1")

    # First attempt fires immediately; a matching face grants.
    assert host.card_calls == ["card-1"]
    assert view.calls[0] == ("camera",)
    assert ("success", "Alice") in view.calls
    assert host.session_active_marks == 1
    assert c.session_active is True

    # Welcome hold elapses -> session ends and screen returns to idle (FR-UI-03).
    sched.advance(3000)
    assert view.calls[-1] == ("idle",)
    assert c.session_active is False
    assert host.session_done_marks == 1


def test_card_grant_stops_retrying_during_the_hold(make_controller, sched, view, host):
    # A second auth must NOT fire during the welcome hold (FR-SESS-06).
    c = make_controller(WELCOME_DURATION_MS=3000, AUTH_RETRY_INTERVAL_SEC=1.0)

    c.on_card_detected("card-1")
    sched.advance(2999)

    assert host.card_calls == ["card-1"]  # exactly one attempt


# --------------------------------------------------------------------------- #
# Card session -- mismatch (BR-05: a card is yours or it isn't)
# --------------------------------------------------------------------------- #

def test_card_mismatch_fails_once_then_idle(make_controller, sched, view, preview):
    host = FakeHost(result=(False, None, "no_match"))
    c = make_controller(host_service=host, FAIL_DURATION_MS=3000, AUTH_RETRY_INTERVAL_SEC=1.0)

    c.on_card_detected("card-9")

    assert ("failure", config.FAIL_DURATION_MS) in view.calls
    # No retry loop for a card mismatch.
    sched.advance(2999)
    assert host.card_calls == ["card-9"]

    sched.advance(1)  # hold elapses
    assert view.calls[-1] == ("idle",)
    assert c.session_active is False
    assert host.session_done_marks == 1


# --------------------------------------------------------------------------- #
# Card session -- worker exception denies safely (FR-FACE-07)
# --------------------------------------------------------------------------- #

def test_auth_exception_is_treated_as_denial(make_controller, sched, view):
    host = FakeHost(raises=True)
    c = make_controller(host_service=host, FAIL_DURATION_MS=1000)

    c.on_card_detected("card-x")

    assert ("failure", config.FAIL_DURATION_MS) in view.calls
    sched.advance(1000)
    assert view.calls[-1] == ("idle",)


# --------------------------------------------------------------------------- #
# Face-only session (demo) -- retry until match / timeout
# --------------------------------------------------------------------------- #

def test_face_only_retries_until_match(make_controller, sched, view):
    host = FakeHost(result=(False, None, "no_match"))
    c = make_controller(
        host_service=host,
        AUTH_ONLY_ON_CARD=False,
        AUTH_RETRY_INTERVAL_SEC=1.0,
        AUTH_SESSION_TIMEOUT_SEC=30.0,
    )

    c.on_user_tapped()
    assert host.face_only_calls == 1  # immediate first attempt

    # Two more retry intervals, still no match -> keeps retrying silently.
    sched.advance(2000)
    assert host.face_only_calls == 3
    assert "idle" not in view.names()

    # Now the face matches on the next tick.
    host.result = (True, "Bob", "employee")
    sched.advance(1000)
    assert ("success", "Bob") in view.calls


def test_face_only_times_out_to_idle(make_controller, sched, view):
    host = FakeHost(result=(False, None, "no_match"))
    c = make_controller(
        host_service=host,
        AUTH_ONLY_ON_CARD=False,
        AUTH_RETRY_INTERVAL_SEC=5.0,
        AUTH_SESSION_TIMEOUT_SEC=10.0,
    )

    c.on_user_tapped()
    sched.advance(10000)  # session timeout with no match

    assert view.calls[-1] == ("idle",)
    assert c.session_active is False


def test_tap_ignored_in_card_mode(make_controller, host):
    c = make_controller(AUTH_ONLY_ON_CARD=True)
    c.on_user_tapped()
    assert host.face_only_calls == 0
    assert c.session_active is False


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #

def test_start_session_ignored_when_page_not_ready(make_controller, host, view):
    c = make_controller(page_ready=False)
    c.on_card_detected("card-1")
    assert c.session_active is False
    assert host.card_calls == []
    assert view.calls == []


def test_second_card_ignored_while_session_active(make_controller, host):
    c = make_controller(WELCOME_DURATION_MS=3000)
    c.on_card_detected("card-1")
    # Grant leaves the session active through the hold; a second tap is a no-op.
    active_marks = host.session_active_marks
    c.on_card_detected("card-2")
    assert host.session_active_marks == active_marks


def test_unregistered_card_shows_brief_failure_no_session(make_controller, sched, view, host, preview):
    c = make_controller(FAIL_DURATION_MS=3000)
    c.on_card_rejected("bad-card")

    assert view.calls[0] == ("failure", config.FAIL_DURATION_MS)
    assert c.session_active is False
    assert host.card_calls == []          # no camera/auth
    assert preview.resumed == 0

    sched.advance(3000)
    assert view.calls[-1] == ("idle",)


# --------------------------------------------------------------------------- #
# Init mode
# --------------------------------------------------------------------------- #

def test_init_mode_times_out_to_idle(make_controller, sched, view, preview):
    c = make_controller(INIT_MODE_DURATION_SEC=8.0)
    c.start_init_mode()

    assert ("overlay", "Init Mode") in view.calls
    assert c.init_mode_active is True

    sched.advance(8000)
    assert ("hide_overlay",) in view.calls
    assert view.calls[-1] == ("idle",)
    assert c.init_mode_active is False


def test_init_mode_tap_does_not_start_session(make_controller, host):
    c = make_controller(AUTH_ONLY_ON_CARD=False)
    c.start_init_mode()
    c.on_user_tapped()
    assert c.session_active is False
    assert host.face_only_calls == 0


def test_init_mode_qr_detected_invokes_binding_once(make_controller, sched):
    seen = []
    payload = {"door_id": "d1", "site_id": "s1", "customer_id": "c1"}
    c = make_controller(
        INIT_MODE_DURATION_SEC=8.0,
        qr_payload=payload,
        on_qr_payload=lambda p: seen.append(p),
    )
    c.start_init_mode()

    # First 200ms scan tick decodes the QR and hands it to the binding callback.
    sched.advance(200)
    assert seen == [payload]

    # Scanning stopped immediately -> no second binding even as time passes.
    sched.advance(1000)
    assert seen == [payload]


def test_end_init_mode_is_idempotent(make_controller, view):
    c = make_controller()
    c.start_init_mode()
    c.end_init_mode()
    idle_count = view.names().count("idle")
    c.end_init_mode()  # second call must be a no-op
    assert view.names().count("idle") == idle_count

