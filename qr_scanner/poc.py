"""Standalone unsigned installation-QR reader; run this file directly.

Does not import the production QRScanner or perform registration/network calls.
"""

import argparse
import json
import queue
import sys
import time
from pathlib import Path


if __package__:
    from .payload import parse_payload
else:
    from payload import parse_payload


class Decoder:
    def __init__(self, cv2):
        self.cv2 = cv2
        self.detector = cv2.QRCodeDetector()
        # Reuse zbar when installed (also used by the production scanner).
        # OpenCV keeps the PoC usable on Windows without a zbar installation.
        try:
            from pyzbar.pyzbar import ZBarSymbol, decode
        except (ImportError, OSError):
            self.zbar = None
        else:
            self.zbar = lambda gray: decode(gray, symbols=[ZBarSymbol.QRCODE])

    def decode(self, frame):
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        if self.zbar is not None:
            symbols = self.zbar(gray)
            if symbols:
                return [symbol.data.decode("utf-8", errors="replace") for symbol in symbols]
        found, texts, _, _ = self.detector.detectAndDecodeMulti(gray)
        if found and any(texts):
            return [text for text in texts if text]
        text, _, _ = self.detector.detectAndDecode(gray)
        return [text] if text else []


class Webcam:
    def __init__(self, cv2, index):
        self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError(f"Cannot open camera {index}; try --camera 1 or --realsense")
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    def read(self):
        ok, frame = self.capture.read()
        if not ok:
            raise RuntimeError("Camera stopped delivering frames")
        return frame

    def close(self):
        self.capture.release()


class RealSenseCamera:
    def __init__(self, cv2, index, port):
        import numpy as np
        import rsid_py

        self.cv2 = cv2
        self.frames = queue.Queue(maxsize=2)
        if port is None:
            devices = rsid_py.discover_devices()
            if not devices:
                raise RuntimeError("No RealSense ID device found; specify --port")
            port = devices[0]
        cfg = rsid_py.PreviewConfig()
        cfg.device_type = rsid_py.discover_device_type(port)
        cfg.camera_number = index
        cfg.preview_mode = rsid_py.PreviewMode.MJPEG_720P

        def on_image(image):
            frame = np.asarray(memoryview(image.get_buffer()), dtype=np.uint8)
            frame = frame.reshape((image.height, image.width, -1)).copy()
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                pass

        self.preview = rsid_py.Preview(cfg)
        try:
            self.preview.start(preview_callback=on_image, snapshot_callback=None)
        except Exception:
            self.preview.stop()
            raise

    def read(self):
        try:
            rgb = self.frames.get(timeout=0.2)
        except queue.Empty:
            return None
        return self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2BGR)

    def close(self):
        self.preview.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--image", type=Path, help="Read a saved QR image or screenshot")
    source.add_argument("--realsense", action="store_true", help="Use the rsid_py preview")
    parser.add_argument("--camera", type=int, help="Webcam index (default 0), SDK index (default -1)")
    parser.add_argument("--port", help="RealSense serial port, e.g. COM9 or /dev/ttyACM0")
    parser.add_argument("--once", action="store_true", help="Exit after the first valid payload")
    parser.add_argument("--timeout", type=float, default=60, help="Scan duration in seconds (default 60)")
    parser.add_argument("--no-preview", action="store_true", help="Scan without opening a window")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.port and not args.realsense:
        parser.error("--port requires --realsense")

    camera = None
    cv2 = None
    try:
        import cv2

        decoder = Decoder(cv2)
        if args.image:
            frame = cv2.imread(str(args.image))
            if frame is None:
                raise RuntimeError(f"Cannot read image: {args.image}")
        elif args.realsense:
            camera = RealSenseCamera(cv2, args.camera if args.camera is not None else -1, args.port)
        else:
            camera = Webcam(cv2, args.camera if args.camera is not None else 0)

        print("Scanning unsigned installation QR. Press Q or Esc to stop.", file=sys.stderr)
        seen = set()
        valid = False
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if camera is not None:
                frame = camera.read()
                if frame is None:
                    continue
            for text in decoder.decode(frame):
                if text in seen:
                    continue
                seen.add(text)
                try:
                    payload = parse_payload(text)
                except ValueError as exc:
                    print(f"QR rejected: {exc}", file=sys.stderr)
                    continue
                print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
                valid = True
                if args.once:
                    return 0
            if args.image:
                break
            if not args.no_preview:
                cv2.imshow("Installation QR PoC - Q to quit", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
        if not valid:
            print("No valid installation QR found.", file=sys.stderr)
        return 0 if valid else 1
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"Scanner error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if camera is not None:
            camera.close()
        if cv2 is not None and not args.no_preview and not args.image:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    sys.exit(main())
