"""Server API behaviour that outlives provisioning: status intake, device
removal, customer/site/door CRUD, dashboard auth and page rendering.

Devices here are created through the unsigned installation pairing flow (the
`paired` fixture); pairing itself is covered by test_installations.py.
"""

import re

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _headers(creds):
    return {"Authorization": f"Bearer {creds['device_token']}"}


# =====================================================
# Status intake
# =====================================================

def test_status_rejects_bad_token(client, paired):
    creds = paired()

    missing = client.post(f"/devices/{creds['device']}/status", json={"status": "online"})
    assert missing.status_code == 401

    wrong = client.post(
        f"/devices/{creds['device']}/status",
        headers={"Authorization": "Bearer garbage"},
        json={"status": "online"},
    )
    assert wrong.status_code == 401


def test_status_rejects_other_devices_id(client, paired):
    first, second = paired(), paired(door_id="back-door")

    # Valid token, but pointed at somebody else's device.
    response = client.post(
        f"/devices/{second['device']}/status",
        headers=_headers(first),
        json={"status": "online"},
    )
    assert response.status_code == 403


def test_status_history_is_capped(client, paired, monkeypatch):
    from server import config

    monkeypatch.setattr(config, "STATUS_HISTORY_LIMIT", 3)
    creds = paired()

    for index in range(6):
        client.post(
            f"/devices/{creds['device']}/status",
            headers=_headers(creds),
            json={"status": "online", "metadata": {"n": index}},
        )

    history = client.get(f"/devices/{creds['device']}").json()["history"]
    assert len(history) == 3
    assert [h["metadata"]["n"] for h in history] == [5, 4, 3]


def test_device_goes_offline_after_timeout(client, paired, monkeypatch):
    from server import config

    creds = paired()
    client.post(
        f"/devices/{creds['device']}/status",
        headers=_headers(creds),
        json={"status": "online"},
    )
    assert client.get("/devices").json()[0]["online"] is True

    # Shrink the window rather than waiting it out.
    monkeypatch.setattr(config, "HEARTBEAT_TIMEOUT_SEC", -1)
    assert client.get("/devices").json()[0]["online"] is False


def test_unknown_device_detail_is_404(client):
    assert client.get("/devices/does-not-exist").status_code == 404


# =====================================================
# Customer / Site / Door management
# =====================================================

def test_customer_site_door_crud_and_cascade(client):
    cust = client.post("/customers", json={"name": "acme-corp"}).json()
    assert cust["id"] and cust["name"] == "acme-corp"

    site = client.post(
        "/sites", json={"customer_id": cust["id"], "name": "tel-aviv-hq"}
    ).json()
    assert site["customer_id"] == cust["id"]

    door = client.post(
        "/doors", json={"site_id": site["id"], "name": "main-entrance"}
    ).json()
    assert door["site_id"] == site["id"]

    # Listing is scoped to the parent (cascade).
    assert [c["name"] for c in client.get("/customers").json()] == ["acme-corp"]
    assert [s["name"] for s in client.get(f"/sites?customer_id={cust['id']}").json()] == ["tel-aviv-hq"]
    assert [d["name"] for d in client.get(f"/doors?site_id={site['id']}").json()] == ["main-entrance"]
    # A different (nonexistent) parent yields nothing.
    assert client.get("/sites?customer_id=99999").json() == []


def test_create_is_idempotent_by_name(client):
    first = client.post("/customers", json={"name": "acme"}).json()
    second = client.post("/customers", json={"name": "acme"}).json()
    assert first["id"] == second["id"]
    assert len(client.get("/customers").json()) == 1


def test_site_and_door_require_existing_parent(client):
    assert client.post("/sites", json={"customer_id": 1, "name": "s"}).status_code == 404
    assert client.post("/doors", json={"site_id": 1, "name": "d"}).status_code == 404


# =====================================================
# Device removal (suspend -> device acknowledges -> purge)
# =====================================================

def test_delete_device_suspends_it(client, paired):
    creds = paired()
    resp = client.delete(f"/devices/{creds['device']}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "suspended"

    # Still listed, now flagged suspended (tombstone awaiting the device).
    device = client.get(f"/devices/{creds['device']}").json()
    assert device["state"] == "suspended"
    assert device["suspended_at"]


def test_delete_unknown_device_is_404(client):
    assert client.delete("/devices/nope").status_code == 404


def test_suspended_device_heartbeat_gets_410_and_acknowledges(client, paired):
    creds = paired()
    client.delete(f"/devices/{creds['device']}")

    # The device's next heartbeat is told it was removed (410 Gone) and the row
    # flips to revoked_ack.
    resp = client.post(
        f"/devices/{creds['device']}/status",
        headers=_headers(creds),
        json={"status": "online"},
    )
    assert resp.status_code == 410

    device = client.get(f"/devices/{creds['device']}").json()
    assert device["state"] == "revoked_ack"


def test_acknowledged_device_is_purged_on_next_list(client, paired):
    creds = paired()
    client.delete(f"/devices/{creds['device']}")
    # Acknowledge (410) -> revoked_ack.
    client.post(
        f"/devices/{creds['device']}/status",
        headers=_headers(creds),
        json={"status": "online"},
    )
    # Listing sweeps out acknowledged tombstones.
    assert client.get("/devices").json() == []
    assert client.get(f"/devices/{creds['device']}").status_code == 404


def test_active_device_heartbeat_unaffected(client, paired):
    creds = paired()
    resp = client.post(
        f"/devices/{creds['device']}/status",
        headers=_headers(creds),
        json={"status": "online"},
    )
    assert resp.status_code == 200


def test_delete_is_idempotent(client, paired):
    creds = paired()
    first = client.delete(f"/devices/{creds['device']}")
    second = client.delete(f"/devices/{creds['device']}")
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["state"] == "suspended"


# =====================================================
# Dashboard auth / health
# =====================================================

def test_dashboard_requires_basic_auth_when_configured(client, monkeypatch):
    from server import config

    monkeypatch.setattr(config, "ADMIN_USER", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "secret")

    assert client.get("/devices").status_code == 401
    assert client.get("/devices", auth=("admin", "wrong")).status_code == 401
    assert client.get("/devices", auth=("admin", "secret")).status_code == 200

    # Health and device pairing must still work with admin auth turned on.
    assert client.get("/healthz").status_code == 200
    assert client.post(
        "/installation/pair",
        json={"installationId": "nope", "mac": "AA:BB:CC:DD:EE:FF", "serial": "000000001234abcd"},
    ).status_code == 404


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert TS_PATTERN.match(body["server_time"])


def test_dashboard_pages_render(client, paired):
    creds = paired()
    client.post(
        f"/devices/{creds['device']}/status",
        headers=_headers(creds),
        json={"status": "online", "metadata": {"user_count": 7, "camera_available": True}},
    )

    assert "Devices" in client.get("/").text
    assert client.get("/new").status_code == 200

    detail = client.get(f"/device/{creds['device']}")
    assert detail.status_code == 200
    assert "main-entrance" in detail.text
    assert "user_count" in detail.text

    assert client.post("/sites", json={"customer_id": 1, "name": "s"}).status_code == 404
    assert client.post("/doors", json={"site_id": 1, "name": "d"}).status_code == 404
