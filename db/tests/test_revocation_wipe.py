"""Fail-secure revocation DB tests (B5 / T6 -- FR-HB-10).

On revocation the device must wipe its local user DB *including faceprints*
(they all live in the one JSON cache) and drop remote sync so it stops pulling
users from a server it's no longer bound to. All off-device with a temp file.
"""

import json

from db.user_database import UserDatabase


def test_clear_wipes_faceprints_on_disk(tmp_path):
    db_file = tmp_path / "users.json"
    db = UserDatabase(str(db_file))
    db.set_user("badge-1", {"name": "Alice", "faceprint": "AAAA-secret-blob"})
    db.set_user("badge-2", {"name": "Bob", "faceprint": "BBBB-secret-blob"})
    assert db.count() == 2

    db.clear()

    # In-memory is empty...
    assert db.count() == 0
    assert db.get_all_users() == {}
    # ...and the on-disk cache no longer contains any faceprint material.
    on_disk = json.loads(db_file.read_text())
    assert on_disk == {}
    assert "secret-blob" not in db_file.read_text()


def test_detach_remote_disables_remote_sync():
    # A fake identity is enough to attach a remote provider.
    class _Id:
        device_id = "dev-1"
        status_url = "http://x/status"
        device_token = "t"

    db = UserDatabase(":memory-not-used:", identity=None)
    db.attach_remote(_Id())
    assert db.is_remote_enabled() is True

    db.detach_remote()
    assert db.is_remote_enabled() is False
