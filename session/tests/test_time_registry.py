"""time_registry mode (FR-MODE-06..08): card tap -> attendance, never the door."""

import config
from observability import events


def _ctl(make_controller, relay=None, **overrides):
    overrides.setdefault("DEVICE_MODE", "time_registry")
    return make_controller(relay=relay, **overrides)


def _attendance(recorded):
    return [e for e in recorded if e["type"] == "attendance_event"]


def test_card_records_attendance_without_relay_or_camera(
    make_controller, view, preview, host, sched, recorded
):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("12345")

    # The defining rule of this mode: an attendance terminal is not a door.
    assert relay.pulses == 0
    events_seen = _attendance(recorded)
    assert len(events_seen) == 1
    assert events_seen[0]["user_id"] == 42
    assert events_seen[0]["direction"] == "in"
    # No biometric stage, no preview, no session (same shape as card_only).
    assert host.card_calls == []
    assert host.face_only_calls == 0
    assert preview.resumed == 0
    assert "camera" not in view.names()
    assert ctl.session_active is False
    assert ("success", "Emma Stone") in view.calls


def test_selected_direction_is_journalled(make_controller, recorded):
    ctl = _ctl(make_controller)

    ctl.set_direction("out")
    ctl.on_card_detected("12345")

    assert _attendance(recorded)[0]["direction"] == "out"


def test_direction_is_sticky_across_taps(make_controller, sched, recorded):
    ctl = _ctl(make_controller)
    ctl.set_direction("out")

    ctl.on_card_detected("12345")
    sched.advance(config.WELCOME_DURATION_MS)
    ctl.on_card_detected("12345")

    # Holds until somebody presses the other option -- no auto-reset.
    assert [e["direction"] for e in _attendance(recorded)] == ["out", "out"]
    assert ctl.direction == "out"


def test_unknown_direction_is_ignored(make_controller, recorded):
    ctl = _ctl(make_controller)
    ctl.set_direction("out")

    ctl.set_direction("sideways")

    assert ctl.direction == "out"


def test_welcome_then_idle_after_hold(make_controller, view, sched):
    ctl = _ctl(make_controller)

    ctl.on_card_detected("12345")
    assert "idle" not in view.names()

    sched.advance(config.WELCOME_DURATION_MS)
    assert view.names()[-1] == "idle"


def test_reader_suppressed_across_result_hold(make_controller, host, sched):
    ctl = _ctl(make_controller)

    ctl.on_card_detected("12345")
    assert host.session_active_marks == 1
    assert host.session_done_marks == 0

    sched.advance(config.WELCOME_DURATION_MS)
    assert host.session_done_marks == 1


def test_inactive_user_records_nothing(make_controller, view, host, sched, recorded):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    host.cardholder = {"user_id": 7, "name": "Emma Stone", "active": False}
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("12345")

    assert _attendance(recorded) == []
    assert relay.pulses == 0
    assert ("failure", config.FAIL_DURATION_MS) in view.calls

    sched.advance(config.FAIL_DURATION_MS)
    assert view.names()[-1] == "idle"


def test_unknown_card_records_nothing(make_controller, view, host, recorded):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    host.cardholder = None
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("99999")

    assert _attendance(recorded) == []
    assert relay.pulses == 0
    assert ("failure", config.FAIL_DURATION_MS) in view.calls


def test_init_mode_card_records_nothing(make_controller, host, recorded):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    ctl = _ctl(make_controller, relay)
    ctl.start_init_mode()

    ctl.on_card_detected("12345")

    assert _attendance(recorded) == []
    assert relay.pulses == 0
    assert host.session_active_marks == 0


def test_screen_tap_does_not_start_a_session(make_controller, host):
    ctl = _ctl(make_controller)

    ctl.on_user_tapped()

    # FR-UI-08: the card tap is the sole trigger outside face_only.
    assert ctl.session_active is False
    assert host.face_only_calls == 0
