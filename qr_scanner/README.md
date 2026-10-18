# Unsigned installation QR PoC

Run from the project root using Python 3.11+ on Raspberry Pi or Python 3.12
on Windows. This standalone program reads JSON with `installationId`,
`deviceType`, and `serverLink`. It requires no signature, key, token, nonce,
or expiry. It prints decoded data only; it makes no network requests and does
not perform binding itself. The production scanner now shares its unsigned
payload parser.

```powershell
python -m pip install -r qr_scanner/requirements-poc.txt
python qr_scanner/poc.py
```

Show the phone's QR to your webcam. The preview remains open until Q/Esc or
the default 60-second timeout. Each distinct payload is printed once as JSON.
Use `--camera 1` to select another camera, `--timeout 120` for a longer scan,
or `--once` to exit on the first valid QR. Status/errors go to stderr and
decoded JSON goes to stdout.

Read a saved screenshot or the included example QR:

```powershell
python qr_scanner/poc.py --image qr_scanner/example_qr.png
python qr_scanner/poc.py --image C:/path/to/phone-screenshot.png
```

Expected result:

```json
{
  "installationId": "6ad4bbae041ea07eac2a9847",
  "deviceType": "Genie Door Camera Lite",
  "serverLink": "https://api-dev.itsu.tech"
}
```

The example QR encodes `example_payload.json`; it is a generated fixture,
not a copy decoded from the supplied phone screenshot.

Use the RealSense ID camera through the same SDK preview API as the kiosk:

```powershell
# Windows: Python 3.12, with rsid_py and rsid.dll available in the environment.
$env:PYTHONPATH = "$PWD/win_py_build_lib"
$env:PATH = "$PWD/win_py_build_lib;$env:PATH"
python qr_scanner/poc.py --realsense --once --port COM9
```

```bash
# Pi: use the configured Python 3.11 virtualenv and native SDK libraries.
PYTHONPATH="$PWD/rpi_py_build_lib" LD_LIBRARY_PATH="$PWD/rpi_py_build_lib:${LD_LIBRARY_PATH}" \
  .venv/bin/python qr_scanner/poc.py --realsense --once
```

Stop the kiosk before using the RealSense camera so it is not already in use.
`--port` is optional when SDK discovery works; `--camera` defaults to -1
(autodetect) for RealSense. `--no-preview` supports scanning without a display.

OpenCV decodes QR images; if pyzbar/zbar is already installed, it is tried
first, matching the production decoder. Invalid JSON, missing/empty fields,
and a `serverLink` without an HTTP(S) host are rejected. Extra JSON fields
are preserved. These checks validate the format only.

Exit codes: 0 = valid QR read, 1 = none found, 2 = setup/input error,
130 = interrupted. Run `python qr_scanner/poc.py --help` for all options.

Run the file directly as shown above, or use `python -m qr_scanner.poc`.
The production camera decoder loads only when requested.
