from pathlib import Path

import pytest

from provisioning.hardware_identity import get_serial_number
from qr_scanner.payload import parse_payload


def test_board_serial_and_cpuinfo_fallback(monkeypatch):
    monkeypatch.delenv("DEVICE_SERIAL_OVERRIDE", raising=False)
    def read(path):
        return "ABCDEF12\x00" if "device-tree" in str(path) else "Serial : 0000000012345678\n"
    monkeypatch.setattr(Path, "read_text", read)
    assert get_serial_number() == "00000000abcdef12"
    def fallback(path):
        if "device-tree" in str(path):
            raise FileNotFoundError()
        return "Serial : 0000000012345678\n"
    monkeypatch.setattr(Path, "read_text", fallback)
    assert get_serial_number() == "0000000012345678"


def test_missing_board_serial_never_uses_camera_or_random_id(monkeypatch):
    monkeypatch.delenv("DEVICE_SERIAL_OVERRIDE", raising=False)
    monkeypatch.setattr(Path, "read_text", lambda path: "")
    with pytest.raises(ValueError, match="DEVICE_SERIAL_OVERRIDE"):
        get_serial_number()
    monkeypatch.setenv("DEVICE_SERIAL_OVERRIDE", "dev-win-my-pc")
    assert get_serial_number() == "dev-win-my-pc"


@pytest.mark.parametrize("text", ['[]', '{}', '{"installationId":"a","deviceType":"b","serverLink":"file:///tmp/x"}'])
def test_invalid_unsigned_payloads(text):
    with pytest.raises(ValueError):
        parse_payload(text)
