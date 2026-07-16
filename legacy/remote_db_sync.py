import requests
import uuid
from user_db import UserDatabase


# ===== CONFIG =====
SERVER_URL = "https://geine-server.onrender.com/getTicketDeviceAccessByMacAdress"
TIMEOUT_SEC = 10


# =========================================================
# Get MAC address in correct format
# =========================================================

def get_mac_address():
    mac = uuid.getnode()
    #return "88:a2:9e:55:09:6c"
    return ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(40, -8, -8))


# =========================================================
# Convert embedding → RSID faceprints format
# =========================================================

def embedding_to_faceprints(embedding):
    """
    Convert server embedding list into RealSense faceprints structure.
    Adds [0,0] as required by your system.
    """

    if not isinstance(embedding, list):
        return None

    descriptor = embedding + [0, 0]

    return {
        "version": 1,
        "features_type": 1,
        "flags": 0,
        "adaptive_descriptor_nomask": descriptor,
        "adaptive_descriptor_withmask": descriptor,
        "enroll_descriptor": descriptor,
    }


# =========================================================
# Main Sync Function
# =========================================================

def sync_remote_users_into_local(db_file, overwrite_existing=True):
    """
    Pull remote DB from server and merge into local user_database.json

    Returns:
        int: number of users updated
    """
    payload = {"mac": get_mac_address()}
    print("🌍 Contacting server with MAC:", payload["mac"])

    try:
        response = requests.post(SERVER_URL, json=payload, timeout=TIMEOUT_SEC)
    except Exception as e:
        print("❌ Network error:", e)
        return 0

    if response.status_code != 200:
        print("❌ Server returned:", response.status_code)
        print("❌ Body:", response.text[:500])
        return 0

    try:
        data = response.json()
    except Exception:
        print("❌ Invalid JSON from server")
        print("❌ Body:", response.text[:500])
        return 0

    if isinstance(data, list):
        remote_entries = data
    elif isinstance(data, dict):
        remote_entries = (
            data.get("ticketDeviceAccess") or
            (data.get("data") or {}).get("ticketDeviceAccess") or
            (data.get("result") or {}).get("ticketDeviceAccess")
        )
        if not remote_entries:
            print("ℹ Could not find entries. Top-level keys:", list(data.keys())[:50])
            return 0
    else:
        print("ℹ Unexpected JSON type:", type(data))
        return 0

    if not remote_entries:
        print("ℹ No users returned from server.")
        return 0

    db = UserDatabase(db_file)
    updated_count = 0
    seen_ids = set()

    for entry in remote_entries:

        badge_raw = entry.get("badgeID")
        if not badge_raw:
            continue

        badge_id = str(badge_raw).strip()

        # skip duplicate badgeIDs in the same sync
        if badge_id in seen_ids:
            continue
        seen_ids.add(badge_id)

        embedding = entry.get("embedding")
        if not isinstance(embedding, list) or len(embedding) == 0:
            continue

        # Build faceprints, converting any floats to ints
        try:
            descriptor = [int(x) for x in embedding] + [2, 0, 0]
        except (ValueError, TypeError) as e:
            print(f"⚠️ Skipping badgeID {badge_id}: bad embedding values ({e})")
            continue

        faceprints = {
            "version": 9,
            "features_type": 0,
            "flags": 3,
            "adaptive_descriptor_nomask": descriptor,
            "adaptive_descriptor_withmask": [0] * 515,
            "enroll_descriptor": list(descriptor),
        }

        user_obj = entry.get("user", {}) or {}
        name = user_obj.get("name", "").strip()

        user_data = {
            "name": name,
            "permission_level": "User",
            "faceprints": faceprints
        }

        if overwrite_existing or not db.get_user(badge_id):
            db.set_user(badge_id, user_data)
            updated_count += 1

    db.save()
    print(f"✅ Sync complete. {updated_count} users updated.")
    return updated_count