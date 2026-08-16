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

events.emit("access_granted", user="alice", method="card")
```

- `emit(type, **fields)` — thread-safe, never raises, never blocks. Safe to
  call from the auth thread, card-reader thread, relay, or GUI. It stamps each
  event with a uuid4 `event_id` and a UTC `ts` and appends it to a bounded
  in-memory ring buffer.
- Event types in use: `device_boot`, `device_shutdown`, `access_granted`,
  `access_denied`, `card_unknown`, `qr_accepted`, `qr_rejected`, `relay_opened`,
  `hardware_error`, `db_sync_ok` / `db_sync_failed`, `init_mode_entered`.

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
| `access_granted` / `access_denied` | `face_auth/auth_service.py` |
| `card_unknown` | `face_auth/auth_service.py` |
| `hardware_error` | `face_auth/auth_service.py` |
| `relay_opened` | `hardware/relay_api.py` `open_door()` |
| `qr_accepted` / `qr_rejected` | `qr_scanner/qr_scanner.py` `scan()` |
| `db_sync_ok` / `db_sync_failed` | `db/remote_provider.py` |
| `init_mode_entered` | `gui_qt` / `gui_web` `start_init_mode()` |
