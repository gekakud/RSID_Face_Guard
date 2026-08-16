"""Test setup.

The DB path has to be redirected *before* server.config is imported, since it
reads the environment at import time -- hence the work at module scope here.
conftest.py is imported by pytest ahead of any test module, so this lands first.
"""

import os
import sys
import tempfile

# Repo root on sys.path so both `server.*` and the device's `qr_scanner` import.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="faceguard-test-"), "test.db")
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ.pop("ADMIN_USER", None)
os.environ.pop("ADMIN_PASSWORD", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import db  # noqa: E402
from server.main import app  # noqa: E402


@pytest.fixture()
def client():
    """Fresh client against an empty database for every test."""
    # The app's lifespan also does this, but the truncate below has to run
    # against a schema that already exists.
    db.init_db()

    conn = db.connect()
    try:
        conn.executescript(
            "DELETE FROM tokens; DELETE FROM devices; "
            "DELETE FROM status_history; DELETE FROM events;"
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def qr(client):
    """Generate a provisioning QR and return the parsed response body."""

    def _generate(validity_minutes=10, **overrides):
        body = {
            "tenant_id": "acme",
            "site_id": "hq",
            "door_id": "main-entrance",
            "validity_minutes": validity_minutes,
        }
        body.update(overrides)
        response = client.post("/devices/generate-qr", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    return _generate
