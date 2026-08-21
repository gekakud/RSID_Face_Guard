# Device dashboard server

FastAPI + SQLite. Issues signed provisioning QR codes, registers devices that
scan them, receives their status heartbeats, and shows it all on a dashboard.

Independent of the device application — no PySide6, no `rsid_py`, no GPIO — so
it deploys to any plain Linux host.

## Run it

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --reload            # from the repo root
```

Then open <http://localhost:8000>. Interactive API docs at `/docs`.

To let a real device on the LAN reach it, bind all interfaces and tell the
server its own address — that URL is signed into every QR as `server_url`, and
it is how a device knows where to call back:

```bash
PUBLIC_BASE_URL=http://192.168.1.50:8000 uvicorn server.main:app --host 0.0.0.0 --port 8000
```

## Configuration

All optional; every value has a working default.

| Env var | Default | Notes |
|---|---|---|
| `DB_PATH` | `server/data/faceguard.db` | On Render, point at a mounted disk. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Signed into each QR as `server_url`. |
| `HEARTBEAT_TIMEOUT_SEC` | `90` | Older than this ⇒ the device shows offline. |
| `HEARTBEAT_INTERVAL_SEC` | `30` | Period handed to devices at registration. |
| `DEFAULT_VALIDITY_MINUTES` | `10` | Default QR lifetime. |
| `STATUS_HISTORY_LIMIT` | `200` | History rows kept per device. |
| `EVENTS_LIMIT` | `500` | Event-log rows kept per device. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | unset | Both set ⇒ HTTP Basic on the dashboard. Unset ⇒ open. |

## Customer / site / door

Devices are organised as **customer → site → door** (a door belongs to a site,
a site to a customer). The dashboard's `/new` page selects these from managed
dropdowns; you create records once and pick them thereafter (`+ add` opens a
popup to create one). The **names** — not the numeric ids — are what get signed into the QR
and stored on the device, so the device shows e.g. `acme-corp / main-entrance`.

- `GET /customers` · `POST /customers {name}`
- `GET /sites?customer_id=` · `POST /sites {customer_id, name}`
- `GET /doors?site_id=` · `POST /doors {site_id, name}`

POST is idempotent by name within its parent: creating a name that already
exists returns the existing record rather than erroring.

## The flow

1. Operator fills the form at `/new` (customer/site/door + network) →
   `POST /devices/generate-qr` mints a one-time token and returns it as a
   signed QR image.
2. Operator shows the QR to the device camera while the device is in init mode.
3. The device verifies the Ed25519 signature and expiry locally, then
   `POST /devices/register` redeems the token and receives a `device_token`.
4. The device `POST /devices/{id}/status` every 30s. It shows online on the
   dashboard for as long as those keep arriving.

## API

### `POST /devices/generate-qr` — dashboard
```jsonc
// request
{ "customer_id": "acme", "site_id": "hq", "door_id": "main-entrance",
  "validity_minutes": 10,
  // How a not-yet-networked device reaches the server. Defaults to "local".
  "network_profile": { "mode": "wifi", "wifi": { "ssid": "acme-guest", "password": "s3cr3t" } } }
  //                 or { "mode": "local" }   -- device already on a LAN cable
// response
{ "token": "...", "nonce": "...", "issued_at": "...", "expires_at": "...",
  "payload": { /* the full signed QR payload, incl. network_profile */ },
  "qr_png": "data:image/png;base64,..." }
```
The `network_profile` is signed into the QR (so it is tamper-proof) and stored
on the device. Note the Wi-Fi password is *signed but not encrypted* — anyone
who photographs the QR can read it.

**Applying it on the device.** When `config.APPLY_NETWORK_PROFILE` is `True`
(set it so only on the Pi), scanning a `wifi` QR makes the device actually join
that network via NetworkManager (`nmcli`) *before* it registers — so a device
with no LAN cable can come online from the QR alone. It is `False` by default so
a dev machine is never reconfigured; a `local` profile is always a no-op.

`nmcli`-based joining needs permission. Either run the service as root, or add a
polkit rule so the `geka` user may manage Wi-Fi, e.g.
`/etc/polkit-1/rules.d/50-faceguard-nm.rules`:
```javascript
polkit.addRule(function (action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
      subject.user === "geka") {
    return polkit.Result.YES;
  }
});
```
If the Pi is joining the *new* Wi-Fi it will drop its current connection, so
don't provision over the same Wi-Fi you're SSH'd in on.

> **Note:** the dashboard seeds a default **Demo Customer → Demo Site → Demo
> Door** the first time it starts with an empty database, so `/new` works
> immediately without creating anything. Real records you add sit alongside it.

### `POST /devices/register` — device
No auth; the provisioning token *is* the credential.
```jsonc
// request
{ "token": "<provisioning_token from the QR>",
  "nonce": "<nonce from the QR>",        // optional, cross-checked if sent
  "mac": "aa:bb:cc:dd:ee:ff",
  "device_type": "F455", "fw_version": "6.1.0", "app_version": "face-guard" }
// response
{ "device_id": "uuid", "device_token": "...", "heartbeat_interval_sec": 30,
  "customer_id": "acme", "site_id": "hq", "door_id": "main-entrance" }
```
`device_token` is returned **once** and stored server-side only as a SHA-256
hash. A device that loses it must be re-provisioned with a new QR.

Errors: `404` unknown token · `409` already used · `400` expired or nonce
mismatch.

### `POST /devices/{device_id}/status` — device
`Authorization: Bearer <device_token>`. The token must belong to `{device_id}`,
otherwise `403`.
```jsonc
// request
{ "status": "online", "metadata": { "user_count": 42, "camera_available": true } }
// response
{ "ok": true, "server_time": "2026-08-16T08:26:07Z" }
```
`metadata` is a free-form object — whatever the device sends is stored and
displayed as-is. No schema migration needed to add a field.

The kiosk currently sends: `app_version`, `device_type`, `serial_port`,
`user_count`, `camera_available`, `relay_available`, `session_active`,
`init_mode_active`, `auth_in_progress`, and `storage` (an object with
`path`, `total_mb`, `used_mb`, `free_mb`, `free_pct`, `low` — see
`observability/storage_monitor.py`). None of these are required by the
server; it's purely a display convention on the device side.

The reserved `metadata.events` key carries device telemetry (see
[Device events](#device-events)); it is pulled out into the events table and
stripped from the stored metadata, so it never appears in the "latest metadata"
panel.


### `DELETE /devices/{device_id}` — dashboard
Removes a device. This is a **soft delete**: the row is marked `suspended`
(kept as a tombstone) rather than erased, so the device's next heartbeat can be
told it's gone. Idempotent; `404` if the device never existed.
```jsonc
// response
{ "ok": true, "device_id": "uuid", "state": "suspended" }
```
Lifecycle: `active` → (Remove) `suspended` → the device's next heartbeat gets
**`410 Gone`** and the row flips to `revoked_ack` → the next dashboard device
list purges it. On the device, the 410 makes it delete its `device_identity.json`
and return to the unbound state (ready to scan a new QR); face auth from the
local DB is unaffected throughout. A removed device that is offline stays
`suspended` until it next checks in.

### Reads — dashboard
- `GET /devices` — all devices with derived `online`, `last_seen_age_sec`, and
  admin `state` (`active`/`suspended`/`revoked_ack`). Purges acknowledged
  removals as a side effect.
- `GET /devices/{id}` — the above plus the last 50 status reports.
- `GET /devices/{id}/events?limit=&type=` — recent device events, newest first,
  optionally filtered by `type`. `limit` is clamped to `EVENTS_LIMIT`.
- `GET /tokens` — unredeemed provisioning tokens.
- `GET /healthz` — unauthenticated liveness probe.

## Device events

A lightweight event log for door telemetry — `device_boot`, `access_granted`,
`access_denied`, `card_unknown`, `qr_accepted`, `qr_rejected`, `relay_opened`,
`hardware_error`, `db_sync_ok` / `db_sync_failed`, `init_mode_entered`,
`device_shutdown`, etc. The device-side emitter lives in `observability/events.py`.

**No new endpoint or connection.** Events piggyback on the existing heartbeat:
the device buffers them and sends them in `metadata.events` on the next status
POST. Each event is:
```jsonc
{ "event_id": "uuid",        // device-generated; enables idempotent resend
  "ts": "2026-08-16T10:00:00Z",
  "type": "access_granted",
  "user": "alice", "method": "card" }   // any extra fields become "data"
```

**Guaranteed delivery.** The device only drops an event from its buffer after
the heartbeat carrying it returns 2xx, so a failed beat re-sends on the next
one. Because a beat could be delivered but its response lost (device then
resends), the server does `INSERT OR IGNORE` on the unique `event_id` — so
resends never duplicate. The per-device log is trimmed to `EVENTS_LIMIT` rows.

The dashboard shows these in an **Event log** section on each device's detail
page (`/device/{id}`), with a type filter, auto-refreshing on the same cadence
as the device list.

## Signing

`server/signing.py` imports `sign_payload()` and the issuer key from
`other/qr_code_poc/`, rather than reimplementing either. The public half of that
key is already deployed at
`provisioning_keys/installer-signing-key-2026-01.pem`, which is exactly what the
device's `QRScanner` loads — so codes issued here verify on an unmodified
device, and canonical-JSON drift between the two sides is impossible.

Timestamps use `%Y-%m-%dT%H:%M:%SZ` because the device parses `expires_at` with
a literal `strptime` of that format and rejects anything else.

### Why every QR is decoded before it is returned

The signed payload is ~600 characters, which renders as a QR version 17 symbol.
The device reads these with **pyzbar (zbar)**, so `signing.generate()` verifies
each rendered image with that *same* decoder before returning it — an image the
device's decoder can't read is one an installer would hold up to the camera in
vain. If a render fails to decode, it re-signs with a fresh nonce (which changes
the module pattern) and retries.

pyzbar reads version-17+ symbols reliably, so this almost always passes on the
first attempt; the retry loop is just a cheap safety net. (This is a deliberate
switch away from OpenCV's `QRCodeDetector`, which dudded roughly 1 in 20 of
these large symbols even from a pixel-perfect PNG — that unreliability was the
original reason the self-check and retry loop exist.)

pyzbar needs the system shared library `libzbar0`. Where it isn't available
(e.g. a plain Render Python runtime with no apt), the self-check degrades to
"skip" rather than failing the request — the QR is still returned, just not
pre-verified server-side.

Error correction is set to L rather than M: it drops the symbol from version 19
to 17, so the modules are larger and easier for the camera to resolve. Error
correction buys little here — the code is displayed on a clean screen, and the
Ed25519 signature already covers integrity.

> The issuer private key is committed to this repo, so anyone with repo access
> can forge provisioning QRs. Acceptable for a POC; before production, generate
> a fresh keypair, ship only the public half to `provisioning_keys/`, and load
> the private half from a secret. Devices load *every* `*.pem` in that
> directory, so both key ids can coexist during rotation.

## Expiry

Checked twice against the same signed `expires_at`:

- **On the device**, in `QRScanner._verify()` — a stale QR never reaches the
  network. `expires_at` is inside the signed blob, so it cannot be edited.
- **On the server**, at `/devices/register` — the server never assumes the
  device honoured the first check, since a device could be replaying an old
  capture.

## Testing

```bash
python -m pytest server/tests -q
```

Three files, 62 tests, no hardware required:

- **`test_provisioning.py`** drives the API through `TestClient`. The tests that
  matter most run server-generated QR *images* through the real, unmodified
  device verifier (`qr_scanner/qr_scanner.py`) and assert they are accepted,
  that replays are rejected, that expired codes are rejected, and that 25
  consecutive codes are all decodable. They skip if `numpy`/`pyzbar` are absent.
- **`test_device_binding.py`** starts a live uvicorn server and exercises
  `provisioning/client.py` and `provisioning/binding.py` over real HTTP — the
  same code the Pi runs, including the reboot-resumes case and the "server
  unreachable" path. It imports no PySide6 and no `rsid_py`.
- **`test_events.py`** posts heartbeats carrying `metadata.events` and asserts
  they are filed, deduped by `event_id`, bounded to `EVENTS_LIMIT`, stripped
  from stored metadata, and served back (with type filtering) by the events
  endpoint.

### Fake device

Exercises the whole flow with no hardware. Its requests are exactly the ones the
real device will make.

```bash
# 1. generate a QR (or use the /new page and save the payload)
curl -s -X POST http://localhost:8000/devices/generate-qr \
     -H 'Content-Type: application/json' \
     -d '{"customer_id":"acme","site_id":"hq","door_id":"main-entrance"}' \
  | python -c "import json,sys; json.dump(json.load(sys.stdin)['payload'], open('payload.json','w'))"

# 2. register + heartbeat
python -m server.tools.fake_device --payload-file payload.json --interval 15
```

The device appears on the dashboard and goes green. Stop it with Ctrl-C and it
flips to offline after `HEARTBEAT_TIMEOUT_SEC`.

## Deploying to Render

`render.yaml` at the repo root is a ready blueprint. Two things to get right:

- **Attach the disk.** Render's filesystem is ephemeral; without the declared
  disk at `/var/data`, every deploy wipes the device registry.
- **Set `PUBLIC_BASE_URL`** to the service's public URL. It is signed into every
  QR, so if it is wrong, devices scan the code fine and then call nowhere.

Free-tier services sleep when idle. A sleeping server means heartbeats fail and
every device shows offline — worth knowing before a demo.

## The device side

Implemented in `provisioning/` at the repo root:

| Module | Role |
|---|---|
| `identity.py` | Load/save/`clear()` `device_identity.json` (atomic write; holds the bearer token + the customer/site/door and network_profile, so it is gitignored). `clear()` is used when the server revokes the device. |
| `client.py` | The two HTTP calls — `register()` and `post_status()`. |
| `heartbeat.py` | Daemon thread posting status every `config.HEARTBEAT_INTERVAL_SEC`, with capped backoff. Never raises into the kiosk. |
| `binding.py` | `BindingManager` — the flow both GUIs call. |

`_on_qr_detected()` in `gui_web/web_window.py` and `gui_qt/main_window_qt.py`
now calls `BindingManager.bind_async()`, which registers on a worker thread
(registration is blocking HTTP and must not touch the UI thread) and reports
back through each window's existing `_SignalBridge`. At startup both GUIs call
`start_if_bound()`, so a device that was provisioned on an earlier run comes
back online after a reboot without rescanning anything.

Rescanning a new QR re-binds the device to the new door, replacing the old
identity. A failed registration leaves the device unbound rather than
half-configured.

**Removal handling.** Both GUIs pass an `on_revoked` callback to
`BindingManager`. When a heartbeat gets `410 Gone` (the device was removed on
the dashboard), the heartbeat thread drops the identity file via
`identity.clear()`, goes unbound, and the GUI shows "Device removed — Rescan a
QR to re-enroll". A reboot then comes back unbound, ready for a fresh QR.

Device-side config lives in the root `config.py` under "Device Binding". Note
the server URL is deliberately *not* configured there — it comes from the signed
QR payload, which is how a fresh device learns which deployment it belongs to.

## Real-device demo runbook

1. **Run the server where the Pi can reach it.** A laptop on the same LAN is the
   lower-risk choice for a first run — fewer moving parts than Render, which
   also sleeps when idle.
   ```bash
   PUBLIC_BASE_URL=http://192.168.1.50:8000 uvicorn server.main:app --host 0.0.0.0 --port 8000
   ```
   Check from the Pi: `curl http://192.168.1.50:8000/healthz`.
2. **Generate.** Fill the form at `/new`, leave validity at 10 minutes.
3. **Bind.** Boot the device with `config.INIT_MODE_ENABLED = True` and hold the
   QR to the camera. `face_guard.log` should show `QR scan result: ACCEPTED`
   followed by `Registered successfully: device_id=…`, and the screen should
   read "Device registered".
4. **Confirm.** The device appears on the dashboard and goes green; `last_seen`
   ticks over every 30s. The detail page should show `user_count`,
   `camera_available` and `relay_available` matching reality.
5. **Reboot.** Restart the Pi. It must return online *without* rescanning.
6. **Offline detection.** Stop the server or unplug the network. The dashboard
   flips the device to offline after `HEARTBEAT_TIMEOUT_SEC`, while the kiosk
   keeps authenticating faces from the local database. Restore the link and it
   recovers on its own.
7. **Expired QR.** Generate with `validity_minutes=1`, wait two minutes, then
   show it. `qr_scanner` must reject it (`token expired` in the log) and no
   device should appear.
