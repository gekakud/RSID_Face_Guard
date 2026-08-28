"""Device event telemetry: ingestion via heartbeat, idempotency, cap, endpoint.

Events piggyback on the status heartbeat's metadata under an "events" key (see
observability/events.py). These tests drive the server the same way a real
device would -- register, then POST /devices/{id}/status with events in the
metadata -- and assert the server files them, dedupes resends, bounds the log,
strips them from stored metadata, and serves them back (optionally filtered).
"""

import uuid


def _register(client, qr):
    """Register a device and return (device_id, device_token)."""
    payload = qr()["payload"]
    resp = client.post(
        "/devices/register",
        json={"token": payload["provisioning_token"], "nonce": payload["nonce"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["device_id"], body["device_token"]


def _event(event_type, **fields):
    ev = {"event_id": str(uuid.uuid4()), "ts": "2026-08-16T10:00:00Z", "type": event_type}
    ev.update(fields)
    return ev


def _post_status(client, device_id, token, events, metadata=None):
    body = {"status": "online", "metadata": {**(metadata or {}), "events": events}}
    return client.post(
        f"/devices/{device_id}/status",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_events_ingested_and_returned(client, qr):
    device_id, token = _register(client, qr)

    sent = [
        _event("device_boot", app_version="face-guard-0.1.0"),
        _event("access_granted", user_id=47, method="card"),
        _event("relay_opened", seconds=3.0),
    ]
    assert _post_status(client, device_id, token, sent).status_code == 200

    resp = client.get(f"/devices/{device_id}/events")
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert len(got) == 3
    # Newest first.
    assert got[0]["type"] == "relay_opened"
    assert got[-1]["type"] == "device_boot"
    # Extra fields land in data; reserved keys don't.
    granted = next(e for e in got if e["type"] == "access_granted")
    assert granted["data"] == {"user_id": 47, "method": "card"}
    assert "event_id" not in granted["data"]
    assert granted["received_at"]  # server stamped it


def test_events_stripped_from_stored_metadata(client, qr):
    device_id, token = _register(client, qr)
    _post_status(
        client, device_id, token,
        [_event("device_boot")],
        metadata={"user_count": 42, "camera_available": True},
    )

    detail = client.get(f"/devices/{device_id}").json()
    # The "latest metadata" panel must not contain the raw events blob.
    assert "events" not in detail["metadata"]
    assert detail["metadata"]["user_count"] == 42


def test_duplicate_event_id_is_idempotent(client, qr):
    device_id, token = _register(client, qr)

    ev = _event("qr_accepted", door_id="main")
    # Same event delivered twice (e.g. a beat whose response was lost, resent).
    assert _post_status(client, device_id, token, [ev]).status_code == 200
    assert _post_status(client, device_id, token, [ev]).status_code == 200

    got = client.get(f"/devices/{device_id}/events").json()
    assert len(got) == 1


def test_events_filter_by_type(client, qr):
    device_id, token = _register(client, qr)
    _post_status(client, device_id, token, [
        _event("access_granted", user_id=47),
        _event("access_denied", reason="no_match"),
        _event("access_granted", user_id=48),
    ])

    granted = client.get(f"/devices/{device_id}/events?type=access_granted").json()
    assert len(granted) == 2
    assert all(e["type"] == "access_granted" for e in granted)

    denied = client.get(f"/devices/{device_id}/events?type=access_denied").json()
    assert len(denied) == 1


def test_event_log_is_bounded(client, qr, monkeypatch):
    from server import config, main
    monkeypatch.setattr(config, "EVENTS_LIMIT", 5)
    monkeypatch.setattr(main.config, "EVENTS_LIMIT", 5)

    device_id, token = _register(client, qr)
    # Post 12 events across a couple of beats; only the newest 5 survive.
    _post_status(client, device_id, token, [_event("device_boot") for _ in range(7)])
    _post_status(client, device_id, token, [_event("access_granted") for _ in range(5)])

    got = client.get(f"/devices/{device_id}/events?limit=100").json()
    assert len(got) == 5


def test_no_events_key_is_harmless(client, qr):
    device_id, token = _register(client, qr)
    # A plain heartbeat with no events at all must still succeed.
    resp = client.post(
        f"/devices/{device_id}/status",
        json={"status": "online", "metadata": {"user_count": 1}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert client.get(f"/devices/{device_id}/events").json() == []


def test_clear_events_removes_the_log(client, qr):
    device_id, token = _register(client, qr)
    _post_status(client, device_id, token, [
        _event("access_granted", user_id=47),
        _event("access_denied", reason="card_unregistered"),
        _event("device_boot"),
    ])
    assert len(client.get(f"/devices/{device_id}/events").json()) == 3

    resp = client.delete(f"/devices/{device_id}/events")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["device_id"] == device_id
    assert body["deleted"] == 3

    # Log is now empty, but the device row itself survives.
    assert client.get(f"/devices/{device_id}/events").json() == []
    assert client.get(f"/devices/{device_id}").status_code == 200


def test_clear_events_is_idempotent(client, qr):
    device_id, token = _register(client, qr)
    # Clearing an already-empty log succeeds and reports zero removed.
    resp = client.delete(f"/devices/{device_id}/events")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 0


def test_clear_events_unknown_device_404(client):
    resp = client.delete("/devices/does-not-exist/events")
    assert resp.status_code == 404


def test_clear_events_only_affects_target_device(client, qr):
    dev_a, tok_a = _register(client, qr)
    dev_b, tok_b = _register(client, qr)
    _post_status(client, dev_a, tok_a, [_event("access_granted")])
    _post_status(client, dev_b, tok_b, [_event("access_granted"), _event("device_boot")])

    client.delete(f"/devices/{dev_a}/events")

    assert client.get(f"/devices/{dev_a}/events").json() == []
    # Device B's log is untouched.
    assert len(client.get(f"/devices/{dev_b}/events").json()) == 2
