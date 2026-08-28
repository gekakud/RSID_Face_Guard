# Observability

Cross-cutting logging and event telemetry for the access-control kiosk.

## Logging

One configuration point: `observability/logging_setup.py`. Entry points
(`main_qt.py`, `main_web.py`) call it once at startup:

```python
from observability.logging_setup import get_logger, install_native_log_bridge, setup_logging

setup_logging()
install_native_log_bridge()
log = get_logger("main")
```

Every other module gets its own child logger:

```python
from observability.logging_setup import get_logger

log = get_logger("relay")
```

### Logger hierarchy

All loggers live under the `face_guard` root, so each line identifies its
source module:

| Tag | Source |
|---|---|
| `main` | `main_qt.py`, `main_web.py` |
| `qr_scanner` | `qr_scanner/qr_scanner.py` |
| `auth` | `face_auth/auth_service.py` |
| `relay` | `hardware/relay_api.py` |
| `card` | `hardware/card_reader_api.py`, `card_backends_impl/*` |
| `preview` | `hardware/camera_preview.py` |
| `db` | `db/*` |
| `gui` | `gui_qt/*`, `gui_web/*` |
| `native` | librsid (C++) via the `rsid_py` log callback |

### Output format

Console and rotating file (`face_guard.log`) share one format:

```
2026-08-14 11:43:29.187 [INFO    ] [qr_scanner] QR scan result: ACCEPTED -- door_id='door_789'
```

### Native (librsid) logs

`librsid.so` logs from C++ with its own format, straight to stdout:

```
[2026-08-14 11:22:19.094] [debug] [LinuxSerial] Opening serial port ...
```

`install_native_log_bridge()` registers `rsid_py.set_log_callback(...)` so
those lines are also emitted through Python logging under the `native` tag,
in our format and into `face_guard.log`. It fails silently (returns `False`)
if `rsid_py` is unavailable, so it's safe to call unconditionally.

**Known caveat — duplicated native lines on the console.** The prebuilt
`rpi_py_build_lib/librsid.so` was compiled with `RSID_DEBUG_CONSOLE=ON`, so
it *also* writes its own raw line straight to stdout from C++. That write
can't be disabled at runtime, so each native message shows up twice on the
console: once raw, once via our bridge. The log **file** only ever receives
our formatted copy. Rebuilding librsid without `RSID_DEBUG_CONSOLE` removes
the raw duplicate (see `docs/linux_readme.md` for the cmake flow).

### Configuration (`config.py`)

```python
LOG_LEVEL = "INFO"          # global default for the whole tree
LOG_LEVELS = {              # per-module overrides, keyed by tag
    "native": "INFO",       # librsid is very chatty at DEBUG
}
LOG_FILE = "face_guard.log" # relative -> resolved against the project root
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5
```

To debug one subsystem without drowning in everything else, raise just that
tag (e.g. `LOG_LEVELS = {"native": "DEBUG", "preview": "DEBUG"}`) — no code
changes needed.

### Level conventions

| Level | Use for |
|---|---|
| `DEBUG` | Verbose internals; off in production. |
| `INFO` | Normal lifecycle/business events (device configured, access granted, relay opened). |
| `WARNING` | Expected/benign failures (expired QR token, unregistered card, retryable network error). |
| `ERROR` | Real failures needing attention. Security-relevant rejections use a `SECURITY:` prefix (invalid QR signature, unknown `key_id`, replayed nonce). |
| `CRITICAL` | Startup-blocking failures (missing `rsid_py`, unavailable card-reader backend). |

Use `log.exception(...)` inside `except` blocks to capture the traceback.

Do not use `print()` in runtime code paths — it bypasses the log file and is
invisible under systemd. (`print()` inside `if __name__ == "__main__":`
blocks of standalone CLI test tools is fine.)

## Event telemetry

Diagnostic logs are free-form text for humans; server-side analysis needs
structured **events**. These live in `observability/events.py` and surface on
the dashboard's per-device **Event log** (see `server/README.md`).

```python
from observability import events

events.emit("access_granted", user_id="u-8f2c1a", method="card")
```

- `emit(type, **fields)` — thread-safe, never raises, never blocks. Safe to
  call from the auth thread, card-reader thread, relay, or GUI. It stamps each
  event with a uuid4 `event_id` and a UTC `ts` and appends it to a bounded
  in-memory ring buffer.
- Event types in use: `device_boot`, `device_shutdown`, `access_granted`,
  `access_denied`, `auth_matched`, `qr_accepted`, `qr_rejected`, `relay_opened`,
  `hardware_error`, `db_sync_ok` / `db_sync_failed`, `init_mode_entered`,
  `storage_low` / `storage_ok`, `heartbeat_post_failed`.
- **Privacy (schema v2 / FR-DATA-06):** access events reference a person only by
  the opaque `user_id`; a real name or raw card id is **never** emitted. An
  unregistered tap resolves no user, so it carries neither (just
  `reason="card_unregistered"`).


### Delivery — piggybacked on the heartbeat, guaranteed

There is **no separate uploader, spool file, or endpoint**. Events ride the
status heartbeat the provisioning layer already sends every
`config.HEARTBEAT_INTERVAL_SEC`:

1. `HeartbeatWorker._run()` calls `events.snapshot()` and puts the result in
   `metadata["events"]` of the status POST.
2. Only on a 2xx response does it call `events.ack(n)`, removing exactly those
   `n` events. A failed beat acks nothing, so the events ride the next one —
   **guaranteed delivery** with no loss across an outage (bounded by the ring
   buffer's `maxlen`; only a very long outage with heavy traffic drops the
   oldest).
3. On shutdown, `BindingManager.shutdown()` emits `device_shutdown` and does one
   best-effort synchronous flush before the heartbeat thread stops, so the last
   events aren't stranded.

The server does `INSERT OR IGNORE` on the unique `event_id`, so a beat that was
delivered but whose response was lost (and therefore resent) never duplicates.

### Emit sites

| Event | Emitted from |
|---|---|
| `device_boot` | `main_qt.py` / `main_web.py` `main()` |
| `device_shutdown` | `provisioning/binding.py` `shutdown()` |
| `access_granted` / `access_denied` | `face_auth/auth_service.py` — both reference the matched user by `user_id` only (never name/card id). `access_denied` carries a `reason`: `face_mismatch`, `no_match`, `face_extraction_failed`, `no_faceprints_on_file`, `user_inactive` (record present but `active: false`), or `card_unregistered` (tapped card absent from the local DB; no user, so no `user_id`) |
| `auth_matched` | `face_auth/auth_service.py` — low-level breadcrumb of a 1:1/1:N match by `user_id`; the door-open grant follows as `access_granted` from the controller |
| `hardware_error` | `face_auth/auth_service.py` (`authenticator_connect`, `wiegand_tx_init`, `authenticate_face_only`, `authenticate_with_card`, `card_monitor`), `hardware/relay_api.py` (`relay_init`, `relay_open`), `hardware/camera_preview.py` (`preview_frame`, `preview_restart`, `preview_resume`), `card_backends_impl/gwiot_hid_card_reader.py` (`gwiot_reader`), `qr_scanner/qr_scanner.py` (`qr_scanner`), `observability/storage_monitor.py` (`storage_monitor`), `main_qt.py` / `main_web.py` `main()` (`boot_device_discovery`, `boot_device_config`, `boot_card_reader`, `boot_relay` — best-effort only: emitted before any heartbeat thread/BindingManager exists, so these never reach the server the boot cycle they fire in, but still land in the local log) — every occurrence carries a `where` field identifying the failure site plus an `error` string |
| `relay_opened` | `hardware/relay_api.py` `open_door()` |
| `qr_accepted` / `qr_rejected` | `qr_scanner/qr_scanner.py` `scan()` |
| `db_sync_ok` / `db_sync_failed` | `db/remote_provider.py` |
| `init_mode_entered` | `gui_qt` / `gui_web` `start_init_mode()` |
| `storage_low` / `storage_ok` | `observability/storage_monitor.py` `check_storage()` — fires once per threshold crossing (not every check), carries `path`, `free_mb`, and (for `storage_low`) `min_free_mb` |
| `heartbeat_post_failed` | `provisioning/heartbeat.py` `HeartbeatWorker._run()` — fires once after 3 consecutive failed heartbeat POSTs (throttled so a brief blip doesn't flood the buffer), carries `consecutive_failures` and `server_url` |

## Storage monitoring

`observability/storage_monitor.py` watches free disk space on the SD card
that holds `face_guard.log`, `user_database.json` and
`device_identity.json`.

```python
from observability import storage_monitor

storage_monitor.check_storage()        # {"path", "total_mb", "used_mb", "free_mb", "free_pct", "low"}
storage_monitor.get_storage_metadata() # same, but never raises -- safe for heartbeat metadata_fn
```

Both GUI windows (`gui_qt/main_window_qt.py`, `gui_web/web_window.py`) start a
`QTimer` on `config.STORAGE_CHECK_INTERVAL_SEC` (default 300s) that calls
`check_storage()` to detect threshold crossings between heartbeats, and embed
`get_storage_metadata()` under `metadata["storage"]` in every heartbeat via
`_collect_metadata()`.

### Configuration (`config.py`)

```python
STORAGE_MONITOR_PATH = None      # None -> project root (SD card)
STORAGE_MIN_FREE_MB = 200        # below this -> storage_low event + WARNING log
STORAGE_CHECK_INTERVAL_SEC = 300 # GUI timer interval, independent of heartbeat cadence
```

