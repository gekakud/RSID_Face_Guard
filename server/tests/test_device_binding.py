"""Device-side binding against a real, running server.

Unlike test_provisioning.py (which drives the API through TestClient), these
tests start a live uvicorn server and exercise provisioning/client.py and
provisioning/binding.py over real HTTP -- the same code paths the Raspberry Pi
runs. That makes them the closest thing to the hardware demo that can run on a
dev box, and it catches anything TestClient would paper over.

Nothing here imports PySide6 or rsid_py, so it runs without the device SDK.
"""

import socket
import threading
import time

import pytest
import uvicorn

import config as device_config
from provisioning import binding as binding_mod
from provisioning import client as device_client
from provisioning import identity as identity_store
from server import config as server_config
from server import signing
from server.main import app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """A real uvicorn server on a loopback port, for the duration of the module."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("live server did not start")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def device_env(tmp_path, monkeypatch, live_server):
    """Point the device's identity file at a tmp dir and the QR at the live server."""
    monkeypatch.setattr(
        device_config, "DEVICE_IDENTITY_FILE", str(tmp_path / "device_identity.json")
    )
    # The QR's server_url is what the device calls back to.
    monkeypatch.setattr(server_config, "PUBLIC_BASE_URL", live_server)
    return live_server


def _issue_qr(base_url, door_id="main-entrance", validity_minutes=10):
    """Mint a token the same way POST /devices/generate-qr does."""
    import requests

    response = requests.post(
        f"{base_url}/devices/generate-qr",
        json={
            "customer_id": "acme",
            "site_id": "hq",
            "door_id": door_id,
            "network_profile": {
                "mode": "wifi",
                "wifi": {"ssid": "acme-guest", "password": "s3cr3t"},
            },
            "validity_minutes": validity_minutes,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["payload"]


# =====================================================
# provisioning/client.py
# =====================================================

def test_register_over_real_http(device_env):
    payload = _issue_qr(device_env)

    identity = device_client.register(payload, device_type="F455")

    assert identity.device_id
    assert identity.device_token
    assert identity.server_url == device_env
    assert identity.door_id == "main-entrance"
    assert identity.heartbeat_interval_sec > 0


def test_register_rejects_reused_token(device_env):
    payload = _issue_qr(device_env)
    device_client.register(payload)

    with pytest.raises(device_client.RegistrationError) as excinfo:
        device_client.register(payload)
    # The installer needs to see *why*, not just that it failed.
    assert "already used" in str(excinfo.value)


def test_register_reports_unreachable_server(device_env):
    payload = _issue_qr(device_env)
    payload["server_url"] = f"http://127.0.0.1:{_free_port()}"  # nothing listening

    with pytest.raises(device_client.RegistrationError) as excinfo:
        device_client.register(payload)
    assert "unreachable" in str(excinfo.value).lower()


def test_register_rejects_payload_without_server_url(device_env):
    payload = _issue_qr(device_env)
    payload["server_url"] = ""

    with pytest.raises(device_client.RegistrationError):
        device_client.register(payload)


def test_post_status_reaches_the_dashboard(device_env):
    import requests

    identity = device_client.register(_issue_qr(device_env))

    assert device_client.post_status(identity, "online", {"user_count": 7}) is True

    device = requests.get(f"{device_env}/devices/{identity.device_id}", timeout=10).json()
    assert device["online"] is True
    assert device["metadata"]["user_count"] == 7


def test_post_status_returns_false_on_bad_token(device_env):
    identity = device_client.register(_issue_qr(device_env))
    identity.device_token = "not-the-real-token"

    # Must not raise -- a heartbeat failure can never be allowed to reach the kiosk.
    assert device_client.post_status(identity, "online", {}) is False

def test_post_status_raises_revoked_after_removal(device_env):
    """A removed device's heartbeat gets 410, surfaced as DeviceRevokedError."""
    import requests

    identity = device_client.register(_issue_qr(device_env))
    # First beat is fine.
    assert device_client.post_status(identity, "online", {}) is True

    # Operator removes it on the dashboard.
    requests.delete(f"{device_env}/devices/{identity.device_id}", timeout=10).raise_for_status()

    # The next heartbeat must raise so the caller drops its identity.
    with pytest.raises(device_client.DeviceRevokedError):
        device_client.post_status(identity, "online", {})


# =====================================================
# provisioning/identity.py
# =====================================================

def test_identity_round_trips(device_env):
    identity = device_client.register(_issue_qr(device_env))
    assert identity_store.save(identity) is True

    loaded = identity_store.load()
    assert loaded is not None
    assert loaded.device_id == identity.device_id
    assert loaded.device_token == identity.device_token
    assert loaded.status_url.endswith(f"/devices/{identity.device_id}/status")


def test_load_returns_none_when_unbound(device_env):
    assert identity_store.load() is None


def test_load_tolerates_unknown_fields(device_env):
    """A newer server adding a field must not stop an existing device booting."""
    import json

    identity = device_client.register(_issue_qr(device_env))
    identity_store.save(identity)

    path = identity_store.identity_path()
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    data["some_future_field"] = "surprise"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    loaded = identity_store.load()
    assert loaded is not None
    assert loaded.device_id == identity.device_id


def test_load_returns_none_on_corrupt_file(device_env):
    with open(identity_store.identity_path(), "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    assert identity_store.load() is None


# =====================================================
# provisioning/binding.py -- the flow the GUIs call
# =====================================================

def _bind_and_wait(manager, payload, timeout=15):
    """Run bind_async and block until its callback fires."""
    done = threading.Event()
    captured = {}

    def on_done(ok, message):
        captured["ok"], captured["message"] = ok, message
        done.set()

    manager.bind_async(payload, on_done)
    assert done.wait(timeout), "binding callback never fired"
    return captured["ok"], captured["message"]


def test_bind_async_registers_persists_and_heartbeats(device_env):
    import requests

    manager = binding_mod.BindingManager(
        device_type="F455", metadata_fn=lambda: {"user_count": 3, "camera_available": True}
    )

    ok, message = _bind_and_wait(manager, _issue_qr(device_env))
    assert ok, message
    assert "registered" in message.lower()

    # Credentials persisted...
    saved = identity_store.load()
    assert saved is not None
    assert saved.device_id == manager.identity.device_id

    # ...and the heartbeat thread started and reported metadata on its own.
    deadline = time.time() + 10
    device = {}
    while time.time() < deadline:
        device = requests.get(
            f"{device_env}/devices/{manager.identity.device_id}", timeout=10
        ).json()
        if device.get("online"):
            break
        time.sleep(0.2)

    assert device.get("online") is True, "heartbeat never reached the server"
    assert device["metadata"]["user_count"] == 3
    manager.shutdown()


def test_bind_async_reports_failure_without_raising(device_env):
    payload = _issue_qr(device_env)
    payload["server_url"] = f"http://127.0.0.1:{_free_port()}"

    manager = binding_mod.BindingManager()
    ok, message = _bind_and_wait(manager, payload)

    assert ok is False
    assert message
    # A failed bind must leave the device unbound rather than half-configured.
    assert identity_store.load() is None
    manager.shutdown()


def test_start_if_bound_resumes_after_restart(device_env):
    """The reboot case: a bound device comes back online with no QR rescan."""
    import requests

    first = binding_mod.BindingManager(metadata_fn=lambda: {"boot": 1})
    ok, _ = _bind_and_wait(first, _issue_qr(device_env))
    assert ok
    device_id = first.identity.device_id
    # Wait for the old worker to actually finish: a beat still in flight would
    # land after the new one and overwrite its metadata. Across a real reboot
    # that cannot happen -- it is an artifact of simulating one in-process.
    first.shutdown(timeout=15)

    # Simulate a restart: brand new manager, same identity file on disk.
    second = binding_mod.BindingManager(metadata_fn=lambda: {"boot": 2})
    assert second.start_if_bound() is True
    assert second.identity.device_id == device_id

    deadline = time.time() + 10
    while time.time() < deadline:
        device = requests.get(f"{device_env}/devices/{device_id}", timeout=10).json()
        if device["metadata"].get("boot") == 2:
            break
        time.sleep(0.2)
    else:
        pytest.fail("restarted device never heartbeated")

    second.shutdown()


def test_start_if_bound_is_false_when_unbound(device_env):
    manager = binding_mod.BindingManager()
    assert manager.start_if_bound() is False
    assert manager.identity is None

def test_removal_drops_identity_and_notifies_gui(device_env, monkeypatch):
    """End-to-end: bind, remove on the server, and the heartbeat thread should
    drop the on-disk identity, go unbound, and fire the on_revoked callback."""
    import requests

    # Hand the device a short heartbeat interval at registration so its next
    # beat (which sees the 410) lands within the test's wait window rather than
    # the default 30s later.
    monkeypatch.setattr(server_config, "HEARTBEAT_INTERVAL_SEC", 1)

    revoked = threading.Event()
    manager = binding_mod.BindingManager(
        metadata_fn=lambda: {"boot": 1},
        on_revoked=lambda: revoked.set(),
    )
    ok, _ = _bind_and_wait(manager, _issue_qr(device_env))
    assert ok
    device_id = manager.identity.device_id
    assert identity_store.load() is not None

    # Wait until the device is online, so we know the heartbeat loop is running.
    deadline = time.time() + 10
    while time.time() < deadline:
        if requests.get(f"{device_env}/devices/{device_id}", timeout=10).json().get("online"):
            break
        time.sleep(0.2)

    # Remove it -- the next heartbeat should see 410 and tear down.
    requests.delete(f"{device_env}/devices/{device_id}", timeout=10).raise_for_status()

    assert revoked.wait(15), "on_revoked callback never fired"
    # Identity dropped on disk and in memory -> a reboot would come back unbound.
    assert identity_store.load() is None
    assert manager.identity is None
    manager.shutdown()


def test_rebinding_replaces_the_old_identity(device_env):
    """An installer moving a device to another door just rescans."""
    manager = binding_mod.BindingManager()

    ok, _ = _bind_and_wait(manager, _issue_qr(device_env, door_id="front-door"))
    assert ok
    first_id = manager.identity.device_id

    ok, _ = _bind_and_wait(manager, _issue_qr(device_env, door_id="back-door"))
    assert ok
    assert manager.identity.device_id != first_id
    assert manager.identity.door_id == "back-door"
    assert identity_store.load().device_id == manager.identity.device_id
    manager.shutdown()


def test_heartbeat_survives_a_broken_metadata_callback(device_env):
    """A bug in the metadata closure must not kill the heartbeat thread."""
    import requests

    def exploding_metadata():
        raise RuntimeError("boom")

    manager = binding_mod.BindingManager(metadata_fn=exploding_metadata)
    ok, _ = _bind_and_wait(manager, _issue_qr(device_env))
    assert ok

    deadline = time.time() + 10
    while time.time() < deadline:
        device = requests.get(
            f"{device_env}/devices/{manager.identity.device_id}", timeout=10
        ).json()
        if device.get("online"):
            break
        time.sleep(0.2)

    assert device.get("online") is True, "heartbeat died with the callback"
    manager.shutdown()
