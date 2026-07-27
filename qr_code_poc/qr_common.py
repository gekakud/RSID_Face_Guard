import hashlib
import hmac
import json


SECRET_KEY = b"my-random-pass"
APP_NAME = "my-device"


def create_signature(data: dict) -> str:
    canonical_data = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hmac.new(
        SECRET_KEY,
        canonical_data,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(data: dict) -> bool:
    if "signature" not in data:
        return False

    received_signature = data["signature"]

    payload_without_signature = {
        key: value for key, value in data.items() if key != "signature"
    }

    expected_signature = create_signature(payload_without_signature)

    return hmac.compare_digest(expected_signature, received_signature)