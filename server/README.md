# Device dashboard server

FastAPI + SQLite. It creates installations and their QR codes, pairs terminals
that scan them, serves each terminal its user list, receives heartbeats and
events, and shows it all on a dashboard.

It is independent of the device application: no PySide6, no `rsid_py`, no GPIO.
It runs on any plain Linux host.

The contract between the terminal and the backend is in
[docs/DEVICE_SERVER_API.md](../docs/DEVICE_SERVER_API.md) (short version:
[docs/API_REFERENCE.md](../docs/API_REFERENCE.md)). This file covers this
server only.

## Run it

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --reload            # from the repo root
```

Then open <http://localhost:8000>. Interactive API docs are at `/docs`.

To let a real device on the LAN reach it, bind all interfaces and tell the
server its own address. That URL goes into every QR as `serverLink`, and it is
how the device knows where to call:

```bash
PUBLIC_BASE_URL=http://192.168.1.50:8000 uvicorn server.main:app --host 0.0.0.0 --port 8000
```

## Configuration

All optional. Every value has a working default.

| Env var | Default | Notes |
|---|---|---|
| `DB_PATH` | `server/data/faceguard.db` | On Render, point at a mounted disk. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Put into each QR as `serverLink`. |
| `HEARTBEAT_TIMEOUT_SEC` | `90` | Older than this means the device shows offline. |
| `HEARTBEAT_INTERVAL_SEC` | `30` | Rate handed to devices in the pair response. |
| `STATUS_HISTORY_LIMIT` | `200` | History rows kept per device. |
| `EVENTS_LIMIT` | `500` | Event rows kept per device. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | unset | Both set: HTTP Basic on the dashboard endpoints. Unset: open. |
| `SEED_DEFAULTS` | `true` | Seed the demo Customer/Site/Door on first start with an empty DB. Tests turn this off. |
| `USER_STORE_FILE` | `server_user_database.json` | Per-device assigned-users JSON store (relative to `server/`). |
| `DEFAULT_USER_DB_FILE` | `default_user_database.json` | Flat template every newly paired device is seeded with (relative to `server/`). |

## Installation pairing

The dashboard **Add device** page (`/new`) creates an installation and shows an
unsigned QR. The QR holds `installationId`, `deviceType` and `serverLink`, plus
`networkProfile` for Wi-Fi. There is no signature, token or expiry.

1. The operator fills the form. `POST /installation` creates a *pending*
   installation and returns the QR.
2. The operator shows the QR to the device camera while the device is in init
   mode. The device joins the Wi-Fi if the QR has one.
3. The device calls `POST /installation/pair`. The server binds the hardware to
   the installation and returns a `device_token`.
4. The device fetches its users and posts a heartbeat. It shows online for as
   long as the heartbeats keep arriving.

The names customer / site / door are what the device shows, for example
`acme-corp / main-entrance`. In the pair response they appear as `customer`,
`venue` and `gate`.

### Hardware key and re-pairing

The Pi board serial is the hardware lookup key. MACs are stored in upper case
and serials in lower case, keeping the full 16-digit Pi serial. A `dev-...`
serial supports Windows development.

- **Same installation, same serial and MAC (replay):** recovers the same device
  ID and rotates the token. The previous token stops working.
- **Removed or replaced installation, or different hardware:** `409`.
- **A new pending installation for known hardware:** the existing hardware row
  is rebound and its older installation is marked `replaced`.
- **Users:** seeded from the demo template on a new binding. A replay keeps
  uploaded users.

### Customer / site / door

Devices are organised as **customer → site → door**. The `/new` page picks them
from dropdowns (`+ add` creates one). The first start with an empty database
seeds a Demo Customer → Demo Site → Demo Door.

- `GET /customers` · `POST /customers {name}`
- `GET /sites?customer_id=` · `POST /sites {customer_id, name}`
- `GET /doors?site_id=` · `POST /doors {site_id, name}`

POST is idempotent by name within its parent. An existing name returns the
existing record.

---

## API

### Dashboard side (operator)

HTTP Basic when `ADMIN_USER` and `ADMIN_PASSWORD` are set.

| Endpoint | Purpose |
|---|---|
| `POST /installation` | Create a pending installation and its QR. |
| `GET /installation`, `GET /installation/{id}` | Pairing status. |
| `GET /devices` | All devices with derived `online`, `last_seen_age_sec` and `state`. Purges acknowledged removals. |
| `GET /devices/{id}` | One device plus its last 50 status reports. |
| `GET /devices/{id}/events?limit=&type=` | Recent events, newest first. `limit` is clamped to `EVENTS_LIMIT`. |
| `DELETE /devices/{id}/events` | Clear the stored event log only. Idempotent. |
| `POST /devices/{id}/users` | Replace the device's whole assigned user set (`{badge_id: user_data}`). |
| `DELETE /devices/{id}` | Remove a device (see below). |
| `GET /healthz` | Liveness probe, no auth. |

`POST /installation` body:
```jsonc
{ "customer_id": "acme", "site_id": "hq", "door_id": "main-entrance",
  "deviceType": "Genie Door Camera Lite",          // optional
  "device_mode": "card_and_face",                  // optional, see below
  // How a device without a network reaches the server. Default "local".
  "network_profile": { "mode": "wifi", "wifi": { "ssid": "acme-guest", "password": "s3cr3t" } } }
```
Response `201`: `_id`, `status` (`pending`), `payload` (the QR content) and
`qr_png` (data URI).

`device_mode` is `card_only`, `card_and_face` (default), `face_only` or
`time_registry`. The device applies it at boot.

The Wi-Fi password is in the QR in plain text. Anyone who photographs the QR can
read it, and the QR has no expiry.

### Terminal side

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /installation/pair` | none | Pair by `{installationId, mac, serial}`. |
| `GET /devices/{id}/users` | Bearer | The device's assigned face users, keyed by badge ID. |
| `POST /devices/{id}/status` | Bearer | Heartbeat with metadata and events. |

The token must belong to `{id}`, otherwise `403`. A bad token gives `401`. All
fields and rules are in [docs/DEVICE_SERVER_API.md](../docs/DEVICE_SERVER_API.md).

Notes on this server:

- The pair response adds `device_token`, `heartbeat_interval_sec` and
  `device_mode` to the installation summary. The token is stored only as a
  SHA-256 hash.
- Users are kept per device in a JSON file (`{device_id: {badge_id: user_data}}`),
  not in SQL. A new binding is seeded from `default_user_database.json`. This is
  the same for every customer, which is fine for a proof of concept.
- `metadata` in the status call is free-form and stored as-is. The kiosk sends
  `app_version`, `device_type`, `serial_port`, `user_count`, `camera_available`,
  `relay_available`, `session_active`, `init_mode_active`, `auth_in_progress`
  and `storage`. The server requires none of them.
- `metadata.events` is pulled out into the events table and removed from the
  stored metadata.

### Removing a device

`DELETE /devices/{id}` is a **soft delete**. The row is marked `suspended` and
kept, so the device's next heartbeat can be told it is gone. It is idempotent,
and `404` means the device never existed.

```jsonc
{ "ok": true, "device_id": "…", "state": "suspended" }
```

Lifecycle: `active` → (remove) `suspended` → the next heartbeat gets
**`410 Gone`** and the row becomes `revoked_ack` → the next `GET /devices`
purges it.

On the device, the `410` deletes `device_identity.json`, erases the local users
and faceprints, and returns to init mode, ready for a new QR. A device that is
offline stays `suspended` until it next checks in. The heartbeat that gets the
`410` is not recorded, and neither are the events of the device's final POST.

## Device events

A light event log for door telemetry: `access_granted`, `access_denied`,
`attendance_event`, `qr_accepted`, `db_sync_ok`, `hardware_error`, and so on.
The device side lives in `observability/events.py`. Events name a person only
by the opaque `user_id`, never a real name or card ID.

There is no extra endpoint. Events ride in `metadata.events` of the status
call. The device drops an event only after a `2xx`. The server does
`INSERT OR IGNORE` on the unique `event_id`, so a resend never duplicates, and
trims the log to `EVENTS_LIMIT` rows per device.

Each device page (`/device/{id}`) shows an event log with a type filter.
```jsonc
{ "event_id": "uuid", "ts": "2026-08-16T10:00:00Z", "type": "access_granted",
  "user_id": 1, "method": "card" }   // extra fields are stored as "data"
```

## Testing

```bash
python -m pytest server/tests -q
```

59 tests, no hardware needed, in four files: `test_installations.py`
(installation and pairing rules), `test_server_api.py` (the API through
`TestClient`), `test_device_binding.py` (a live uvicorn server driven by
`provisioning/client.py` and `provisioning/binding.py` over real HTTP) and
`test_events.py` (events are filed, deduplicated, bounded and served back).

## Deploying to Render

`render.yaml` at the repo root is a ready blueprint. Two things must be right:

- **Attach the disk.** Render's filesystem is ephemeral. Without the disk at
  `/var/data`, every deploy wipes the device registry.
- **Set `PUBLIC_BASE_URL`** to the service's public URL. It is the `serverLink`
  in every QR. If it is wrong, devices scan the code fine and then call nowhere.

The blueprint uses `PUBLIC_BASE_URL=https://rsid-face-guard.onrender.com`,
`DB_PATH=/var/data/faceguard.db` and
`USER_STORE_FILE=/var/data/server_user_database.json`.

- Before you switch an existing deployment to the persistent user-store path,
  copy its current assigned-user file there. Do not overwrite an existing
  persistent store.
- The SQLite migration is additive and keeps legacy device rows.
- Deploy the server before you update the kiosks.
- Installation IDs only exist on the server that created them. The device uses
  the `serverLink` from its QR.

Free-tier services sleep when idle. A sleeping server means heartbeats fail and
every device shows offline. Remember this before a demo.

---

## The device side

Implemented in `provisioning/` at the repo root:

| Module | Role |
|---|---|
| `identity.py` | Load/save/`clear()` `device_identity.json` (atomic write, mode 0600, gitignored). It holds the bearer token, the customer/site/door, the network profile and the device mode. `clear()` runs when the server removes the device. |
| `client.py` | The two HTTP calls: `pair()` and `post_status()`. |
| `heartbeat.py` | Daemon thread posting status every `heartbeat_interval_sec`, with capped backoff (300 s). Never raises into the kiosk. |
| `binding.py` | `BindingManager`, the flow the GUI calls. |
| `network.py` | Joins the QR's Wi-Fi through NetworkManager. |
| `hardware_identity.py` | Reads the Pi serial (`DEVICE_SERIAL_OVERRIDE` overrides it for development). |

`session/controller.py` calls `BindingManager.bind_async()` when a QR is
accepted. It pairs on a worker thread, because pairing is blocking HTTP and must
not touch the UI thread, and reports back through the window's `_SignalBridge`.
At startup the app calls `start_if_bound()`, so a paired device comes back
online after a reboot without rescanning.

Scanning a new QR re-pairs the device and replaces the old identity. A failed
pairing keeps the old identity.

**Removal.** When a heartbeat gets `410 Gone`, `BindingManager` queues
`device_revoked`, tries one final POST, drops the identity file and calls the
GUI, which stops sync, erases the local users and faceprints and returns to init
mode. A reboot comes back unbound.

Device-side settings are in the root `config.py` under "Device Binding". The
server URL is deliberately not configured there. It comes from the QR's
`serverLink`.

### Wi-Fi from the QR

When `config.APPLY_NETWORK_PROFILE` is `True` (set it only on the Pi), a QR
with a `wifi` profile makes the device join that network through NetworkManager
(`nmcli`) **before** it pairs. So a device with no LAN cable can come online
from the QR alone. A `local` profile does nothing.

`nmcli` needs polkit authorisation. The rule NetworkManager ships only grants
it to a real logind session, so a systemd service fails with
`Insufficient privileges`. Install the scoped rule from
`deploy/50-faceguard-nm.rules` into `/etc/polkit-1/rules.d/`. See
`docs/SETUP_RPI5.md` §6.2 for the details. If the Pi joins a *new* Wi-Fi it
drops its current connection, so do not pair over the Wi-Fi you are using for
SSH.

## Real-device demo runbook

1. **Run the server where the Pi can reach it.** A laptop on the same LAN is the
   lower-risk choice for a first run. Render also sleeps when idle.
   ```bash
   PUBLIC_BASE_URL=http://192.168.1.50:8000 uvicorn server.main:app --host 0.0.0.0 --port 8000
   ```
   Check from the Pi: `curl http://192.168.1.50:8000/healthz`.
2. **Create the installation.** Fill the form at `/new`.
3. **Pair.** Boot the device with `config.INIT_MODE_ENABLED = True` and hold the
   QR to the camera. Keep it near the middle of the picture, because the
   preview is cropped to the centre square. The log should show `qr_accepted`
   and `Device identity saved`, and the screen should read "Device paired".
4. **Confirm.** The device appears on the dashboard and goes green. `last_seen`
   ticks over every `heartbeat_interval_sec`. The detail page should show
   `user_count`, `camera_available` and `relay_available` matching reality.
5. **Reboot.** Restart the Pi. It must come back online without rescanning.
6. **Offline detection.** Stop the server or unplug the network. The dashboard
   flips the device to offline after `HEARTBEAT_TIMEOUT_SEC`, while the kiosk
   keeps authenticating faces from the local database. Restore the link and it
   recovers on its own.
7. **Removal.** Remove the device on the dashboard. Within one heartbeat the
   kiosk shows "Device removed", erases its users and waits for a new QR.
