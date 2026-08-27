"""Fail-secure revocation tests (B5 / T6 -- FR-HB-10, FR-STATE-02).

Verify BindingManager._handle_revoked runs its teardown in the mandated order:
the final event flush must happen while the credential is STILL valid, only
then is the identity cleared, the heartbeat dropped, and the GUI told to wipe
local data + return to init mode. Everything runs off-device with fakes.
"""

from unittest import mock

from provisioning.binding import BindingManager


class _FakeIdentity:
    device_id = "dev-1"
    door_id = "door-1"
    heartbeat_interval_sec = 30


def _make_bound_manager(on_revoked):
    mgr = BindingManager(on_revoked=on_revoked)
    mgr.identity = _FakeIdentity()
    mgr._heartbeat = object()  # stand-in; _handle_revoked only drops the ref
    return mgr


def test_revoke_flushes_before_clearing_identity():
    """The final flush must see a live identity; identity is cleared only after."""
    seen_identity_during_flush = []

    def fake_flush(self):
        # _handle_revoked calls self._flush_events(); capture the live state.
        seen_identity_during_flush.append(self.identity is not None)

    with mock.patch.object(BindingManager, "_flush_events", fake_flush), \
         mock.patch("provisioning.binding.identity_store.clear") as clear_id:
        mgr = _make_bound_manager(on_revoked=lambda: None)
        mgr._handle_revoked()

    # Flush ran once, and the identity was still valid at that moment.
    assert seen_identity_during_flush == [True]
    # Only afterwards was the on-disk identity deleted and memory cleared.
    clear_id.assert_called_once_with()
    assert mgr.identity is None


def test_revoke_drops_heartbeat_and_fires_ui_callback():
    ui_called = []

    with mock.patch.object(BindingManager, "_flush_events", lambda self: None), \
         mock.patch("provisioning.binding.identity_store.clear"):
        mgr = _make_bound_manager(on_revoked=lambda: ui_called.append(True))
        mgr._handle_revoked()

    assert mgr._heartbeat is None
    assert ui_called == [True]


def test_revoke_survives_ui_callback_failure():
    """A misbehaving GUI callback must not stop the teardown from completing."""
    def boom():
        raise RuntimeError("ui exploded")

    with mock.patch.object(BindingManager, "_flush_events", lambda self: None), \
         mock.patch("provisioning.binding.identity_store.clear") as clear_id:
        mgr = _make_bound_manager(on_revoked=boom)
        # Must not raise despite the callback blowing up.
        mgr._handle_revoked()

    clear_id.assert_called_once_with()
    assert mgr.identity is None
    assert mgr._heartbeat is None


def test_revoke_survives_flush_failure():
    """A network failure during the final flush must not abort the teardown."""
    def flush_boom(self):
        raise RuntimeError("network down")

    with mock.patch.object(BindingManager, "_flush_events", flush_boom), \
         mock.patch("provisioning.binding.identity_store.clear") as clear_id:
        mgr = _make_bound_manager(on_revoked=lambda: None)
        mgr._handle_revoked()

    # Even though the flush raised, the credential was still destroyed.
    clear_id.assert_called_once_with()
    assert mgr.identity is None
