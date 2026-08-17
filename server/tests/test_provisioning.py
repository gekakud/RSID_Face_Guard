"""End-to-end tests for QR issuance, registration, expiry and status intake.

The most important tests here are the ones marked "device compatibility": they
run a server-generated QR image through the *real, unmodified* device verifier
(qr_scanner/qr_scanner.py). Everything else could pass while the device still
refuses every code we produce, so those are the ones worth keeping green.
"""

import base64
import io
import re
import time
from datetime import timedelta

import pytest

from server import signing, timeutil

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# numpy/pyzbar are only needed for the device-compatibility tests; the rest of
# the suite runs fine without them (see server/requirements.txt).
try:
    import numpy as np
    from PIL import Image

    from qr_scanner.qr_scanner import QRScanner

    DEVICE_VERIFIER_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional test deps
    DEVICE_VERIFIER_AVAILABLE = False

needs_device_verifier = pytest.mark.skipif(
    not DEVICE_VERIFIER_AVAILABLE,
    reason="numpy/pyzbar/qr_scanner not available",
)


def _frame(data_uri: str):
    """data:image/png;base64,... -> RGB numpy frame, as the device camera sees it."""
    png = base64.b64decode(data_uri.split(",", 1)[1])
    return np.array(Image.open(io.BytesIO(png)).convert("RGB"))


def _register(client, qr_body, **overrides):
    body = {
        "token": qr_body["token"],
        "nonce": qr_body["nonce"],
        "mac": "aa:bb:cc:dd:ee:ff",
        "device_type": "F455",
        "fw_version": "6.1.0",
        "app_version": "face-guard",
    }
    body.update(overrides)
    return client.post("/devices/register", json=body)


# =====================================================
# QR generation
# =====================================================

def test_generate_qr_returns_signed_payload(qr):
    body = qr()

    assert TS_PATTERN.match(body["issued_at"]), body["issued_at"]
    # The device parses expires_at with a literal strptime format; drift here
    # makes every code we issue unreadable.
    assert TS_PATTERN.match(body["expires_at"]), body["expires_at"]

    payload = body["payload"]
    assert payload["schema"] == "acme.provisioning-qr.v1"
    assert payload["command"] == "provision_device"
    assert payload["server_url"] == "http://testserver"
    assert payload["provisioning_token"] == body["token"]
    assert payload["signature"]["algorithm"] == "Ed25519"
    assert body["qr_png"].startswith("data:image/png;base64,")


def test_generate_qr_tokens_are_unique(qr):
    assert qr()["token"] != qr()["token"]


def test_pending_tokens_lists_unredeemed(client, qr):
    body = qr()
    tokens = client.get("/tokens").json()
    assert [t["token"] for t in tokens] == [body["token"]]
    assert tokens[0]["expired"] is False

    _register(client, body)
    assert client.get("/tokens").json() == []


# =====================================================
# Device compatibility -- server QR vs the real device verifier
# =====================================================

@needs_device_verifier
def test_real_device_verifier_accepts_our_qr(qr):
    body = qr()
    scanned = QRScanner().scan(_frame(body["qr_png"]))

    assert scanned is not None, "the device would reject this QR"
    assert scanned["provisioning_token"] == body["token"]
    assert scanned["door_id"] == "main-entrance"


@needs_device_verifier
def test_every_generated_qr_is_readable_by_the_device(qr):
    """The regression test for signing._decodes().

    The server re-signs with a fresh nonce until the decoder reads the image it
    just produced. zbar reads version-17+ symbols reliably (unlike OpenCV, which
    dudded ~1 in 20), so this asserts every generated code is readable by the
    device's real decoder. 25 samples keeps the regression net wide even though
    zbar rarely needs a retry; the run is cheap.
    """
    for index in range(25):
        body = qr(door_id=f"door-{index:03d}")
        assert QRScanner().scan(_frame(body["qr_png"])) is not None, (
            f"generated an undecodable QR on sample {index}"
        )


@needs_device_verifier
def test_real_device_verifier_rejects_replayed_qr(qr):
    frame = _frame(qr()["qr_png"])
    scanner = QRScanner()

    assert scanner.scan(frame) is not None
    # Same nonce a second time -- the device's replay guard must fire.
    assert scanner.scan(frame) is None


@needs_device_verifier
def test_real_device_verifier_rejects_expired_qr():
    # Build a payload that was issued in the past so it is already expired,
    # rather than sleeping out a real validity window.
    past = timeutil.utcnow() - timedelta(minutes=30)
    payload = signing.build_payload("acme", "hq", "door", "tok", 1, now=past)
    frame = np.array(
        Image.open(io.BytesIO(signing.render_qr_png(payload))).convert("RGB")
    )

    assert QRScanner().scan(frame) is None


# =====================================================
# Registration
# =====================================================

def test_register_issues_credentials(client, qr):
    response = _register(client, qr())
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["device_id"]
    assert body["device_token"]
    assert body["door_id"] == "main-entrance"
    assert body["heartbeat_interval_sec"] > 0

    devices = client.get("/devices").json()
    assert len(devices) == 1
    assert devices[0]["mac"] == "aa:bb:cc:dd:ee:ff"
    # Registered but never heard from -> offline until the first heartbeat.
    assert devices[0]["online"] is False


def test_register_rejects_reused_token(client, qr):
    body = qr()
    assert _register(client, body).status_code == 200
    assert _register(client, body).status_code == 409


def test_register_rejects_unknown_token(client):
    response = client.post("/devices/register", json={"token": "nope"})
    assert response.status_code == 404


def test_register_rejects_expired_token(client, qr):
    body = qr(validity_minutes=0)
    # Timestamps are second-resolution, so wait out the second the code was
    # issued in before asserting it is expired.
    time.sleep(1.1)
    assert _register(client, body).status_code == 400


def test_register_rejects_mismatched_nonce(client, qr):
    assert _register(client, qr(), nonce="not-the-right-nonce").status_code == 400


# =====================================================
# Status intake
# =====================================================

def test_status_marks_device_online(client, qr):
    creds = _register(client, qr()).json()

    response = client.post(
        f"/devices/{creds['device_id']}/status",
        headers={"Authorization": f"Bearer {creds['device_token']}"},
        json={"status": "online", "metadata": {"user_count": 42, "camera_available": True}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    device = client.get(f"/devices/{creds['device_id']}").json()
    assert device["online"] is True
    assert device["status"] == "online"
    assert device["metadata"]["user_count"] == 42
    assert len(device["history"]) == 1


def test_status_rejects_bad_token(client, qr):
    creds = _register(client, qr()).json()

    missing = client.post(f"/devices/{creds['device_id']}/status", json={"status": "online"})
    assert missing.status_code == 401

    wrong = client.post(
        f"/devices/{creds['device_id']}/status",
        headers={"Authorization": "Bearer garbage"},
        json={"status": "online"},
    )
    assert wrong.status_code == 401


def test_status_rejects_other_devices_id(client, qr):
    first = _register(client, qr()).json()
    second = _register(client, qr()).json()

    # Valid token, but pointed at somebody else's device.
    response = client.post(
        f"/devices/{second['device_id']}/status",
        headers={"Authorization": f"Bearer {first['device_token']}"},
        json={"status": "online"},
    )
    assert response.status_code == 403


def test_status_history_is_capped(client, qr, monkeypatch):
    from server import config

    monkeypatch.setattr(config, "STATUS_HISTORY_LIMIT", 3)
    creds = _register(client, qr()).json()
    headers = {"Authorization": f"Bearer {creds['device_token']}"}

    for index in range(6):
        client.post(
            f"/devices/{creds['device_id']}/status",
            headers=headers,
            json={"status": "online", "metadata": {"n": index}},
        )

    history = client.get(f"/devices/{creds['device_id']}").json()["history"]
    assert len(history) == 3
    assert [h["metadata"]["n"] for h in history] == [5, 4, 3]


def test_device_goes_offline_after_timeout(client, qr, monkeypatch):
    from server import config

    creds = _register(client, qr()).json()
    client.post(
        f"/devices/{creds['device_id']}/status",
        headers={"Authorization": f"Bearer {creds['device_token']}"},
        json={"status": "online"},
    )
    assert client.get("/devices").json()[0]["online"] is True

    # Shrink the window rather than waiting it out.
    monkeypatch.setattr(config, "HEARTBEAT_TIMEOUT_SEC", -1)
    assert client.get("/devices").json()[0]["online"] is False


def test_unknown_device_detail_is_404(client):
    assert client.get("/devices/does-not-exist").status_code == 404


# =====================================================
# Dashboard auth
# =====================================================

def test_dashboard_requires_basic_auth_when_configured(client, monkeypatch):
    from server import config

    monkeypatch.setattr(config, "ADMIN_USER", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "secret")

    assert client.get("/devices").status_code == 401
    assert client.get("/devices", auth=("admin", "wrong")).status_code == 401
    assert client.get("/devices", auth=("admin", "secret")).status_code == 200

    # Devices must still be able to register and report with auth turned on.
    assert client.get("/healthz").status_code == 200
    assert client.post("/devices/register", json={"token": "nope"}).status_code == 404


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert TS_PATTERN.match(body["server_time"])


# =====================================================
# Dashboard pages render
# =====================================================

def test_dashboard_pages_render(client, qr):
    creds = _register(client, qr()).json()
    client.post(
        f"/devices/{creds['device_id']}/status",
        headers={"Authorization": f"Bearer {creds['device_token']}"},
        json={"status": "online", "metadata": {"user_count": 7, "camera_available": True}},
    )

    assert "Devices" in client.get("/").text
    assert "Generate QR code" in client.get("/new").text

    detail = client.get(f"/device/{creds['device_id']}")
    assert detail.status_code == 200
    assert "main-entrance" in detail.text
    assert "user_count" in detail.text
