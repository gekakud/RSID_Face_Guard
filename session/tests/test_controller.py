"""Behavioural tests for the extracted SessionController (B1 / T1).

Everything runs off-device: a manual-clock FakeScheduler drives holds, retries
and timeouts deterministically (see conftest.py). These lock in the FR-SESS-*
/ BR-05 behaviour the extraction had to preserve.
"""

import config
from session.tests.conftest import FakeHost, FakeRelay


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
        DEVICE_MODE="face_only",
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
        DEVICE_MODE="face_only",
        AUTH_RETRY_INTERVAL_SEC=5.0,
        AUTH_SESSION_TIMEOUT_SEC=10.0,
    )

    c.on_user_tapped()
    sched.advance(10000)  # session timeout with no match

    assert view.calls[-1] == ("idle",)
    assert c.session_active is False


def test_tap_ignored_in_card_mode(make_controller, host):
    c = make_controller(DEVICE_MODE="card_and_face")
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
    c = make_controller(DEVICE_MODE="face_only")
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


# --------------------------------------------------------------------------- #
# Init mode -- always the entry state (T16 / rev 1.3)
# --------------------------------------------------------------------------- #

def test_disabled_init_mode_enters_then_immediately_ends(make_controller, sched, view, preview):
    # INIT_MODE_ENABLED=False no longer skips entry: we enter (single path),
    # then hand off to normal operation via a delay-0 end. No scan window, no
    # lingering overlay, no camera preview.
    from observability import events as ev
    ev.clear()

    c = make_controller(INIT_MODE_ENABLED=False)
    c.start_init_mode()

    # Entry happened and the lifecycle event fired exactly as when enabled.
    assert c.init_mode_active is True
    assert [e["type"] for e in ev.snapshot()] == ["init_mode_entered"]
    # Zero-length window: no scan preview / overlay while "in" init mode.
    assert ("camera",) not in view.calls
    assert ("overlay", "Init Mode") not in view.calls
    assert preview.resumed == 0

    # The delay-0 end fires on the first clock tick -> back to idle.
    sched.advance(0)
    assert c.init_mode_active is False
    assert view.calls[-1] == ("idle",)


def test_disabled_init_mode_does_not_scan(make_controller, sched, view, preview):
    seen = []
    payload = {"door_id": "d1", "site_id": "s1", "customer_id": "c1"}
    c = make_controller(
        INIT_MODE_ENABLED=False,
        qr_payload=payload,
        on_qr_payload=lambda p: seen.append(p),
    )
    c.start_init_mode()
    sched.advance(1000)

    # No scan interval was scheduled, so the QR is never decoded / bound.
    assert seen == []
    assert view.calls[-1] == ("idle",)


def test_zero_duration_falls_back_to_immediate_end(make_controller, sched, view, preview):
    # Enabled but a zero-length duration collapses onto the same single path.
    c = make_controller(INIT_MODE_ENABLED=True, INIT_MODE_DURATION_SEC=0.0)
    c.start_init_mode()

    assert ("overlay", "Init Mode") not in view.calls
    assert preview.resumed == 0
    sched.advance(0)
    assert c.init_mode_active is False
    assert view.calls[-1] == ("idle",)


def test_start_init_mode_when_already_active_re_enters(make_controller, sched, view):
    # A second start (re-provision) still enters + re-emits the event.
    from observability import events as ev
    ev.clear()

    c = make_controller(INIT_MODE_DURATION_SEC=8.0)
    c.start_init_mode()
    c.start_init_mode()
    assert c.init_mode_active is True
    assert [e["type"] for e in ev.snapshot()].count("init_mode_entered") == 2


# --------------------------------------------------------------------------- #
# T2 / B3 -- access decision separated from door actuation
# --------------------------------------------------------------------------- #

def test_grant_pulses_relay_then_emits_access_granted(make_controller, sched, view, host):
    # The controller -- not the auth service -- opens the door, and
    # access_granted is emitted only AFTER a successful pulse.
    from observability import events as ev
    ev.clear()
    relay = FakeRelay(opens=True)

    c = make_controller(relay=relay, WELCOME_DURATION_MS=3000)
    c.on_card_detected("card-1")

    assert relay.pulses == 1
    types = [e["type"] for e in ev.snapshot()]
    assert "access_granted" in types
    assert ("success", "Alice") in view.calls
    # access_granted is a controller event; auth_service no longer emits it
    # (auth_matched is emitted inside the fake-free real service, not here).
    granted = next(e for e in ev.snapshot() if e["type"] == "access_granted")
    assert granted["method"] == "card"


def test_relay_failure_after_match_is_access_output_failed_not_granted(make_controller, sched, view, host):
    # Matched, but the door would not open -> fail secure: a distinct
    # access_output_failed event, a failure screen, and NO access_granted.
    from observability import events as ev
    ev.clear()
    relay = FakeRelay(opens=False)

    c = make_controller(relay=relay, FAIL_DURATION_MS=2000)
    c.on_card_detected("card-1")

    assert relay.pulses == 1
    types = [e["type"] for e in ev.snapshot()]
    assert "access_output_failed" in types
    assert "access_granted" not in types
    assert ("failure", 2000) in view.calls
    # The welcome is painted optimistically at match time (so the pane isn't
    # black across the blocking pulse), but a failed strike must retract it:
    # the failure screen has to come *after* it and be the final verdict.
    assert view.calls.index(("failure", 2000)) > view.calls.index(("success", "Alice"))
    assert view.calls[-1] == ("failure", 2000)

    # The early welcome's hold timers were cancelled, so the only thing left to
    # fire is the failure hold -- one advance lands on idle, not a second time.
    sched.advance(2000)
    assert view.calls[-1] == ("idle",)


def test_relay_exception_is_treated_as_output_failure(make_controller, sched, view, host):
    from observability import events as ev
    ev.clear()
    relay = FakeRelay(raises=True)

    c = make_controller(relay=relay, FAIL_DURATION_MS=2000)
    c.on_card_detected("card-1")

    assert relay.pulses == 1
    types = [e["type"] for e in ev.snapshot()]
    assert "access_output_failed" in types
    assert "access_granted" not in types
    assert ("failure", 2000) in view.calls


def test_no_relay_wired_still_grants(make_controller, sched, view, host):
    # RUN_WITH_RELAY off / no relay injected: a match still shows the welcome
    # screen and emits access_granted (demo / no-door deployments).
    from observability import events as ev
    ev.clear()

    c = make_controller(relay=None, WELCOME_DURATION_MS=3000)
    c.on_card_detected("card-1")

    types = [e["type"] for e in ev.snapshot()]
    assert "access_granted" in types
    assert ("success", "Alice") in view.calls


def test_denied_face_never_pulses_relay(make_controller, sched, view):
    from observability import events as ev
    ev.clear()
    relay = FakeRelay(opens=True)
    host = FakeHost(result=(False, None, "no_match"))

    c = make_controller(host_service=host, relay=relay, FAIL_DURATION_MS=2000)
    c.on_card_detected("card-1")

    assert relay.pulses == 0
    types = [e["type"] for e in ev.snapshot()]
    assert "access_granted" not in types
    assert "access_output_failed" not in types
    assert ("failure", 2000) in view.calls



# --------------------------------------------------------------------------- #
# Preview lead-in: a valid badge must show a LIVE preview before the first
# attempt takes the camera (each attempt pauses the preview for exclusive UVC
# access, so firing instantly left the user staring at a paused frame).
# --------------------------------------------------------------------------- #

def test_lead_in_shows_camera_before_first_attempt(make_controller, sched, view, host):
    c = make_controller(PREVIEW_LEAD_IN_MS=700)

    c.on_card_detected("card-1")

    # Camera is up, but no auth attempt yet -- the preview owns the sensor.
    assert view.calls[0] == ("camera",)
    assert host.card_calls == []

    sched.advance(699)
    assert host.card_calls == []  # still within the lead-in

    sched.advance(1)  # lead-in elapses
    assert host.card_calls == ["card-1"]
    assert ("success", "Alice") in view.calls


def test_lead_in_keeps_preview_running_during_the_window(make_controller, sched, preview):
    """The preview must actually be streaming during the lead-in."""
    c = make_controller(PREVIEW_LEAD_IN_MS=700)

    c.on_card_detected("card-1")

    assert preview.resumed == 1
    assert preview.paused == 0  # nothing has taken the camera away yet


def test_zero_lead_in_preserves_immediate_first_attempt(make_controller, host):
    """PREVIEW_LEAD_IN_MS = 0 restores the original NFR-03 behaviour."""
    c = make_controller(PREVIEW_LEAD_IN_MS=0)

    c.on_card_detected("card-1")

    assert host.card_calls == ["card-1"]


def test_session_ending_during_lead_in_cancels_the_attempt(make_controller, sched, host):
    """A session torn down mid-lead-in must not fire a late auth attempt."""
    c = make_controller(PREVIEW_LEAD_IN_MS=700)

    c.on_card_detected("card-1")
    assert host.card_calls == []

    c._end_session()      # e.g. timeout / teardown before the lead-in elapses
    sched.advance(5000)

    assert host.card_calls == []  # the pending attempt was cancelled

