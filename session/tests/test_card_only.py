"""card_only mode (FR-MODE-03): card tap -> relay, no session, no camera."""

import config


def _ctl(make_controller, relay, **overrides):
    overrides.setdefault("DEVICE_MODE", "card_only")
    return make_controller(relay=relay, **overrides)


def test_valid_card_opens_relay_without_camera(make_controller, view, preview, host, sched):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("12345")

    # Door opened, and no biometric/preview involvement at all.
    assert relay.pulses == 1
    assert host.card_calls == []
    assert host.face_only_calls == 0
    assert preview.resumed == 0
    assert "camera" not in view.names()
    assert ("success", "Emma Stone") in view.calls
    assert ctl.session_active is False


def test_welcome_then_idle_after_hold(make_controller, view, sched):
    from session.tests.conftest import FakeRelay
    ctl = _ctl(make_controller, FakeRelay(opens=True))

    ctl.on_card_detected("12345")
    assert "idle" not in view.names()

    sched.advance(config.WELCOME_DURATION_MS)
    assert view.names()[-1] == "idle"


def test_reader_suppressed_across_pulse_and_hold(make_controller, host, sched):
    from session.tests.conftest import FakeRelay
    ctl = _ctl(make_controller, FakeRelay(opens=True))

    ctl.on_card_detected("12345")
    # Held for the whole result hold (BR-04) ...
    assert host.session_active_marks == 1
    assert host.session_done_marks == 0

    sched.advance(config.WELCOME_DURATION_MS)
    # ... and released once it ends (FR-CARD-04).
    assert host.session_done_marks == 1


def test_inactive_user_is_denied_without_actuation(make_controller, view, host, sched):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    host.cardholder = {"user_id": 7, "name": "Emma Stone", "active": False}
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("12345")

    # A suspended record never opens the door (BR-01, FR-DATA-07).
    assert relay.pulses == 0
    assert ("failure", config.FAIL_DURATION_MS) in view.calls
    assert "success" not in view.names()

    sched.advance(config.FAIL_DURATION_MS)
    assert view.names()[-1] == "idle"


def test_unknown_card_is_denied_without_actuation(make_controller, view, host):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    host.cardholder = None
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("99999")

    assert relay.pulses == 0
    assert ("failure", config.FAIL_DURATION_MS) in view.calls


def test_failed_pulse_shows_failure(make_controller, view, host, sched):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=False)
    ctl = _ctl(make_controller, relay)

    ctl.on_card_detected("12345")

    # Authorised but the strike did not fire: welcome is retracted (FR-OUT-06).
    assert relay.pulses == 1
    assert ("failure", config.FAIL_DURATION_MS) in view.calls

    sched.advance(config.FAIL_DURATION_MS)
    assert view.names()[-1] == "idle"
    assert host.session_done_marks == 1


def test_relay_exception_is_fail_secure(make_controller, view):
    from session.tests.conftest import FakeRelay
    ctl = _ctl(make_controller, FakeRelay(raises=True))

    ctl.on_card_detected("12345")

    assert ("failure", config.FAIL_DURATION_MS) in view.calls


def test_card_and_face_mode_still_starts_a_session(make_controller, view, host):
    from session.tests.conftest import FakeRelay
    ctl = make_controller(relay=FakeRelay(opens=True), DEVICE_MODE="card_and_face")

    ctl.on_card_detected("12345")

    # Unchanged path: biometric session, camera on.
    assert host.card_calls == ["12345"]
    assert "camera" in view.names()


def test_tap_ignored_when_mode_is_not_face_only(make_controller, host):
    ctl = make_controller(DEVICE_MODE="card_only")

    ctl.on_user_tapped()

    # FR-UI-08: in card modes the card tap is the sole trigger.
    assert ctl.session_active is False
    assert host.face_only_calls == 0


def test_init_mode_blocks_card_only(make_controller, host):
    from session.tests.conftest import FakeRelay
    relay = FakeRelay(opens=True)
    ctl = _ctl(make_controller, relay)
    ctl.start_init_mode()

    ctl.on_card_detected("12345")

    assert relay.pulses == 0
