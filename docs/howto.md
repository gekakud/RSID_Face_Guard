# HOWTO – RSID Face Guard: virtual env, running the app, `rsid_py` and web UI setup

How to work with the Python environment, run the app, fix the `rsid_py` import
error, and set up the QtWebEngine web UI on a Raspberry Pi 5. For a fresh Pi, see
`docs/SETUP_RPI5.md`.

---

## 1. The virtual environment (`.venv`)

An isolated Python 3.11 installation that keeps this project's dependencies away
from the system Python. It lives in the project root:

```
/home/geka/RSID_Face_Guard/.venv
```

Packages go to `.venv/lib/python3.11/site-packages/`.

## 2. Activate / deactivate

```bash
cd /home/geka/RSID_Face_Guard
source .venv/bin/activate     # the prompt now starts with (.venv)
deactivate                    # when done
```

You can also call the venv Python directly, without activating:

```bash
/home/geka/RSID_Face_Guard/.venv/bin/python main_web.py
```

## 3. Create the venv (if it does not exist)

```bash
cd /home/geka/RSID_Face_Guard
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Install dependencies

With the venv active: `pip install -r requirements.txt`.

## 5. Run the app

The app is `main_web.py` (PySide6 + QtWebEngine, UI in `demo_ui/`).

### Easiest: the launcher script

```bash
./run_main_web.sh
```

It sets `LD_LIBRARY_PATH` for `rsid_py`, and by default turns on **GPU rendering
on native Wayland** (see section 8). Environment values you set yourself win over
its defaults.

### From a terminal, by hand

```bash
source .venv/bin/activate
LD_LIBRARY_PATH=$PWD/rpi_py_build_lib python main_web.py
```

### As a service (auto-start on boot)

`face-guard.service` runs `run_service.sh`. Steps are in `docs/SETUP_RPI5.md`
section 8. Useful commands:

```bash
sudo systemctl restart face-guard
sudo systemctl stop face-guard
journalctl -u face-guard -b
```

### From VS Code

1. Command Palette → `Python: Select Interpreter` → `.venv/bin/python`.
2. Run or debug `main_web.py` (F5). Add `LD_LIBRARY_PATH` to the launch config,
   see the end of section 6.

### Kiosk mode and how to exit

`config.py` has two switches:

- `RUN_ON_REAL_SCREEN = True` places the window on the small display.
- `KIOSK_BORDERLESS = True` makes it fullscreen with no border. `False` gives a
  normal window, handy for debugging.

To close the app:

- **Keyboard** (needs a USB keyboard): `Ctrl+C`, `Ctrl+Q` or `Esc`.
- **SSH:** `sudo pkill -f main_web.py` (clean shutdown). If it hangs, a watchdog
  forces the exit after 6 seconds.
- **Service:** `sudo systemctl stop face-guard`.

Keep an SSH session open when you first test fullscreen on a touch-only screen.

### Round or square screens

The UI is drawn for 720x720. A fullscreen window uses the whole output, so the
output mode must match the panel. If the screensaver circle looks like an
ellipse, the Pi is driving a 4:3 mode (for example 1024x768) into a square
panel. Check the modes:

```bash
WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 wlr-randr
```

Try a square mode, for example `wlr-randr --output HDMI-A-2 --custom-mode 720x720@60`
(use your output name). I have not confirmed that a given panel accepts it. If
the screen goes blank, set the old mode back over SSH. To keep it after a reboot,
add `video=HDMI-A-2:720x720@60` to `/boot/firmware/cmdline.txt`, or run the
`wlr-randr` command from the labwc autostart file.

---

## 6. Fixing "Failed importing rsid_py"

```
[CRITICAL] Failed importing rsid_py. Please ensure rsid_py module is available.
```

### Root cause

The compiled `rsid_py` extension (a `.so` for the Pi's architecture) is in the
repo under `rpi_py_build_lib/`, but:

1. It is not installed into the venv's `site-packages`, so `import rsid_py`
   fails.
2. Its native libraries `librsid.so` and `librsid_c.so` (also in
   `rpi_py_build_lib/`) are not known to the dynamic linker, so the import fails
   with a missing shared library error even after step 1.

### Fix

1. Copy the extension into the venv:

   ```bash
   cp rpi_py_build_lib/rsid_py.cpython-311-aarch64-linux-gnu.so \
      .venv/lib/python3.11/site-packages/
   ```

2. Install the native libraries system-wide and refresh the linker cache:

   ```bash
   sudo cp rpi_py_build_lib/librsid.so rpi_py_build_lib/librsid_c.so /usr/lib/
   sudo ldconfig
   ldconfig -p | grep rsid       # both libraries should be listed
   ```

3. Check the import:

   ```bash
   .venv/bin/python -c "import rsid_py; print(rsid_py.__file__)"
   ```

4. Run the app:

   ```bash
   ./run_main_web.sh
   ```

   The log should show the device being found and configured, not the
   `Failed importing rsid_py` error.

### After a rebuild

If you rebuild `rsid_py` (new RealSenseID SDK or wrapper), the new files appear
in `rpi_py_build_lib/` again. Repeat steps 1–2.

### Alternative: no system install

Keep the libraries in `rpi_py_build_lib/` and set `LD_LIBRARY_PATH`. Both
`run_main_web.sh` and `run_service.sh` already do this. By hand:

```bash
LD_LIBRARY_PATH=/home/geka/RSID_Face_Guard/rpi_py_build_lib:$LD_LIBRARY_PATH \
  .venv/bin/python main_web.py
```

For VS Code debugging, add it to `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: main_web.py",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/main_web.py",
      "env": {
        "LD_LIBRARY_PATH": "${workspaceFolder}/rpi_py_build_lib"
      }
    }
  ]
}
```

---

## 7. QtWebEngine setup (one time)

The UI in `demo_ui/` runs in a `QWebEngineView` (Chromium inside PySide6). On a
fresh Raspberry Pi 5 (Raspberry Pi OS **bookworm**, aarch64) it needs a few
fixes. The smoke test `test_webengine_ui.py` checks all of this.

### Import errors

QtWebEngine in PySide6 was built against older library names than bookworm
ships. Link the newer libraries to the names it expects:

1. `ImportError: libwebp.so.6: cannot open shared object file`
   (bookworm ships `libwebp.so.7`):

   ```bash
   sudo ln -sf /usr/lib/aarch64-linux-gnu/libwebp.so.7 \
               /usr/lib/aarch64-linux-gnu/libwebp.so.6
   ```

2. `ImportError: libtiff.so.5: cannot open shared object file`
   (bookworm ships `libtiff.so.6`):

   ```bash
   sudo ln -sf /usr/lib/aarch64-linux-gnu/libtiff.so.6 \
               /usr/lib/aarch64-linux-gnu/libtiff.so.5
   ```

   These links are a workaround. If the versions on your machine differ, check:
   `ls /usr/lib/aarch64-linux-gnu/libwebp.so.* /usr/lib/aarch64-linux-gnu/libtiff.so.*`

Check the import:

```bash
.venv/bin/python -c "from PySide6.QtWebEngineWidgets import QWebEngineView; print('OK')"
```

For a reproducible image, put these two links in an install script. A freshly
flashed Pi will not have them.

---

## 8. Rendering: GPU (default) or software

`main_web.py` picks the Chromium flags from `RSID_GPU`. The launchers
(`run_main_web.sh`, `run_service.sh`) and `face-guard.service` set these
defaults:

```
RSID_GPU=1
QT_QPA_PLATFORM=wayland
WAYLAND_DISPLAY=wayland-0
XDG_RUNTIME_DIR=/run/user/1000
```

- **GPU mode** (`RSID_GPU=1`) runs Chromium with the Pi's GPU on native
  Wayland (labwc). On the idle screensaver it cut user CPU from about 62% to
  about 19%. I measured only the screensaver, not a long card session.
- **Software mode** is the old path: Chromium with `--disable-gpu` and Mesa
  software GL (`QT_OPENGL=software`, `LIBGL_ALWAYS_SOFTWARE=1`). To use it:

  ```bash
  RSID_GPU=0 QT_QPA_PLATFORM=xcb ./run_main_web.sh
  ```

  In the service, change the three `Environment=` lines in
  `face-guard.service`, then `sudo systemctl daemon-reload` and restart.

Both modes use `--no-sandbox`, because Chromium refuses to run its sandbox as
root.

Under native Wayland an app cannot place its own window. With one screen this
does not matter, because the window goes to that screen.

The camera preview is an MJPEG stream from `gui_web/frame_server.py`: centre
square crop at up to 704x704, sent when the camera produces a frame. The mirror
is done in CSS.

---

## 9. Smoke test

```bash
DISPLAY=:0 .venv/bin/python test_webengine_ui.py
```

It shows a 720x720 window cycling screensaver → camera → success → failed, and
writes a log to `/tmp/webengine_ui_test.txt`. A good run contains:

```
page loaded ok=True
typeof window.deviceUI = object
after success() body.dataset.state = success
```

The test sets its own Chromium flags and may not match the GPU defaults above.
I have not re-run it since the GPU change.

### Camera in the browser

The browser's `getUserMedia()` cannot open the RealSense camera, because
`rsid_py` owns it and the firmware needs exclusive access during a scan. So
`Camera could not start: DOMException` in the smoke test is expected. In the
real app the frames come from Python (the `PreviewController`) over the local
MJPEG stream.
