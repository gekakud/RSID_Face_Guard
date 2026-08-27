"""Unit tests for the device event buffer (observability/events.py).

Focus: FR-HB-05 -- events are acknowledged by ``event_id``, never by
position, so events emitted (or ring evictions happening) while a heartbeat is
in flight are never dropped by accident.

Run: .venv/bin/python -m pytest observability/test_events.py -q
"""

import observability.events as events


def _ids(evs):
    return [e["event_id"] for e in evs]


def setup_function(_):
    events.clear()


def test_ack_removes_only_acked_ids():
    events.emit("a")
    events.emit("b")
    sent = events.snapshot()
    events.ack(_ids(sent))
    assert events.pending_count() == 0


def test_events_emitted_mid_beat_survive_ack():
    """Snapshot two events, emit a third 'while the beat is in flight',
    then ack only the two that were sent -- the third must remain."""
    events.emit("a")
    events.emit("b")
    sent = events.snapshot()          # what the heartbeat carried

    events.emit("c")                  # emitted mid-beat, NOT sent

    events.ack(_ids(sent))            # ack only the delivered ids
    remaining = events.snapshot()
    assert [e["type"] for e in remaining] == ["c"]


def test_eviction_during_beat_does_not_drop_undelivered():
    """The bug FR-HB-05 targets: if the ring evicts entries while a beat is
    in flight, positional ack drops the wrong events. Id-based ack must only
    remove the events that were actually sent."""
    max_events = events._MAX_EVENTS

    # Fill the ring, snapshot the current contents as 'sent'.
    for i in range(max_events):
        events.emit("old", i=i)
    sent = events.snapshot()
    assert len(sent) == max_events

    # Mid-beat: emit enough new events to evict the OLDEST 'sent' ones.
    overflow = 50
    for i in range(overflow):
        events.emit("new", i=i)

    # Old positional ack would popleft() len(sent) items -- wiping the whole
    # ring, including undelivered 'new' events. Id-based ack must keep them.
    events.ack(_ids(sent))
    remaining = events.snapshot()

    # None of the acked (sent) ids may remain...
    sent_ids = set(_ids(sent))
    assert not any(e["event_id"] in sent_ids for e in remaining)
    # ...and every 'new' event that survived eviction must still be buffered.
    assert all(e["type"] == "new" for e in remaining)
    assert len(remaining) == overflow


def test_ack_empty_or_unknown_is_noop():
    events.emit("a")
    events.ack([])
    events.ack(None)
    events.ack(["not-a-real-id"])
    assert events.pending_count() == 1
