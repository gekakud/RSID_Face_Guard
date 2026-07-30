#!/usr/bin/env python3

import queue
import threading
from pathlib import Path
from typing import Optional

from evdev import InputDevice, categorize, ecodes, list_devices

class GwiotCardReader:
    KEY_MAP = {
        ecodes.KEY_0: "0",
        ecodes.KEY_1: "1",
        ecodes.KEY_2: "2",
        ecodes.KEY_3: "3",
        ecodes.KEY_4: "4",
        ecodes.KEY_5: "5",
        ecodes.KEY_6: "6",
        ecodes.KEY_7: "7",
        ecodes.KEY_8: "8",
        ecodes.KEY_9: "9",
        ecodes.KEY_KP0: "0",
        ecodes.KEY_KP1: "1",
        ecodes.KEY_KP2: "2",
        ecodes.KEY_KP3: "3",
        ecodes.KEY_KP4: "4",
        ecodes.KEY_KP5: "5",
        ecodes.KEY_KP6: "6",
        ecodes.KEY_KP7: "7",
        ecodes.KEY_KP8: "8",
        ecodes.KEY_KP9: "9",
    }

    def __init__(
        self,
        device_path: Optional[str] = None,
        device_name_contains: str = "GWIOT",
    ):
        self.device = self._open_device(
            device_path=device_path,
            device_name_contains=device_name_contains,
        )

        print(
            f"Card reader connected: {self.device.name} "
            f"({self.device.path})"
        )

    @staticmethod
    def _open_device(
        device_path: Optional[str],
        device_name_contains: str,
    ) -> InputDevice:
        if device_path:
            path = Path(device_path)

            if not path.exists():
                raise FileNotFoundError(
                    f"Card reader device does not exist: {device_path}"
                )

            return InputDevice(str(path))

        search_text = device_name_contains.upper()

        for path in list_devices():
            device = InputDevice(path)

            if search_text in device.name.upper():
                return device

            device.close()

        raise RuntimeError(
            f'Could not find an input device containing '
            f'"{device_name_contains}"'
        )

    def wait_for_card_number(self) -> str:
        """
        Wait until the reader sends a card number followed by Enter.

        Returns:
            The card number as a string, for example "2325780402".
        """
        card_buffer = ""

        for event in self.device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue

            key_event = categorize(event)

            # Accept only key-down events.
            # Ignore key release and key repeat events.
            if key_event.keystate != key_event.key_down:
                continue

            key_code = key_event.scancode

            if key_code in self.KEY_MAP:
                card_buffer += self.KEY_MAP[key_code]
                continue

            if key_code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                if card_buffer:
                    card_number = card_buffer
                    card_buffer = ""
                    return card_number

            elif key_code == ecodes.KEY_BACKSPACE:
                card_buffer = card_buffer[:-1]

            elif key_code == ecodes.KEY_ESC:
                card_buffer = ""

    def close(self) -> None:
        self.device.close()

# ====== Module-level singleton API (matches other card backends) ======
#
# GwiotCardReader.wait_for_card_number() is a blocking call with no timeout
# support, so we run it in a background daemon thread and funnel completed
# card numbers through a Queue, letting get_card_id(timeout=...) behave like
# the other backends (wiegand_card_reader.py, card_read_write_simulator.py).

_instance: Optional[GwiotCardReader] = None
_card_queue: "queue.Queue[str]" = queue.Queue()
_reader_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

def _reader_loop():
    while not _stop_event.is_set():
        try:
            card_number = _instance.wait_for_card_number()
            if card_number:
                _card_queue.put(card_number)
        except Exception:
            if _stop_event.is_set():
                break
            # Device hiccup (e.g. transient read error) -- avoid a tight
            # error loop, keep trying.
            _stop_event.wait(1.0)

def initialize_card_reader(
    device_path: Optional[str] = None,
    device_name_contains: str = "GWIOT",
):
    """Open the GWIOT HID device and start the background read thread."""
    global _instance, _reader_thread
    if _instance is not None:
        return  # already started
    _instance = GwiotCardReader(
        device_path=device_path,
        device_name_contains=device_name_contains,
    )
    _stop_event.clear()
    _reader_thread = threading.Thread(target=_reader_loop, daemon=True)
    _reader_thread.start()

def get_card_id(timeout: Optional[float] = None) -> Optional[str]:
    """Return the next card number (string, e.g. "2325780402"), or None on timeout."""
    if _instance is None:
        raise RuntimeError("Card reader not initialized. Call initialize_card_reader() first.")
    try:
        return _card_queue.get(timeout=timeout)
    except queue.Empty:
        return None

def disconnect_card_reader():
    """Stop the background thread and close the HID device."""
    global _instance, _reader_thread
    _stop_event.set()
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
    if _reader_thread is not None:
        _reader_thread.join(timeout=2.0)
        _reader_thread = None
    _instance = None

def main() -> None:
    reader = GwiotCardReader()

    try:
        while True:
            print("Waiting for card...")

            card_number = reader.wait_for_card_number()

            print(f"Card number: {card_number}")

    except KeyboardInterrupt:
        print("\nStopping card reader.")

    finally:
        reader.close()

if __name__ == "__main__":
    main()