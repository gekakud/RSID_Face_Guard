"""
Unified card reader / writer hardware API.

Selects a card-reader backend based on config.CARD_READER_BACKEND:
    "gwiot_hid"    -> real GWIOT USB HID keyboard-emulation reader (evdev),
                      the main reader hardware going forward.
    "wiegand_gpio" -> real Wiegand GPIO reader (lgpio), older hardware.
    "simulated"    -> card_backends_impl.card_read_write_simulator module.

The Wiegand *transmitter* (send_w32 to the external access-control panel)
is a separate physical concern from the reader -- it always comes from
wiegand_card_writer.py (or the simulator's writer when backend=="simulated"),
regardless of which reader hardware is in use, since GWIOT is read-only.

Callers (face_auth, GUI) never need to branch on config.CARD_READER_BACKEND
themselves -- they just import from here.

Public API:
    initialize_card_reader()
    get_card_id(timeout=None) -> int | str | None
    disconnect_card_reader()
    initialize_wiegand_writer()
    send_w32(value)
    close_wiegand_writer()
"""

from observability.logging_setup import get_logger

log = get_logger("card")

import importlib
import config

_reader = None
_writer = None


def _reader_module():
    global _reader
    if _reader is None:
        names = {
            "simulated": "card_read_write_simulator",
            "gwiot_hid": "gwiot_hid_card_reader",
            "wiegand_gpio": "wiegand_card_reader",
        }
        if config.CARD_READER_BACKEND not in names:
            raise ValueError(f"Unknown CARD_READER_BACKEND: {config.CARD_READER_BACKEND!r}")
        _reader = importlib.import_module("card_backends_impl." + names[config.CARD_READER_BACKEND])
    return _reader


def _writer_module():
    global _writer
    if _writer is None:
        name = "card_read_write_simulator" if config.CARD_READER_BACKEND == "simulated" else "wiegand_card_writer"
        _writer = importlib.import_module("card_backends_impl." + name)
    return _writer


def initialize_card_reader():
    return _reader_module().initialize_card_reader()


def get_card_id(timeout=None):
    return _reader_module().get_card_id(timeout=timeout)


def disconnect_card_reader():
    if _reader is not None:
        return _reader.disconnect_card_reader()


def initialize_wiegand_writer():
    return _writer_module().initialize_wiegand_tx()


def send_w32(value: int):
    return _writer_module().send_w32(value)


def close_wiegand_writer():
    if _writer is not None:
        return _writer.close_wiegand_tx()
