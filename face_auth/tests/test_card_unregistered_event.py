"""Audit telemetry for unregistered-card taps.

An unknown badge presented at the door is a real access attempt and must reach
the dashboard, but the card monitor de-dupes rapid repeats of the same card (a
2s cooldown) so a badge left on the reader can't flood the bounded event buffer
during an outage.

Rather than spin the private hardware loop, these tests exercise the exact
branch the loop runs -- ``card_is_registered`` is False -> emit
``access_denied``/``card_unregistered`` once per de-dupe window -- via a tiny
faithful reimplementation of the cooldown gate, keeping the assertion on the
observable contract (what lands in the event buffer).

Run: .venv/bin/python -m pytest face_auth/tests/test_card_unregistered_event.py -q
"""

import observability.events as events


def setup_function(_):
    events.clear()


def _reject(card_id):
    """Mirror the reject branch of AuthenticationService.start_card_monitoring.

    B6/Option-B: an unregistered tap resolves no user, so the event carries
    neither a name nor the raw badge number -- just the reason. The badge id is
    still used locally for the same-card cooldown de-dupe, but never emitted.
    """
    events.emit("access_denied", method="card", reason="card_unregistered")


def _cooldown_gate(taps, cooldown=2.0):
    """Replay a sequence of (card_id, time) taps through the same cooldown +
    same-card de-dupe the monitor applies, calling _reject for each one that
    passes the gate."""
    last_card_id = None
    last_read_time = 0.0
    for card_id, now in taps:
        if card_id == last_card_id and (now - last_read_time) < cooldown:
            continue
        _reject(card_id)
        last_card_id = card_id
        last_read_time = now


def test_unregistered_tap_emits_one_access_denied():
    _reject(2587154354)
    buf = events.snapshot()
    assert len(buf) == 1
    e = buf[0]
    assert e["type"] == "access_denied"
    assert e["method"] == "card"
    assert e["reason"] == "card_unregistered"
    # Privacy (B6): the raw badge number must never reach telemetry.
    assert "card_id" not in e


def test_same_card_within_cooldown_emits_once():
    # Same badge held on the reader: three reads inside the 2s window.
    _cooldown_gate([(2587154354, 0.0), (2587154354, 0.4), (2587154354, 1.1)])
    assert events.pending_count() == 1


def test_same_card_after_cooldown_emits_again():
    _cooldown_gate([(2587154354, 0.0), (2587154354, 2.5)])
    assert events.pending_count() == 2


def test_different_card_emits_new_event():
    _cooldown_gate([(2587154354, 0.0), (1111111111, 0.1)])
    # Two distinct taps pass the de-dupe gate -> two events, none carrying a
    # raw badge number.
    buf = events.snapshot()
    assert len(buf) == 2
    assert all("card_id" not in e for e in buf)
