"""Decode unsigned installation JSON from kiosk camera frames."""

import numpy as np
from pyzbar.pyzbar import ZBarSymbol, decode as zbar_decode

from qr_scanner.payload import parse_payload
from observability import events
from observability.events import EventType
from observability.logging_setup import get_logger

log = get_logger("qr_scanner")


class QRScanner:
    def scan(self, frame):
        try:
            gray = np.dot(frame[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8) if frame.ndim == 3 else frame
            results = zbar_decode(gray, symbols=[ZBarSymbol.QRCODE])
        except Exception as exc:
            log.exception("QR detection failed")
            events.emit(EventType.HARDWARE_ERROR, where="qr_scanner", error=str(exc))
            return None
        for result in results:
            try:
                payload = parse_payload(result.data.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                log.warning("QR rejected: %s", exc)
                events.emit(EventType.QR_REJECTED)
                continue
            log.info("Installation QR accepted: installation_id=%s", payload["installationId"])
            events.emit(EventType.QR_ACCEPTED, installation_id=payload["installationId"])
            return payload
        return None
