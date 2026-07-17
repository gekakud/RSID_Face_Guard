# Setting Up RSID_Face_Guard on a Fresh Raspberry Pi 5

This document records the **exact, verified** steps used to get `main_qt.py`
running from a completely clean Raspberry Pi OS install. Follow it top to
bottom on a new Pi 5 to reproduce a working environment.

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

`requirements.txt` has been reorganized into a "core" section (what
`main_qt.py` actually needs) and an "optional" section (Tkinter GUI, LED
control, real card-reader GPIO helpers — not on the `main_qt.py` import
path with the current `config.py`). Install just the core set:

```bash
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy requests PySide6 lgpio
```

(equivalently, the first block of `requirements.txt` above the "Optional"
section).

- `numpy`, `PySide6` — hard imports in `main_qt.py`.
- `requests` — imported transitively via `gui_qt` → `face_auth` →
  `db.__init__` → `db/remote_provider.py` (always imported, even in local
  DB mode).
- `lgpio` — `hardware/relay_api.py`, used because `config.RUN_WITH_RELAY =
  True`.

Not installed (not needed for `main_qt.py` as currently configured):
`pyaudio`, `rpi_ws281x`, `adafruit-blinka`, `neopixel`, `Pillow`, `tk`,
`gpiozero` — these are only used by other entry points / LED code paths.

If you later enable `RUN_WITH_CARD_READER = True` and set
`SIMULATE_HW = False` in `config.py` to use the real Wiegand card reader
hardware (`card_api/`), you will additionally need `lgpio` (already
installed above) and should double check `card_api/card_reader_api.py`'s
imports work as a package (it currently has a non-relative import that may
need fixing).

## 6. Hardware / OS permissions

```bash
sudo usermod -aG dialout,gpio,video,plugdev geka
```

Log out/in (or reboot) for group membership changes to take effect for
serial (`/dev/ttyACM0`), GPIO, and camera (`/dev/video0`) access.

Verify the RealSense device and camera are visible:

```bash
ls -la /dev/ttyACM0
ls /dev/video*
```

## 7. Run the app

```bash
cd /home/geka/RSID_Face_Guard
DISPLAY=:0 .venv/bin/python main_qt.py
```

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

The Qt window opens, the camera preview streams, and the app cycles through
auto-authentication every `AUTO_AUTH_INTERVAL_SEC` (5s) as configured in
`config.py`. `AuthenticateStatus.NoFaceDetected` warnings are expected/
normal when no one is in front of the camera.

## 8. Enable the systemd service (auto-start on boot)

`face-guard.service` in the repo root has already been updated for this
user/path (was previously pointing at `mahat` / `host_mode_gui_tk.py`):

```ini
[Service]
User=geka
Group=geka
WorkingDirectory=/home/geka/RSID_Face_Guard
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/geka/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=/home/geka/RSID_Face_Guard/.venv/bin/python /home/geka/RSID_Face_Guard/main_qt.py
SupplementaryGroups=dialout gpio video plugdev
```

Install and enable it:

```bash
sudo cp face-guard.service /etc/systemd/system/face-guard.service
sudo systemctl daemon-reload
sudo systemctl enable face-guard.service
sudo systemctl start face-guard.service
sudo systemctl status face-guard.service
journalctl -u face-guard.service -f   # follow logs
```

## 9. (Optional) Web UI (`main_web.py`) — NOT needed for `main_qt.py`

Only required if you use the QtWebEngine-based `main_web.py` instead of
`main_qt.py`. On Bookworm/trixie you need library symlinks:

```bash
sudo ln -sf /usr/lib/aarch64-linux-gnu/libwebp.so.7 /usr/lib/aarch64-linux-gnu/libwebp.so.6
sudo ln -sf /usr/lib/aarch64-linux-gnu/libtiff.so.6 /usr/lib/aarch64-linux-gnu/libtiff.so.5
```

Plus running Chromium with `--no-sandbox --disable-gpu` and
`QT_OPENGL=software` (already set inside `test_webengine_ui.py` /
`main_web.py`). See `howto.md` §7 for full details. Skip this section
entirely if only using `main_qt.py`.

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

# 3. Python deps
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy PySide6 requests lgpio

# 4. Permissions
sudo usermod -aG dialout,gpio,video,plugdev geka   # then re-login

# 5. Run
DISPLAY=:0 .venv/bin/python main_qt.py