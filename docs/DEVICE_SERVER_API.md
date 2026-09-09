# RSID Face Guard — Device ↔ Server API

What the terminal (the door device) and your server send each other. Four
endpoints, in the order you will build them.

---

## 1. What you are building

| # | Endpoint | Called by | Auth | How often |
|---|---|---|---|---|
| 1 | `POST /devices/generate-qr` | your dashboard | your own operator login | when a technician installs a terminal |
| 2 | `POST /devices/register` | terminal | none — the one-time token in the body is the credential | once per terminal |
| 3 | `GET /devices/{device_id}/users` | terminal | `Authorization: Bearer <device_token>` | every 600 s |
| 4 | `POST /devices/{device_id}/status` | terminal | `Authorization: Bearer <device_token>` | every 30 s |

There is also one operator action that is **not** an endpoint the terminal
calls: removing a terminal. See [§9](#9-removing-a-terminal).

Three facts that shape everything else:

- **The terminal decides access on its own.** It never asks you whether to open
  a door. It matches the card and face against the user list it downloaded from
  endpoint 3 and cached locally, so doors keep working while your server is
  down. What you control is *what is in that list*.
- **That list is a full replacement set.** Whatever endpoint 3 returns is
  exactly what the door will honour from then on. Anyone missing from a `200`
  loses access.
- **You hold the signing key.** You sign the provisioning QR with an Ed25519
  private key; terminals carry only the public half and verify offline.

---

## 2. Before you start: the signing key

1. Generate an Ed25519 keypair. The private key stays on your server, always.
2. Give it a `key_id` string, e.g. `installer-signing-key-2026-01`.
3. Send us the **public** key (PEM) plus that `key_id` — we deploy it to the
   terminals' trust store. Terminals can hold several keys at once, so rotation
   is: deploy the new public key first, then switch the signer.

---

## 3. Conventions

- **HTTPS** with a valid certificate in production; terminals verify it.
- **Auth header** on endpoints 3 and 4: `Authorization: Bearer <device_token>`.
  Check the token belongs to the `device_id` in the URL, else `403`.
- **Timestamps**: UTC, exactly `YYYY-MM-DDTHH:MM:SSZ` — no offsets, no
  fractional seconds. Example: `2026-07-27T15:02:00Z`.
- **Errors**: a JSON body with a `detail` string.

  ```json
  { "detail": "Provisioning token expired — generate a new QR" }
  ```

  On registration, this text is shown on the terminal screen to the technician
  standing at the door, so write it for them.

- **Status codes the terminal reacts to**:

| Code | What the terminal does |
|---|---|
| `2xx` | Success. |
| `400` / `404` / `409` on **registration only** | Permanent failure for that token. Shows your `detail` on screen; the technician must scan a new QR — the terminal does not retry. |
| `401` / `403` on **heartbeat / user sync** | Bad or mismatched token. This one call fails, but the terminal keeps calling on its normal schedule and keeps working from its cached data — nothing stops. |
| `410` | **This device was removed.** Wipes its identity, users and faceprints. Use it for nothing else ([§9](#9-removing-a-terminal)). |
| `5xx` | Temporary. Backs off, retries, keeps its cached data. |

Report your own failures as `5xx`, never as one of the codes above. `400`,
`404` and `409` mean "this specific request can never succeed" — the terminal
stops *that one action* (e.g. gives up on registering with this token) rather
than retrying it, since retrying an already-used or expired token would just
fail the same way again. `401`/`403` don't stop anything: the heartbeat and
user-sync calls keep firing on their normal schedule regardless.

---

## 4. The flow

### 4.1 Installing a terminal — happens once

```
   +----------------------------------------------------------------------+
   | 1   DASHBOARD  -->  SERVER      POST /devices/generate-qr            |
   |                                                                      |
   |     { customer_id, site_id, door_id, device_mode,                    |
   |       validity_minutes, network_profile }                            |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | 2   SERVER                                                           |
   |                                                                      |
   |     - create a one-time token + nonce, store them                    |
   |     - build the QR envelope, sign it (canonical JSON + Ed25519)      |
   |     - render the QR PNG, check it decodes before returning it        |
   |                                                                      |
   |     -> { token, nonce, expires_at, payload, qr_png }                 |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | 3   TECHNICIAN  -->  TERMINAL   holds the QR to the camera           |
   |                                                                      |
   |     the terminal verifies it OFFLINE, no network call:               |
   |     signature, schema, command, expires_at                           |
   |     then joins Wi-Fi if the QR carried a wifi profile                |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | 4   TERMINAL  -->  SERVER       POST /devices/register               |
   |                                 (no Authorization header)            |
   |                                                                      |
   |     { token, nonce, mac, device_type, fw_version, app_version }      |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | 5   SERVER                                                           |
   |                                                                      |
   |     - is the token known, unused and not expired?                    |
   |     - does the nonce match the one stored with that token?           |
   |     - mark the token used AND bind the device, in one transaction    |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | 6   SERVER  -->  TERMINAL       200                                  |
   |                                                                      |
   |     { device_id, device_token, heartbeat_interval_sec,               |
   |       customer_id, site_id, door_id, device_mode, registered_at }    |
   |                                                                      |
   |     the terminal saves this, then starts calling the two             |
   |     endpoints below on its own                                       |
   +----------------------------------------------------------------------+
```

If step 5 fails, nothing is bound and the technician generates a new QR:

```
   +----------------------------------------------------------------------+
   | 5b  SERVER  -->  TERMINAL       400 / 404 / 409                      |
   |                                                                      |
   |     { "detail": "Provisioning token already used -- generate         |
   |                  a new QR" }                                         |
   |                                                                      |
   |     nothing is bound; your detail text is shown on the terminal      |
   |     screen to the technician                                         |
   +----------------------------------------------------------------------+
```

### 4.2 Normal running — from then on

```
   +----------------------------------------------------------------------+
   | every 600 s     GET /devices/{device_id}/users                       |
   |                 Authorization: Bearer <device_token>                 |
   |                                                                      |
   |     -> 200 { "2325780402": { user_id, name, active, faceprints },    |
   |              "1049337812": { ... } }                                 |
   |                                                                      |
   |     the terminal REPLACES its whole local user list with this        |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | every 30 s      POST /devices/{device_id}/status                     |
   |                 Authorization: Bearer <device_token>                 |
   |                                                                      |
   |     { status, metadata { ...device state..., events: [ ... ] } }     |
   |                                                                      |
   |     -> 200 { "ok": true }                                            |
   |        means you stored the events; the terminal deletes them        |
   +----------------------------------------------------------------------+
                                       |
                                       v
   +----------------------------------------------------------------------+
   | after you remove the device on your dashboard                        |
   |                                                                      |
   |     -> 410 on the next status call                                   |
   |                                                                      |
   |     the terminal wipes its identity, users and faceprints,           |
   |     denies everyone, and waits for a new QR   (see section 9)        |
   +----------------------------------------------------------------------+
```

---

## 5. `POST /devices/generate-qr` — create a provisioning QR

Called by your dashboard when a technician is installing a terminal. You create
a one-time token, put it in a signed envelope, and render that envelope as a QR
image for the technician to hold up to the camera.

### Request

```json
{
  "customer_id": "acme",
  "site_id": "hq",
  "door_id": "main-entrance",
  "device_mode": "card_and_face",
  "validity_minutes": 10,
  "network_profile": {
    "mode": "wifi",
    "wifi": { "ssid": "acme-installers", "password": "s3cr3t-pass" }
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `customer_id`, `site_id`, `door_id` | yes | 1–64 chars. Human-readable names from your dashboard, not numeric primary keys. Signed into the QR and stored on the device. |
| `device_mode` | no | One of the three values below. Default `card_and_face`. |
| `validity_minutes` | no | Keep it around 10. Anyone who photographs the QR can read the Wi-Fi password out of it, and the QR stays usable until it expires. |
| `network_profile` | no | `{"mode": "local"}` (default, terminal is already cabled) or `{"mode": "wifi", "wifi": {"ssid": "...", "password": "..."}}` — the terminal joins that network before registering. |

`device_mode` values:

| Mode | Behaviour at the door |
|---|---|
| `card_only` | Valid card opens the relay. No face step. |
| `card_and_face` | Valid card starts a session, then the face is verified against that cardholder. **Normal production mode.** |
| `face_only` | Screen tap starts a face search against all enrolled users. For doors with no card reader. |

A fourth mode, `time_registry`, is **not implemented yet** — reject it for now
([§10](#10-not-implemented-yet)).

### Response

```json
{
  "token": "kR3vQ8mN2pL7xW4tY6bZ1aC5",
  "nonce": "8f2c1a4b-6d3e-4f7a-9b2c-5e8d1f3a7c9b",
  "issued_at": "2026-07-27T14:52:00Z",
  "expires_at": "2026-07-27T15:02:00Z",
  "payload": {
    "schema": "acme.provisioning-qr.v1",
    "command": "provision_device",
    "server_url": "https://access.example.com"
  },
  "qr_png": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."
}
```

`payload` is the complete signed envelope that went into the QR image (§5.1),
returned only so you can inspect it while debugging — shortened above.
`qr_png` drops straight into an `<img src="...">` on your dashboard.

Store the token (hashed), the nonce, the expiry and the chosen `device_mode`
together — you need all four when the terminal registers.

### 5.1 The QR envelope

This exact JSON is what goes into the QR image, and what the terminal parses:

```json
{
  "schema": "acme.provisioning-qr.v1",
  "command": "provision_device",
  "server_url": "https://access.example.com",
  "customer_id": "acme",
  "site_id": "hq",
  "door_id": "main-entrance",
  "provisioning_token": "kR3vQ8mN2pL7xW4tY6bZ1aC5",
  "issued_at": "2026-07-27T14:52:00Z",
  "expires_at": "2026-07-27T15:02:00Z",
  "nonce": "8f2c1a4b-6d3e-4f7a-9b2c-5e8d1f3a7c9b",
  "network_profile": { "mode": "local" },
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "installer-signing-key-2026-01",
    "value": "3rT9xK2mQ7pL...base64url of the 64-byte signature"
  }
}
```

- `schema` must be exactly `acme.provisioning-qr.v1` and `command` exactly
  `provision_device`. Anything else is rejected by the terminal.
- `server_url` is your public base URL. The terminal registers against it and
  stores it — get it wrong and the device is bound to an unreachable host.
- `device_mode` is deliberately **not** here; it comes back in the register
  response instead. Do not add fields to this envelope: it is already ~600
  characters, about the largest QR the camera reads reliably off a phone screen.

### 5.2 Signing — get this exactly right

If your signing bytes differ from what the terminal computes by even one
character, **every terminal rejects every QR you produce**, and you get no error
on your side.

1. Build the envelope **without** the `signature` key.
2. Serialise it to canonical JSON: keys sorted, no whitespace, UTF-8.
3. Sign those bytes with raw Ed25519 (not pre-hashed, not `Ed25519ph`).
4. Base64url-encode the 64-byte signature (the `-_` alphabet, RFC 4648 §5) and
   attach it as `signature.value`.

```python
import json, base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def sign_envelope(envelope: dict, private_key: Ed25519PrivateKey, key_id: str) -> dict:
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    message = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope["signature"] = {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "value": base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii"),
    }
    return envelope
```

The terminal verifies by stripping `signature` and rebuilding the same bytes, so
the signature covers every other field, including `network_profile`.

Ways this goes wrong, all of them fatal and silent:

- Unsorted keys, or pretty-printed JSON with spaces or newlines.
- Standard base64 (`+/`) instead of base64url (`-_`).
- Signing the string *after* adding a placeholder `signature` field.
- Passing the envelope through a library that reorders or reformats nested
  objects before signing.

### 5.3 Rendering the QR

- Error correction level **L**, QR version 17.
- Decode the PNG you just produced before returning it. If it does not decode,
  re-render with a fresh nonce (our reference implementation retries up to 6
  times). A QR no decoder can read is worse than an error, because the
  technician only finds out at the door.

---

## 6. `POST /devices/register` — the terminal redeems the token

The terminal calls this after verifying the QR offline and joining Wi-Fi if
needed. **No `Authorization` header** — the one-time token in the body is the
credential.

### Request

```json
{
  "token": "kR3vQ8mN2pL7xW4tY6bZ1aC5",
  "nonce": "8f2c1a4b-6d3e-4f7a-9b2c-5e8d1f3a7c9b",
  "mac": "aa:bb:cc:dd:ee:ff",
  "device_type": "F455",
  "fw_version": "6.1.0",
  "app_version": "face-guard-0.1.0"
}
```

Only `token` is guaranteed. Every other field may be `null` or missing — MAC
detection can fail, and a dev box without the SDK sends `"unknown"`. Do not
reject on that.

### What to check, in one transaction

1. The token exists, has not been used, and `expires_at` has not passed. Check
   the expiry even though the terminal already did — a terminal could be
   replaying an old photo of a QR. Single-use tokens plus this check are the
   whole replay defence.
2. If `nonce` is present, it matches the nonce you stored with that token.
3. Mark the token used **and** create the device row in the same transaction, so
   a crash between the two cannot leave a usable token pointing at a live
   device.

### Response — `200`

```json
{
  "device_id": "3f9a2c7e-5b18-4d6f-a91c-7e2b4d8f1a30",
  "device_token": "v8Kq2Xn5Rt7Yw1Zb4Ce6Df9Gh0Jk3Lm5Np8Qr1St4Uv",
  "heartbeat_interval_sec": 30,
  "customer_id": "acme",
  "site_id": "hq",
  "door_id": "main-entrance",
  "device_mode": "card_and_face",
  "registered_at": "2026-07-27T15:02:00Z"
}
```

- `device_token` is returned **exactly once** and is never retrievable again.
  Store only a hash of it (e.g. SHA-256), and never log it.
- `heartbeat_interval_sec` is yours to decide — whatever you return becomes the
  terminal's heartbeat rate for the life of the binding. Use it to control your
  own load.
- `device_mode` must be the mode you stored with that token. If it somehow has
  none, fall back to `card_and_face`.
- The terminal writes this whole object to a local credential file and starts
  calling endpoints 3 and 4.

### Failures

| Status | Condition | `detail` to return |
|---|---|---|
| `404` | Token unknown | `Unknown provisioning token` |
| `409` | Token already used | `Provisioning token already used — generate a new QR` |
| `400` | Token expired | `Provisioning token expired — generate a new QR` |
| `400` | `nonce` does not match the token | `Nonce does not match token` |

### 6.1 A terminal registering again replaces its old binding

Moving a terminal to another door is done by simply showing it a new QR. Being
already bound is **not** an error.

Match on a stable identifier (`mac`, or whatever device identity you keep) and
**update the existing row** — new `door_id`, new `device_mode`, new
`device_token` — instead of inserting a second device. The old `device_token`
must stop working. Otherwise every re-installation leaves behind a live row
holding a valid token, still counted as a device at its old door.

---

## 7. `GET /devices/{device_id}/users` — the door's user list

Bearer authenticated. This is the data the door runs on. Return **only the users
allowed through this terminal's door**.

### Response — `200`

A JSON object keyed by card/badge id:

```json
{
  "2325780402": {
    "user_id": 1,
    "name": "Emma Stone",
    "active": true,
    "permission_level": "employee",
    "faceprints": {
      "version": 9,
      "features_type": 0,
      "flags": 3,
      "adaptive_descriptor_nomask": [-142, 87, -310, 455, 12, 2, 0, 0]
    }
  },
  "1049337812": {
    "user_id": 2,
    "name": "John Doe",
    "active": true,
    "permission_level": "employee",
    "faceprints": {
      "version": 9,
      "features_type": 0,
      "flags": 3,
      "adaptive_descriptor_nomask": [93, -204, 71, -388, 626, 2, 0, 0]
    }
  }
}
```

The descriptor arrays above are shortened for readability — each one is always
exactly 515 integers.

### 7.1 Record fields

| Field | Required | Notes |
|---|---|---|
| *(the key)* | yes | Card/badge id exactly as the reader reads it off the card. Must be a **string**, even when numeric. |
| `user_id` | **yes** | Stable person id. Any non-null integer or string; we never interpret it. A record without it is skipped. This — never the card id — identifies the person in every event we send you. |
| `faceprints` | **yes** | Face data from the RealSense SDK, see §7.2. A record without a valid one is skipped. |
| `name` | no | Shown on the welcome screen. Defaults to `""`. |
| `active` | no | `false` = kept on the device but never opens the door. Defaults to `true`. This is how you disable a person. |
| `permission_level` | no | Informational only; the device does no permission check. Defaults to `"User"`. |

Every enrolled person has a faceprint, in every mode — `card_only` is a property
of a *door*, not of a person. "A user with no faceprints" is not a supported
case.

### 7.2 The `faceprints` object

Pass it through **exactly** as it was given to you at enrolment. Store it as
opaque JSON: do not truncate, round, reorder, or convert the numbers to floats.

| Key | Type | Value |
|---|---|---|
| `version` | int | Currently **9**. Must match the device firmware, or every comparison silently fails and nobody gets in. |
| `features_type` | int | `0` in practice. |
| `flags` | int | `3` in practice. |
| `adaptive_descriptor_nomask` | array of int | **Exactly 515** integers, each within **±1023**. |

The 515 length is a hard requirement: 512 face features plus 3 slots, where
index 512 is a flag (value `2`) and 513–514 are `0`. Trimming the array to the
512 "real" features breaks matching.

Do **not** send `adaptive_descriptor_withmask` or `enroll_descriptor`. The
terminal ignores both, and together they are about two thirds of the payload
size for no benefit.

### 7.3 Two rules you must not break

**(a) Only this door's users.** The terminal treats the presence of an `active`
record in its local list as the permission itself — no further check, no
schedules, no per-door rules on the device. If another door's user appears in
this response, that user can open this door. Any access rules you need must be
applied by deciding what goes into this payload.

**(b) All or nothing.** This is a full replacement, not a delta, and there is no
"removed users" list. Anyone missing from a well-formed `200` is deleted from
the device, faceprints and all.

- Never return a partial list — not on a slow query, not on a partly failed DB
  read, not paginated. A truncated `200` silently removes access for everyone
  missing from it.
- If you cannot build the complete list, return **`5xx`**. The terminal keeps its
  existing list and retries. A failed sync never clears anything.

### 7.4 What a bad record does

| Problem | Result |
|---|---|
| Missing `user_id`, or a missing key inside `faceprints` | Record is skipped cleanly at sync; the rest of the payload is fine. We send you a `db_sync_invalid_record` event. |
| `adaptive_descriptor_nomask` not exactly 515 ints | Sync succeeds, then the SDK rejects the array when that person presents their face. It surfaces as a `hardware_error` and **blocks all access at that door for 20 s**, on every attempt. |
| Wrong `version` | No error anywhere. Every comparison silently fails, so **every user is denied**. |

Treat `db_sync_invalid_record` and `db_sync_skipped_entries` events as bugs on
your side: they mean we received records we could not use.

---

## 8. `POST /devices/{device_id}/status` — heartbeat and events

Bearer authenticated, sent every `heartbeat_interval_sec`. It reports the
device's current state and carries any queued events in the same request.

### Request

```json
{
  "status": "online",
  "metadata": {
    "app_version": "face-guard-0.1.0",
    "device_type": "F455",
    "serial_port": "/dev/ttyACM0",
    "user_count": 42,
    "camera_available": true,
    "relay_available": true,
    "session_active": false,
    "init_mode_active": false,
    "auth_in_progress": false,
    "storage": {
      "path": "/", "total_mb": 29820.0, "used_mb": 17540.0,
      "free_mb": 12280.0, "free_pct": 41.2, "low": false
    },
    "events": [
      {
        "event_id": "b2c4e6f8-1a3c-4d5e-8f90-2b4d6e8a0c1f",
        "type": "access_granted",
        "ts": "2026-07-27T15:04:11Z",
        "user_id": 1,
        "method": "card"
      },
      {
        "event_id": "d7e9f1a3-5c7e-4a9b-b1d3-6f8a0c2e4d60",
        "type": "access_denied",
        "ts": "2026-07-27T15:05:02Z",
        "user_id": 2,
        "reason": "face_mismatch"
      }
    ]
  }
}
```

Treat `metadata` as an **open object**: store it as JSON, do not validate its
shape, do not reject unknown keys. Its contents are diagnostic and will grow
between device releases. `metadata.events` is the one part with rules — see
§8.1.

### Response — `200`

```json
{ "ok": true, "server_time": "2026-07-27T15:04:12Z" }
```

The terminal only looks at the status code; the body is ignored. It does not
read configuration back from this response, so returning a new `device_mode` or
`heartbeat_interval_sec` here has no effect today.

### 8.1 Events: store them before you answer `200`

Events sit in a 200-entry in-memory buffer on the device (oldest dropped first)
and are deleted **as soon as you answer `2xx`**. So:

- **`2xx` means "every event in that array is safely stored."** Persist them
  first, then respond. Once you answer, they are gone from the device.
- **Anything other than `2xx` keeps them buffered** and the terminal resends
  them on the next heartbeat. Never answer `2xx` after storing only some of
  them.
- **Ignore duplicate `event_id`s** — a unique index plus `INSERT OR IGNORE`. If
  your response is lost after you stored the events, the terminal resends the
  same `event_id`s, and without this you get duplicate door records.

Every event has `event_id` (uuid4), `type`, `ts` (the device's UTC clock), plus
a few fields specific to the type. Record your own `received_at` as well —
device clocks drift.

### 8.2 Event types

Store `type` as a plain string. Accept unknown types rather than rejecting them:
the terminal is permissive and this list will grow.

| Type | Meaning and extra fields |
|---|---|
| `access_granted` | Door opened. `user_id`, `method` |
| `access_denied` | Refused. `user_id` if known, plus `reason`: `face_mismatch` (a genuine refusal), `no_faceprints_on_file` or `face_extraction_failed` (usually a data problem on your side) |
| `access_output_failed` | Access approved but the relay pulse failed |
| `auth_matched` | Face matched, logged before the access decision |
| `relay_opened` | Relay triggered |
| `card_unregistered` | Card is not in the device's list |
| `device_boot` / `device_shutdown` | Terminal started / stopped |
| `device_revoked` | Sent right before the terminal wipes itself after a `410` |
| `init_mode_entered` | Terminal is waiting for a provisioning QR |
| `qr_accepted` / `qr_rejected` | QR scan outcome; `qr_rejected` carries a `reason` |
| `db_sync_ok` / `db_sync_failed` | User sync outcome, with counts or a `reason` |
| `db_sync_invalid_record` / `db_sync_skipped_entries` | **Records you sent that we could not use — watch these** |
| `db_users_revoked` | Users deleted locally because they were missing from your payload |
| `hardware_error` | Camera, card reader or relay fault |
| `storage_low` / `storage_ok` | Disk space crossed a threshold |
| `attendance_event` | Not used yet ([§10](#10-not-implemented-yet)) |

Events always identify a person by `user_id`, never by card id or name.

---

## 9. Removing a terminal

The terminal never calls anything to be removed and never asks. It finds out by
getting **`410`** on its next heartbeat, and that single response is the whole
mechanism.

So this is an effect you must produce, not a URL we depend on — build whatever
operator action you like (our mock server uses `DELETE /devices/{device_id}`).

### 9.1 Keep the row — a hard delete cannot revoke anything

This is the one choice that looks right and is not. If you delete the device row
when the operator clicks remove, the terminal's token resolves to nothing and
its heartbeat comes back `401`/`404`. The terminal treats that as an ordinary
rejected heartbeat: it backs off, **retries forever, keeps its cached users and
faceprints, and keeps opening the door.** Only `410` triggers the wipe.

So mark the device removed and keep the row:

| State | Meaning | Answer to `POST /status` |
|---|---|---|
| `active` | Normal | `200` |
| `suspended` | Operator removed it, device not told yet | `410`, then move it to `revoked_ack` |
| `revoked_ack` | Device has been told at least once | `410` |
| *(row deleted)* | Cleaned up after acknowledgement | `401` |

Delete the row only after the device has been told (`revoked_ack`). Keep a
`suspended` row indefinitely — a terminal that is switched off gets told on its
next boot, and one that never returns costs you a single row.

### 9.2 What to expect

- Removal takes effect on the **next heartbeat** — up to
  `heartbeat_interval_sec` for a running terminal, or next boot for one that is
  off. Your UI should distinguish "removal pending" from "acknowledged".
- **Expect one final heartbeat right after you answer `410`**, carrying a
  `device_revoked` event, sent while the terminal still has a valid token.
  Accept it and store its events — it is your proof the wipe happened.
- **Answer `410` on `GET /devices/{device_id}/users` too**, otherwise a removed
  terminal can still refresh its full user list in the window before its next
  heartbeat.
- On `410` the terminal deletes its identity, **erases all users and
  faceprints**, denies everyone, and goes back to waiting for a QR. You are the
  master copy of the face data; the first sync after re-provisioning restores it.
- There is no un-remove. Re-provisioning with a new QR gives the terminal a
  **new** `device_id`. To disable a *person* rather than a terminal, set their
  record `active: false` (§7.1).

---

## 10. Not implemented yet

`time_registry` mode (working-hours IN/OUT logging) is planned but built on
neither side. Reject `time_registry` as a `device_mode` for now.

When it lands, the terminal will show an IN/OUT screen instead of a
screensaver, trigger no relay, and send `attendance_event` events (`user_id`,
`direction` of `in`/`out`, `ts`) through the normal heartbeat channel with the
same `event_id` duplicate handling. Attendance events will be queued on disk
rather than in memory: a lost check-in is a payroll error, not just lost
telemetry.
