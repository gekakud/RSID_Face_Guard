"""The FR-FACE-06 error backoff must gate BOTH auth paths.

After an SDK fault the service blocks further attempts for 20s while a
reconnect runs. The face-only path always checked that window; the card path
set it but never read it, so a card tap walked straight into the broken SDK.

These build the service via ``object.__new__`` to skip the hardware connect in
__init__, setting only the few attributes the gated branch touches.

Run: .venv/bin/python -m pytest face_auth/tests/test_backoff_gate.py -q
"""

import time

from face_auth.auth_service import AuthService


class ExplodingDB:
    """Proves the gate short-circuits: reaching the DB means it didn't."""

    def get_user(self, _card_id):
        raise AssertionError("backoff gate let the card path through to the DB")

    def get_all_users(self):
        raise AssertionError("backoff gate let the face path through to the DB")


def _service(backoff_remaining):
    svc = object.__new__(AuthService)
    svc.last_user_id = None
    svc.user_db = ExplodingDB()
    svc._error_backoff_until = time.monotonic() + backoff_remaining
    return svc


def test_card_path_is_blocked_during_backoff():
    svc = _service(20.0)
    success, name, message = svc.authenticate_with_card_and_face(2587154354)
    assert success is False
    assert name is None
    assert message == "Device recovering"


def test_face_path_is_blocked_during_backoff():
    # The pre-existing gate -- pinned so the two paths stay symmetric.
    svc = _service(20.0)
    success, _name, message = svc.authenticate_face_only()
    assert success is False
    assert message == "Device recovering"


def test_biometric_unavailable_reflects_the_window():
    assert _service(20.0).biometric_unavailable() is True
    assert _service(-1.0).biometric_unavailable() is False


def test_card_path_proceeds_once_backoff_expired():
    # Window elapsed -> no longer gated, so it reaches the DB lookup (which
    # raises here, proving the gate is open rather than silently denying).
    svc = _service(-0.01)
    try:
        svc.authenticate_with_card_and_face(2587154354)
    except AssertionError as exc:
        assert "let the card path through" in str(exc)
    else:
        raise AssertionError("expected the DB lookup to be reached")
