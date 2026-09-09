# Setting Up RSID_Face_Guard on a Fresh Raspberry Pi 5

This document records the **exact, verified** steps used to get `main_web.py`
running from a completely clean Raspberry Pi OS install. Follow it top to
bottom on a new Pi 5 to reproduce a working environment.

> `main_web.py` is the only entry point. The former Qt-widgets harness
> (`main_qt.py`, `gui_qt/`) was removed on 2026-08-31; steps that used to be
> optional "web UI extras" are now part of the main path.

> Verified on: Raspberry Pi OS based on **Debian 13 "trixie"** (aarch64),
> which ships **Python 3.13 only** (no `python3.11` package available in
> apt, and the `deadsnakes` PPA does not support Debian — only Ubuntu).
> Because the prebuilt `rsid_py` native module is compiled for **CPython
> 3.11** specifically (`rsid_py.cpython-311-aarch64-linux-gnu.so`), Python
> 3.11 must be built from source on trixie. If your Pi OS image is based on
> **Bookworm** (which does ship Python 3.11), you can skip straight to
> [step 3](#3-create-the-project-virtual-environment-python-311).

---

## 0. Clone the repo

```bash
cd /home/geka
git clone https://github.com/gekakud/RSID_Face_Guard.git
cd RSID_Face_Guard
```

## 1. Check what Python version the OS ships

```bash
python3 --version
```

- If this prints `Python 3.11.x` → **skip to step 3**.
- If it prints `Python 3.12.x` / `3.13.x` (e.g. trixie) → continue to step 2
  to build Python 3.11 from source.

## 2. Build Python 3.11 from source (only needed on Debian trixie / Python ≥3.12)

### 2.1 Install build dependencies

```bash
sudo apt update
sudo apt install -y build-essential pkg-config libbz2-dev libffi-dev \
  libgdbm-dev libgdbm-compat-dev liblzma-dev libncurses-dev \
  libreadline-dev libsqlite3-dev libssl-dev lzma tk-dev uuid-dev \
  zlib1g-dev libzstd-dev wget
```

> Note: on trixie the package is `liblzma-dev` (not `lzma-dev`), and
> `libncurses-dev`/`libreadline-dev` (not the `5`/`6` suffixed names used on
> older Debian/Ubuntu releases).

### 2.2 Download, configure, and build Python 3.11.9

```bash
cd /tmp
wget -q https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar xf Python-3.11.9.tgz
cd Python-3.11.9
./configure --enable-shared --with-ensurepip=install \
  --enable-loadable-sqlite-extensions --prefix=/usr/local
make -j"$(nproc)"
```

This takes several minutes on a Pi 5. A warning about the optional `nis`
module not being found is expected and harmless.

### 2.3 Install it alongside the system Python (does NOT touch `python3`/`python3.13`)

```bash
sudo make altinstall
sudo ldconfig
```

`make altinstall` installs to `/usr/local/bin/python3.11` without
overwriting or symlinking the system `python3`, so `python3.13` remains the
OS default.

### 2.4 Verify

```bash
/usr/local/bin/python3.11 --version
# Python 3.11.9
```

## 3. Create the project virtual environment (Python 3.11)

```bash
cd /home/geka/RSID_Face_Guard
python3.11 -m venv .venv        # use /usr/local/bin/python3.11 if not on PATH
.venv/bin/python --version      # must print Python 3.11.x
```

## 4. Install the prebuilt `rsid_py` native module

The prebuilt module and its native shared libraries already live in the
repo under `rpi_py_build_lib/`. **Do not rebuild it** — just install it.

```bash
cp rpi_py_build_lib/rsid_py.cpython-311-aarch64-linux-gnu.so \
   .venv/lib/python3.11/site-packages/

sudo cp rpi_py_build_lib/librsid.so rpi_py_build_lib/librsid_c.so /usr/lib/
sudo ldconfig
ldconfig -p | grep rsid
# librsid_c.so (libc6,AArch64) => /lib/librsid_c.so
# librsid.so (libc6,AArch64) => /lib/librsid.so
```

Verify the import and version:

```bash
.venv/bin/python -c "import rsid_py; print(rsid_py.__version__)"
# 1.3.1
```

> Alternative to copying the `.so`s system-wide: keep them in
> `rpi_py_build_lib/` and set
> `LD_LIBRARY_PATH=/home/geka/RSID_Face_Guard/rpi_py_build_lib` before
> running the app instead of steps above (see `howto.md` for details).

## 5. Install the minimal Python dependencies

`requirements.txt` is split into a "core" section (what `main_web.py` actually
needs) and an "optional" section (LED control, real card-reader GPIO helpers —
not on the `main_web.py` import path with the current `config.py`). Install
just the core set:

```bash
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy requests PySide6 Pillow lgpio
```

(equivalently, the first block of `requirements.txt` above the "Optional"
section).

### QR scanning (init mode)

The technician "init mode" flow scans a provisioning QR with the camera. It
uses `pyzbar` (zbar) — chosen over OpenCV because it reads the large, dense
provisioning symbols far more reliably off a live frame — plus `cryptography`
to verify the QR's Ed25519 signature. `pyzbar` needs the system shared library
`libzbar0`:

```bash
sudo apt install -y libzbar0
.venv/bin/pip install pyzbar cryptography evdev
```

- `numpy`, `PySide6` — hard imports in `main_web.py`. `PySide6` must be the
  full metapackage, not `PySide6-Essentials`: the kiosk window is a
  `QWebEngineView`, and QtWebEngine ships in `PySide6-Addons`.
- `Pillow` — `gui_web/frame_server.py` JPEG-encodes camera frames for the
  MJPEG stream the page consumes.
- `requests` — imported transitively via `session`/`gui_web` → `face_auth` →
  `db.__init__` → `db/remote_provider.py` (always imported, even in local
  DB mode).
- `lgpio` — `hardware/relay_api.py`, used because `config.RUN_WITH_RELAY =
  True`.

Not installed (not needed for `main_web.py` as currently configured):
`pyaudio`, `rpi_ws281x`, `adafruit-blinka`, `neopixel`, `tk`, `gpiozero` —
these are only used by the standalone scripts in `other/` / LED code paths.

In any mode that uses the card reader (`card_only` or `card_and_face` — see
`config.DEVICE_MODE`, or the mode provisioned by the server), the backend used is
controlled by `config.CARD_READER_BACKEND`:
- `"gwiot_hid"` (default) — real GWIOT USB HID card reader via `evdev`
  (`pip install evdev`, already in the core `requirements.txt`).
- `"wiegand_gpio"` — older real Wiegand GPIO reader (`lgpio`, already
  installed above).
- `"simulated"` — fake reader for dev off the Pi.

## 6. Hardware / OS permissions

```bash
sudo usermod -aG dialout,gpio,video,plugdev,input geka
```

The `input` group is required for `config.CARD_READER_BACKEND = "gwiot_hid"`
(the default) -- `card_backends_impl/gwiot_hid_card_reader.py` reads the
GWIOT USB HID keyboard-emulation card reader directly via `/dev/input/eventX`
using `evdev`, which requires read access to that device node.

Log out/in (or reboot) for group membership changes to take effect for
serial (`/dev/ttyACM0`), GPIO, and camera (`/dev/video0`) access.

Verify the RealSense device and camera are visible:

```bash
ls -la /dev/ttyACM0
ls /dev/video*
```

### 6.1 udev rule for the RealSense camera (required for non-root runs)

Group membership alone is **not** enough for the camera preview.
`rsid_py`/libuvc opens the **raw USB node** (`/dev/bus/usb/BBB/DDD`), not
`/dev/video*`. The video nodes get an ACL for the seat-local user
automatically, but the raw node defaults to `root:root 0644`, so any non-root
run (notably `face-guard.service` with `User=geka`) fails with:

```
[Preview] Streaming ERROR : uvc_open(...) failed with: Access denied
```

Running under `sudo` masks the problem, which is why it only shows up once the
app runs as a service. Install the rule shipped in `deploy/`:

```bash
sudo cp deploy/99-realsense-id.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2aad
```

Verify the raw node is now group-owned by `plugdev` (bus/device numbers vary —
find them with `lsusb | grep 2aad`):

```bash
ls -l /dev/bus/usb/003/002
# crw-rw---- 1 root plugdev 189, 257 ... 	<- root:plugdev 0660, not root:root 0644
```

## 7. Run the app

> Complete [step 9](#9-qtwebengine-system-dependencies-required) first — the
> QtWebEngine kiosk needs two library symlinks on Bookworm/trixie or the page
> silently fails to load.

```bash
cd /home/geka/RSID_Face_Guard
DISPLAY=:0 .venv/bin/python main_web.py
```

(or `./run_main_web.sh`, which sets `LD_LIBRARY_PATH` and `DISPLAY` for you)

Expected log output on success:

```
[INFO] Auto-detected device on port: /dev/ttyACM0
[info] [DiscoverDevices] Detected device type F45x
[INFO] Device type: DeviceType.F45x
[INFO] Device configured successfully
[INFO] Relay initialized
[INFO] FaceAuthenticator connected
[INFO] DB_MODE=local -- using local JSON file only, no remote sync
[info] [Preview] Preview started!
```

The kiosk window opens on `demo_ui/`, enters init mode (scanning for a
provisioning QR for `INIT_MODE_DURATION_SEC`), then falls through to the idle
screensaver with the camera off. A registered card tap starts a session and
streams the preview. `AuthenticateStatus.NoFaceDetected` warnings are
expected/normal when no one is in front of the camera.

## 8. Enable the systemd service (auto-start on boot)

`face-guard.service` in the repo root is already set up for this user/path.
Complete [step 6.1](#61-udev-rule-for-the-realsense-camera-required-for-non-root-runs)
first, or the preview will fail with `uvc_open(...) Access denied`.

The unit runs `run_service.sh` rather than `main_web.py` directly. Two reasons:

- **`run_main_web.sh` must not be used here** — it ends with a blocking
  `read -p "Press Enter..."`, which would hang systemd forever.
- **The display race.** lightdm autologs into labwc, which spawns Xwayland
  *on demand*, so `graphical.target` is reached before `:0` exists.
  `run_service.sh` waits up to 60 s for the X display, then exits non-zero so
  `Restart=always` retries cleanly instead of Qt aborting with
  "cannot connect to X server". It `exec`s Python so `SIGTERM` reaches
  `main_web.py`'s orderly-shutdown handler directly.

Key settings and why they matter:

| Setting | Why |
|---|---|
| `SupplementaryGroups=... input render` | `input` is required for the GWIOT HID card reader (`/dev/input/event*`, `root:input 0660`) |
| `Environment=QT_QPA_PLATFORM=xcb` | on a Wayland session Qt would otherwise pick the wayland plugin and ignore `DISPLAY` |
| `StartLimitIntervalSec=0` | early-boot display-wait retries must not trip the start limit and permanently disable the unit |
| `TimeoutStopSec=15` | matches `main_web.py`'s ~6 s shutdown watchdog |
| `After=graphical.target` (no `network-online`) | the app is offline-tolerant (local JSON cache + buffered events); waiting on the network only delays boot |

Install and enable it:

```bash
sudo cp face-guard.service /etc/systemd/system/face-guard.service
sudo systemctl daemon-reload
sudo systemctl enable face-guard.service
sudo systemctl start face-guard.service
sudo systemctl status face-guard.service
journalctl -u face-guard.service -f   # follow logs
```

> **File ownership:** the service runs as `geka`, so nothing in the project may
> be left `root`-owned by earlier `sudo` runs. `face_guard.log` is the usual
> culprit — `setup_logging()` opens it at *import* time, so an unwritable log
> raises `PermissionError` before `main()` and crash-loops the unit. Fix with:
>
> ```bash
> sudo chown -R geka:geka /home/geka/RSID_Face_Guard
> sudo rm -f /home/geka/RSID_Face_Guard/.lgd-nfy0   # stale lgpio FIFO
> ```

Check the display-wait behaviour after a reboot, and disable if needed:

```bash
journalctl -b -u face-guard | grep -E 'Display .* is up|not available'
sudo systemctl disable --now face-guard   # revert
```

## 9. QtWebEngine system dependencies (required)

`main_web.py` hosts `demo_ui/` in a `QWebEngineView`, so the Chromium engine
bundled with PySide6 must be able to start. On Bookworm/trixie it needs two
library symlinks (it links against SO versions the OS no longer ships):

```bash
sudo ln -sf /usr/lib/aarch64-linux-gnu/libwebp.so.7 /usr/lib/aarch64-linux-gnu/libwebp.so.6
sudo ln -sf /usr/lib/aarch64-linux-gnu/libtiff.so.6 /usr/lib/aarch64-linux-gnu/libtiff.so.5
```

Chromium must also run with `--no-sandbox --disable-gpu` and
`QT_OPENGL=software` — `main_web.py` sets these itself before importing Qt
(see its module header), so no manual step is needed. `howto.md` §7 has the
full background.

---

## Summary: minimal command sequence (Debian trixie, Python 3.13 base OS)

```bash
# 1. Build Python 3.11 (trixie has no python3.11 package)
sudo apt update
sudo apt install -y build-essential pkg-config libbz2-dev libffi-dev \
  libgdbm-dev libgdbm-compat-dev liblzma-dev libncurses-dev \
  libreadline-dev libsqlite3-dev libssl-dev lzma tk-dev uuid-dev \
  zlib1g-dev libzstd-dev wget
cd /tmp && wget -q https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar xf Python-3.11.9.tgz && cd Python-3.11.9
./configure --enable-shared --with-ensurepip=install \
  --enable-loadable-sqlite-extensions --prefix=/usr/local
make -j"$(nproc)"
sudo make altinstall
sudo ldconfig

# 2. venv + rsid_py
cd /home/geka/RSID_Face_Guard
python3.11 -m venv .venv
cp rpi_py_build_lib/rsid_py.cpython-311-aarch64-linux-gnu.so .venv/lib/python3.11/site-packages/
sudo cp rpi_py_build_lib/librsid.so rpi_py_build_lib/librsid_c.so /usr/lib/
sudo ldconfig
.venv/bin/python -c "import rsid_py; print(rsid_py.__version__)"   # -> 1.3.1

# 3. Python deps (+ libzbar0 for QR scanning in init mode)
sudo apt install -y libzbar0
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy PySide6 Pillow requests lgpio evdev pyzbar cryptography

# 4. Permissions
sudo usermod -aG dialout,gpio,video,plugdev,input geka   # then re-login

# 4b. udev rule: raw USB access for the camera (step 6.1)
#     Without it, non-root runs fail with "uvc_open(...) Access denied".
sudo cp deploy/99-realsense-id.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2aad

# 5. QtWebEngine library symlinks (step 9)
sudo ln -sf /usr/lib/aarch64-linux-gnu/libwebp.so.7 /usr/lib/aarch64-linux-gnu/libwebp.so.6
sudo ln -sf /usr/lib/aarch64-linux-gnu/libtiff.so.6 /usr/lib/aarch64-linux-gnu/libtiff.so.5

# 6. Run
DISPLAY=:0 .venv/bin/python main_web.py

# 7. Auto-start on boot (step 8)
sudo cp face-guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now face-guard.service
```