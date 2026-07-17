# HOWTO – RSID Face Guard: Setup, Virtual Env, and Fixing `rsid_py` Import Error

This guide explains how to work with the Python virtual environment (`.venv`) in this
project, and documents the exact steps used to fix the error:

```
[CRITICAL] Failed importing rsid_py. Please ensure rsid_py module is available.
```

---

## 1. What is the virtual environment (`.venv`)?

A virtual environment is an isolated Python installation used to keep this project's
dependencies separate from the system Python. The project's venv lives at:

```
/home/mahat/RSID_Face_Guard/.venv
```

It uses Python 3.11 and has its own `site-packages` directory:

```
.venv/lib/python3.11/site-packages/
```

## 2. Activating / deactivating the virtual environment

From the project root:

```bash
cd /home/mahat/RSID_Face_Guard

# Activate
source .venv/bin/activate

# Your shell prompt will now show (.venv) at the beginning.
# Any `python` / `pip` commands now refer to the venv's binaries.

# Deactivate when done
deactivate
```

You can also call the venv's Python directly without activating, using its full path:

```bash
/home/mahat/RSID_Face_Guard/.venv/bin/python your_script.py
```

## 3. Creating the virtual environment (if it doesn't exist)

```bash
cd /home/mahat/RSID_Face_Guard
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Installing project dependencies

With the venv activated:

```bash
pip install -r requirements.txt
```

## 5. Running the project

### From a terminal

```bash
source .venv/bin/activate
python host_mode_gui_tk.py
```

### From VS Code

1. Open the Command Palette → `Python: Select Interpreter`.
2. Choose the interpreter at `.venv/bin/python`.
3. Run/Debug `host_mode_gui_tk.py` normally (F5 or the Run button).

---

## 6. Fixing "Failed importing rsid_py" error

### Root cause

The `rsid_py` native extension module (a compiled `.so` file for the Raspberry Pi's
architecture) was present in the repo under `rpi_py_build_lib/`, but:

1. It was **not installed** into the venv's `site-packages`, so `import rsid_py` failed.
2. Its native dependencies, `librsid.so` and `librsid_c.so` (also under
   `rpi_py_build_lib/`), were not registered with the system's dynamic linker, so even
   after installing the module, importing it would fail with a missing shared-library
   error.

### Fix steps performed

1. **Copy the compiled Python extension into the venv's site-packages:**

   ```bash
   cp rpi_py_build_lib/rsid_py.cpython-311-aarch64-linux-gnu.so \
      .venv/lib/python3.11/site-packages/
   ```

2. **Install the native shared libraries system-wide and refresh the linker cache:**

   ```bash
   sudo cp rpi_py_build_lib/librsid.so rpi_py_build_lib/librsid_c.so /usr/lib/
   sudo ldconfig
   ```

   Verify they are registered:

   ```bash
   ldconfig -p | grep rsid
   # librsid_c.so (libc6,AArch64) => /lib/librsid_c.so
   # librsid.so (libc6,AArch64) => /lib/librsid.so
   ```

3. **Verify the import works:**

   ```bash
   .venv/bin/python -c "import rsid_py; print(rsid_py.__file__)"
   ```

   Expected output:

   ```
   /home/mahat/RSID_Face_Guard/.venv/lib/python3.11/site-packages/rsid_py.cpython-311-aarch64-linux-gnu.so
   ```

4. **Run the application to confirm the fix:**

   ```bash
   .venv/bin/python host_mode_gui_tk.py
   ```

   You should see log lines such as:

   ```
   [INFO] rsid_py version: 1.3.1
   [DiscoverDevices] Detected device type F45x
   [INFO] Device configured successfully
   [INFO] FaceAuthenticator connected
   ```

   instead of the `Failed importing rsid_py` critical error.

### Notes for future re-builds

- If you rebuild `rsid_py` (e.g. after updating the RealSenseID SDK or C++ wrapper),
  the new `.so` files will again be produced under `rpi_py_build_lib/`. Repeat steps
  1–2 above to reinstall them into the venv and system linker path.
- Alternative to copying `librsid.so`/`librsid_c.so` into `/usr/lib`: you can instead
  keep them in `rpi_py_build_lib/` and set `LD_LIBRARY_PATH` before running the script:

  ```bash
  LD_LIBRARY_PATH=/home/mahat/RSID_Face_Guard/rpi_py_build_lib:$LD_LIBRARY_PATH \
    .venv/bin/python host_mode_gui_tk.py
  ```

  This is useful if you don't have `sudo` access or don't want to modify system
  library paths. To make this permanent for VS Code debugging, add an `env` entry
  to `.vscode/launch.json`:

  ```json
  {
    "version": "0.2.0",
    "configurations": [
      {
        "name": "Python: host_mode_gui_tk.py",
        "type": "debugpy",
        "request": "launch",
        "program": "${workspaceFolder}/host_mode_gui_tk.py",
        "env": {
          "LD_LIBRARY_PATH": "${workspaceFolder}/rpi_py_build_lib"
        }
      }
    ]
  }
  ```

---

## 7. QtWebEngine (web UI) setup

The project can render the designer-provided web UI in `demo_ui/` inside a
`QWebEngineView` (embedded Chromium via PySide6). Getting QtWebEngine to run on
a fresh Raspberry Pi 5 (Raspberry Pi OS **bookworm**, aarch64) requires a few
one-time fixes. The smoke test `test_webengine_ui.py` validates all of this.

### Symptom-by-symptom fixes

QtWebEngine ships inside `PySide6` (`PySide6-Addons`), but on bookworm it fails
to import because Qt was built against older library sonames than bookworm
ships. Fix by symlinking the newer libs to the names QtWebEngine expects:

1. **`ImportError: libwebp.so.6: cannot open shared object file`**
   (bookworm ships `libwebp.so.7`):

   ```bash
   sudo ln -sf /usr/lib/aarch64-linux-gnu/libwebp.so.7 \
               /usr/lib/aarch64-linux-gnu/libwebp.so.6
   ```

2. **`ImportError: libtiff.so.5: cannot open shared object file`**
   (bookworm ships `libtiff.so.6`):

   ```bash
   sudo ln -sf /usr/lib/aarch64-linux-gnu/libtiff.so.6 \
               /usr/lib/aarch64-linux-gnu/libtiff.so.5
   ```

   > These two symlinks are a pragmatic workaround (the ABIs are compatible for
   > QtWebEngine's usage). If the linked-to soname version differs on your
   > machine, adjust the target — check with:
   > `ls /usr/lib/aarch64-linux-gnu/libwebp.so.* /usr/lib/aarch64-linux-gnu/libtiff.so.*`

   Verify the import now works:

   ```bash
   .venv/bin/python -c "from PySide6.QtWebEngineWidgets import QWebEngineView; print('OK')"
   ```

### Runtime requirements (already baked into `test_webengine_ui.py` / the app)

These environment variables MUST be set **before** QtWebEngine / QApplication
initializes. They are set at the top of `test_webengine_ui.py`; the real web
front-end sets them the same way.

- **Run as root → disable Chromium sandbox.** Chromium's zygote refuses to run
  as root with the sandbox on (this kiosk runs as root):
  `ERROR:zygote_host_impl_linux.cc … Running as root without --no-sandbox is not supported`

  ```python
  os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-gpu-compositing --in-process-gpu"
  ```

- **Force software OpenGL.** The Pi's GL stack under X cannot back Chromium's
  GPU process; without this the page **silently never finishes loading**
  (`loadFinished` never fires — the window stays blank):

  ```python
  os.environ["QT_OPENGL"] = "software"
  os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
  ```

  Harmless console noise you can ignore under software GL:
  `ERROR:gbm_wrapper.cc … Failed to export buffer to dma_buf`.

### Running the smoke test

```bash
DISPLAY=:0 .venv/bin/python test_webengine_ui.py
```

Expected: a 720x720 window shows the `demo_ui` web interface cycling through
screensaver → camera → success → failed. A log is written to
`/tmp/webengine_ui_test.txt`; a successful run contains:

```
page loaded ok=True
typeof window.deviceUI = object
after success() body.dataset.state = success
```

- `page loaded ok=True` → QtWebEngine rendered `demo_ui/index.html`.
- `typeof window.deviceUI = object` → the UI's JS integration API is present.
- `after success() … = success` → Python drove the UI via
  `runJavaScript("window.deviceUI.success(...)")`.

### Known non-issue: camera

The web UI's browser `getUserMedia()` **cannot** access the RealSense UVC
camera (it is owned by `rsid_py`, and the firmware needs exclusive access
during a scan). So `js: Camera could not start: DOMException` in the console is
**expected** during the smoke test. In the real integration, camera frames are
pushed from Python (the existing `rsid_py` `PreviewController`) into the page
instead of using `getUserMedia()`.

### Deployment note

The `libwebp` / `libtiff` symlinks above are manual. For a reproducible image,
fold them into a provisioning/install script (or ship the matching libraries)
so a freshly-flashed Pi isn't missing them.
