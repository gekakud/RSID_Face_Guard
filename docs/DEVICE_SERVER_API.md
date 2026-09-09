# RSID Face Guard — Device ↔ Server API Contract

| Item | Detail |
|---|---|
| Document ID | API-FG-001 |
| Revision | 1.2 (2026-09-07) |
| Audience | Server / dashboard developer |
| Scope | **Only** the endpoints the terminal and the technician app call |
| Companion | [SOFTWARE_REQUIREMENTS.md](SOFTWARE_REQUIREMENTS.md) — device-side spec, not required reading |

## 1. Trust model in one page

Three things establish trust, in this order:

1. **A signed QR code.** An operator mints a provisioning QR from the
   dashboard; a technician holds it up to the terminal's camera. The envelope
   is Ed25519-signed by *you*. The terminal ships with the **public** key only
   and verifies the QR **entirely offline** — no network call. This is how a
   factory-fresh terminal learns which deployment it belongs to: the server URL
   comes from the QR, never from device configuration.
2. **A one-time provisioning token**, carried inside the QR. The terminal
   redeems it at `POST /devices/register`. This token is the *only* credential
   for that call, and it is single-use — that is what stops someone
   photographing a QR and provisioning their own hardware later.
3. **A long-lived bearer `device_token`**, issued in the registration response.
   Every subsequent device call presents it.

Two consequences that drive most of the design:

- **You hold the Ed25519 private key. Terminals never do.** Key management,
  storage and rotation are yours. Terminals hold one PEM per `key_id` in a
  local trust store directory.
- **The terminal never asks the server whether to open a door.** It
  authorises from a locally cached user set. A terminal with a valid cache
  performs the *complete* access flow — card lookup, face verification,
  decision, relay, attendance — with the server unreachable. Your availability
  is not the door's availability. Losing the network changes only what the
  terminal can *report* and how fresh its data is, never what it can *decide*.

---

## 2. Endpoint summary

Four endpoints. That is the entire device-facing surface.

| # | Endpoint | Caller | Auth | Cadence |
|---|---|---|---|---|
| 1 | `POST /devices/generate-qr` | Technician / dashboard | Your operator auth | On demand |
| 2 | `POST /devices/register` | Terminal | **None** — the provisioning token is the credential | Once per binding |
| 3 | `POST /devices/{device_id}/status` | Terminal | Bearer `device_token` | Every `heartbeat_interval_sec` (default 30 s) |
| 4 | `GET /devices/{device_id}/users` | Terminal | Bearer `device_token` | Every 600 s (`DB_SYNC_INTERVAL_SEC`) |

There is deliberately **no revoke endpoint in this table**. Revoking a terminal
is an *operator* action on your dashboard, and the terminal learns of it only
passively — by being answered `410` on its next heartbeat. It is nonetheless a
required part of this contract: see [§7](#7-revocation--removing-a-terminal).

### General rules

- **Transport** — HTTPS with a valid certificate in production. Terminals
  validate certificates.
- **Timestamps** — every timestamp exchanged in either direction shall be UTC
  in exactly `%Y-%m-%dT%H:%M:%SZ` (e.g. `2026-07-27T15:02:00Z`). No offsets,
  no fractional seconds, no local time.
- **Status codes carry meaning.** Terminals branch on them:
  - `2xx` — success.
  - `401` / `403` — bad or mismatched bearer token.
  - `404` / `409` / `400` on register — permanent, actionable failures whose
    `detail` string is shown to the technician on the kiosk screen.
  - **`410` — this device has been removed.** Irreversible and destructive on
    the terminal. See [§6.3](#63-410-gone--device-removal-and-what-it-destroys).
  - `5xx` — transient; the terminal backs off and retries.
- **Error bodies** — return a JSON object with a human-readable `detail`
  string: `{"detail": "Provisioning token already used"}`. On registration
  failure this string is displayed verbatim to the technician standing at the
  door, so write it for them: *"Provisioning token expired — generate a new
  QR"* beats *"invalid request"*.
- **Bounded latency.** Terminals apply a request timeout
  (`REMOTE_TIMEOUT_SEC`). A slow endpoint is a failed endpoint.

---

## 3. `POST /devices/generate-qr` — mint a provisioning QR

Called by the technician-facing side of your dashboard, not by the terminal.
It mints a one-time token, builds the envelope, signs it, and renders a QR.

### Request

```jsonc
{
  "customer_id": "acme",              // required, 1..64 chars
  "site_id": "hq",                    // required, 1..64 chars
  "door_id": "main-entrance",         // required, 1..64 chars
  "device_mode": "card_and_face",     // see below; default "card_and_face"
  "validity_minutes": 10,             // 0..1440; keep short (see §3.4)
  "network_profile": {                // default {"mode": "local"}
    "mode": "wifi",
    "wifi": { "ssid": "acme-guest", "password": "s3cr3t" }
  }
}
```

`customer_id` / `site_id` / `door_id` are **human-readable names** as chosen in
the dashboard, not numeric primary keys. They are signed into the QR and
persisted on the device.

`device_mode` shall be one of exactly three values in this release:

| Mode | Behaviour at the door |
|---|---|
| `card_only` | Valid card opens the relay. No camera, no face step. |
| `card_and_face` | Valid card starts a session; face verified 1:1 against that cardholder. **Default production mode.** |
| `face_only` | Screen tap starts a 1:N identify against all enrolled users. For doors with no card reader fitted. |

A fourth mode, `time_registry` (working-hours IN/OUT journalling), is specified
but **not yet implemented on the device** — it raises an error at boot. Your
enum shall reject it for now; it will be added when the device half ships,
together with a `face_policy` field (`none` / `verify`).

`network_profile` tells a terminal with no cable how to reach you:

```jsonc
{ "mode": "local" }                                                  // already cabled; no-op
{ "mode": "wifi", "wifi": { "ssid": "...", "password": "..." } }     // join before registering
```

### Response

```jsonc
{
  "token": "kR3v...",                      // the one-time provisioning token
  "nonce": "8f2c...",
  "issued_at": "2026-07-27T14:52:00Z",
  "expires_at": "2026-07-27T15:02:00Z",
  "payload": { /* the full signed envelope, for debugging */ },
  "qr_png": "data:image/png;base64,..."    // drop straight into an <img src>
}
```

### 3.1 The QR envelope

This is the JSON encoded into the QR image. The terminal parses and verifies
exactly these fields.

```jsonc
{
  "schema": "acme.provisioning-qr.v1",
  "command": "provision_device",
  "server_url": "https://access.example.com",
  "customer_id": "acme",
  "site_id": "hq",
  "door_id": "main-entrance",
  "provisioning_token": "kR3v...",
  "issued_at": "2026-07-27T14:52:00Z",
  "expires_at": "2026-07-27T15:02:00Z",
  "nonce": "8f2c...",
  "network_profile": { "mode": "local" },
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "installer-signing-key-2026-01",
    "value": "<base64url of the Ed25519 signature>"
  }
}
```

- `schema` shall be the literal `acme.provisioning-qr.v1`. A mismatch is
  rejected.
- `command` shall be the literal `provision_device`. It is the only command
  honoured; no factory-reset or maintenance command exists, and any other
  value is rejected.
- `server_url` is your public base URL. The terminal registers against it and
  stores it — get this right or the device binds to an unreachable host.
- `device_mode` is deliberately **not** in the QR. See [§3.3](#33-why-devicemode-is-not-in-the-qr).

### 3.2 Signing — the canonical JSON rule

> **This is the single highest-risk part of the integration.** Any deviation
> and every terminal rejects every QR you mint, logged at *error* level as
> suspected forgery. There is no fallback path and no useful error at the
> dashboard end — the failure is silent on your side and total on theirs.

To sign:

1. Build the envelope **without** the `signature` key.
2. Serialise it to canonical JSON: **keys sorted**, **no whitespace**, UTF-8.
   In Python this is exactly:
   ```python
   json.dumps(payload_without_signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
   ```
3. Sign those bytes with Ed25519 (raw Ed25519 over the message — *not*
   pre-hashed, not `Ed25519ph`).
4. Encode the 64-byte signature as **base64url** (RFC 4648 §5, the `-_`
   alphabet), and attach:
   ```jsonc
   "signature": { "algorithm": "Ed25519", "key_id": "<your key id>", "value": "<base64url>" }
   ```

Verification re-derives the same bytes by stripping `signature` again, so the
signature covers every other field including `network_profile`.

Common ways to get this wrong, all of which produce a total failure:

- Unsorted keys, or pretty-printed JSON with spaces or newlines.
- Standard base64 (`+/`) instead of base64url (`-_`).
- Signing the QR *string* after adding a placeholder `signature` field.
- Re-serialising through a library that reorders or reformats nested objects.

The reference signer is `other/qr_code_poc/qr_common.py` (`sign_payload`) and
the device-side verifier is `qr_scanner/qr_scanner.py` (`_verify`). Read them
both once before writing your own.

### 3.3 Why `device_mode` is not in the QR

The mode is authenticated by the one-time provisioning token instead, arriving
in the **registration response** ([§4](#4-post-devicesregister--redeem-the-token)). Two reasons:

- **QR size.** The signed envelope is ~600 characters, which renders at QR
  version 17 with error-correction level L. That is the largest symbol the
  terminal's camera reliably resolves off a phone screen. Adding fields pushes
  the version up and read reliability down.
- **Trust level is identical.** The one-time token is as trustworthy as the
  `device_token` it mints, so nothing is weakened by moving the mode out of
  the signed payload.

Keep the envelope at or below its current field set. If you must add a field,
verify the rendered QR still decodes at version 17 with level L.

### 3.4 The Wi-Fi password is signed but not encrypted

Anyone who photographs the QR can read the Wi-Fi password out of it. This is a
known and accepted risk, mitigated only by keeping `validity_minutes` short —
10 minutes is the intended order of magnitude, not 1440. Do not treat a
provisioning QR as safe to email, print, or leave on a screen.

### 3.5 Rendering the QR

- Error correction level **L**, targeting QR version 17.
- Verify your own output: decode the PNG you just produced before returning
  it, and re-render with a fresh nonce if it fails to decode. The reference
  implementation retries up to 6 times. A QR that a decoder cannot read is
  worse than an error, because the technician discovers it at the door.

---

## 4. `POST /devices/register` — redeem the token

The terminal calls this after verifying a QR offline, and after joining the
Wi-Fi network if the QR carried one. **No bearer auth**: the provisioning
token *is* the credential.

### Sequence diagram

End-to-end registration, from QR mint ([§3](#3-post-devicesgenerate-qr--mint-a-provisioning-qr)) through token
redemption ([§4](#4-post-devicesregister--redeem-the-token)). Numbers in brackets point at the rule that governs the step.

```
   +------------------------------------------------------------------+
   | 1  OPERATOR  -->  SERVER          POST /devices/generate-qr      |
   |                                                                  |
   |    { customer_id, site_id, door_id, device_mode,                 |
   |      network_profile }                                           |
   +------------------------------------------------------------------+
                                     |
                                     v
   +------------------------------------------------------------------+
   | 2  SERVER  (mint + sign)                                         |
   |                                                                  |
   |    - mint one-time provisioning token + fresh nonce              |
   |    - build envelope, sign canonical JSON w/ Ed25519  (3.2)       |
   |    - render QR, self-decode to verify it reads  (3.5)            |
   |                                                                  |
   |    -> { token, nonce, issued_at, expires_at, qr_png }            |
   +------------------------------------------------------------------+
                                     |
                                     v
   +------------------------------------------------------------------+
   | 3  TECHNICIAN  -->  TERMINAL      QR held up to the camera       |
   |                                                                  |
   |    verified OFFLINE - no network call:                           |
   |    - Ed25519 signature vs. public key in trust store             |
   |    - schema + command literals, expires_at                       |
   |    - joins Wi-Fi if network_profile.mode == "wifi"               |
   +------------------------------------------------------------------+
                                     |
                                     v
   +------------------------------------------------------------------+
   | 4  TERMINAL  -->  SERVER          POST /devices/register         |
   |                                   (no bearer auth)               |
   |                                                                  |
   |    { token, nonce, mac, device_type, fw_version,                 |
   |      app_version }        <- all but token may be null           |
   +------------------------------------------------------------------+
                                     |
                                     v
   +------------------------------------------------------------------+
   | 5  SERVER  (redeem)                                              |
   |                                                                  |
   |    - token known? unused? not expired?  (4.2)                    |
   |    - nonce matches the token row?                                |
   |    - burn token + create-or-replace binding, one txn  (4.1)      |
   +------------------------------------------------------------------+
                                     |
                                     v
   +------------------------------------------------------------------+
   | 6  SERVER  -->  TERMINAL          200                            |
   |                                                                  |
   |    { device_id, device_token, heartbeat_interval_sec,            |
   |      customer_id, site_id, door_id, device_mode,                 |
   |      registered_at }                                             |
   |                                                                  |
   |    terminal persists it atomically at 0600, then starts          |
   |    heartbeating every heartbeat_interval_sec                     |
   +------------------------------------------------------------------+
```

On failure at step 5 nothing is bound, and the technician sees why:

```
   +------------------------------------------------------------------+
   | x  SERVER  -->  TERMINAL          404 / 409 / 400                |
   |                                                                  |
   |    { "detail": "Provisioning token already used -- generate      |
   |                 a new QR" }                                      |
   |                                                                  |
   |    no binding is created; detail is shown verbatim to the        |
   |    technician on the kiosk screen                                |
   +------------------------------------------------------------------+
```

### Request

```jsonc
{
  "token": "kR3v...",                     // provisioning_token from the QR (required)
  "nonce": "8f2c...",                     // nonce from the QR; cross-check it
  "mac": "aa:bb:cc:dd:ee:ff",             // may be null
  "device_type": "F455",                  // RealSense model, may be null
  "fw_version": "6.1.0",                  // rsid_py / SDK version, may be null
  "app_version": "face-guard-0.1.0"       // may be null
}
```

Every field except `token` may be absent or `null` — a dev box without the SDK
reports `"unknown"`, and MAC detection can fail. Do not reject on their
absence.

### Response — `200`

```jsonc
{
  "device_id": "3f9a...-uuid",
  "device_token": "<opaque bearer, returned exactly once>",
  "heartbeat_interval_sec": 30,
  "customer_id": "acme",
  "site_id": "hq",
  "door_id": "main-entrance",
  "device_mode": "card_and_face",
  "registered_at": "2026-07-27T15:02:00Z"
}
```

The terminal persists all of this atomically, `0600`, as a credential file.
`device_token` is never retrievable again — store only a hash server-side.

`heartbeat_interval_sec` is **server-authoritative**: whatever you return here
becomes the terminal's heartbeat cadence for the life of the binding. Use it
to manage your own load.

`device_mode` shall be the mode recorded against the redeemed token. If a
legacy token carries none, fall back to `card_and_face` rather than returning
an empty string the terminal would have to interpret.

### Failure responses

The `detail` string is shown to the technician. Make it actionable.

| Status | Condition | Suggested `detail` |
|---|---|---|
| `404` | Token unknown | `Unknown provisioning token` |
| `409` | Token already redeemed | `Provisioning token already used — generate a new QR` |
| `400` | Token expired | `Provisioning token expired — generate a new QR` |
| `400` | `nonce` present but does not match the token row | `Nonce does not match token` |

### 4.1 Re-registration replaces the binding

A terminal being moved to another door is re-provisioned by simply showing it
a new QR. That is the supported field workflow — there is no separate reset
step, and being already bound is **not** an error.

**A new token redeemed by an already-bound terminal shall replace that
terminal's prior binding, not create a second device.** Match on a stable
device identifier (`mac`, or a device identity you already hold) and update
the existing row: new `door_id`, new `device_mode`, new `device_token`. The
old `device_token` shall stop working.

Getting this wrong leaks a stale device row per re-provisioning, each still
holding a valid token and still counted as a device at its old door.

### 4.2 Token expiry is checked twice

The terminal already verified the signed `expires_at` offline before calling
you. Check it again anyway. A terminal could be replaying an old capture, and
the server never delegates that decision — this, plus single-use tokens, is
the *entire* replay defence. Terminals deliberately keep no nonce history:
a replayed QR passes local checks and then fails registration.

---

## 5. `GET /devices/{device_id}/users` — the door's user set

Bearer authenticated. This is the data the terminal authorises from, and the
reason it can run offline.

### Response — `200`

A map keyed by **card/badge id**:

```jsonc
{
  "2325780402": {                          // key = card/badge id, as a STRING
    "user_id": 1,
    "name": "Emma Stone",
    "active": true,
    "permission_level": "employee",
    "faceprints": {
      "version": 9,
      "features_type": 0,
      "flags": 3,
      "adaptive_descriptor_nomask": [ /* exactly 515 ints */ ]
    }
  }
}
```

A top-level `{"users": {...}}` wrapper and a list of records each carrying
`badge_id` are also accepted by the terminal, but **the bare map above is the
canonical shape** — emit that.

### 5.1 Record fields

| Field | Required | Meaning |
|---|---|---|
| *(key)* | yes | Card / badge id, exactly as read from the physical card. Must be a **string**, even when the id is numeric. |
| `user_id` | **yes** | Stable person identifier, treated as **opaque**: any non-null JSON scalar (integer or string). A record without it is **skipped**. This — never the card id — is the subject identifier in all telemetry. |
| `name` | no | Display name, shown on the welcome screen. Defaults to `""`. |
| `active` | no | `false` = retained but never authorising. Defaults to `true`. |
| `permission_level` | no | **Informational only.** The device performs no permission check. Defaults to `"User"`. |
| `faceprints` | **yes** | RealSense ID SDK faceprint object, per [§5.2](#52-the-faceprints-object). A record without a valid one is **skipped**. |

> **Every enrolled person carries a faceprint, in every mode.** `card_only` is
> a property of a *door*, not of a *person* — the people on a site are the same
> people whichever door they use, and they are enrolled once. A `card_only`
> terminal holds ordinary face-carrying records and simply never runs the face
> step. "A user with no faceprints" is not a supported configuration and needs
> no representation on your side.

`faceprints` is a single object (one faceprint per person). Multi-faceprint
re-enrolment is deferred; when it lands it becomes a list.

### 5.2 The `faceprints` object

This is the RealSense ID SDK's own structure. **Pass it through unaltered** —
do not truncate, round, reorder, convert to floats, or re-shape it. Store it as
opaque JSON.

| Key | Type | Required | Legal values |
|---|---|---|---|
| `version` | int | **yes** | Faceprints schema version, currently **9**. Must match the device firmware's version ([§5.3](#53-how-a-malformed-record-fails)). |
| `features_type` | int | **yes** | `0` = W10, `1` = RGB. `0` in practice. |
| `flags` | int | **yes** | SDK operation flags; `3` (`OpFlagEnrollWithoutMask`) in practice. |
| `adaptive_descriptor_nomask` | array of int | **yes** | **Exactly 515** elements. The vector every match is scored against. |

**The 515 length is a hard requirement, not a guideline.** It is 512
recognition features plus 3 extra slots. Element values are signed and must lie
within **±1023**; the SDK's matcher rejects any vector outside that range.

**Index 512 is a flag slot, not a feature.** It carries `2`
(`VecFlagValidWithoutMask`); indices 513 and 514 are zero. A payload that trims
the array to 512 "real" features destroys this slot and breaks matching.

#### Two descriptors you should not send

The SDK structure, older data and the vendor's own samples carry two further
515-element descriptors. **Omit both.** The terminal ignores them if present —
so sending them is harmless, just wasteful — but together they are roughly
**two thirds** of the payload for no information at all.

`adaptive_descriptor_withmask` is **deprecated in the SDK**. It is all zeros in
every real record and never contributes to a match: the matcher consults it
only when the *live* face is masked **and** the stored vector is flagged valid,
which zeros never are. Every match, masked or not, scores against
`adaptive_descriptor_nomask`.

`enroll_descriptor` is not deprecated, merely **unused**. It is the immutable
enrolment vector, and the SDK never reads it on the match path: it serves only
as an anchor for adaptive learning, consulted *after* the score and verdict are
decided. Since the terminal does not implement adaptive learning — it discards
the SDK's updated faceprints — the field has no effect on any decision. It is
also byte-identical to `adaptive_descriptor_nomask` for every user who has
never been re-enrolled, which is every user today.

The terminal sets only the fields matching reads, so it neither requires nor
uses `enroll_descriptor`.

> Should adaptive learning ever be implemented, this field becomes meaningful
> and would be reinstated as a required key in a future revision of this
> contract. Until then, do not send it — a value copied from
> `adaptive_descriptor_nomask` would be a fabricated enrolment vector rather
> than a real one.

### 5.3 How a malformed record fails

Worth understanding, because a bad record does not always fail cleanly:

| Fault | What the terminal does |
|---|---|
| Missing `user_id`, or `faceprints` missing any of its four required keys | **Clean skip.** The record is dropped at sync and `db_sync_invalid_record` is emitted. The rest of the payload is unaffected. This is the good outcome. |
| `adaptive_descriptor_nomask` length ≠ 515 | Passes sync, then fails when that user presents their face — the SDK rejects the array. The failure is reported as a **`hardware_error`** and **blocks all authentication for 20 s**, recurring on every attempt. One bad record degrades the whole door, and the dashboard names the wrong subsystem. |
| Wrong `version` | No error at all. The matcher silently refuses every comparison, so **every user is denied** with nothing in telemetry naming the cause. |

Only the first row is fail-clean. Treat `db_sync_invalid_record` and
`db_sync_skipped_entries` events as build-breakers on your side: they mean the
terminal received records it could not use.

### 5.4 Two invariants you must not break

**(a) Door scoping.** The response shall contain **only** the users authorised
for this terminal's door. This is not an optimisation — it is what makes the
device's authorisation model sound. The terminal treats *presence of a valid,
`active` record in its local cache as the authorisation itself*, with no
further permission or schedule check. If you leak a user from another door,
that user can open this door. Access schedules and per-door permission rules
do not exist on the device; if you need them, enforce them by what you put in
this payload.

**(b) This is a full replacement set, not a delta.** There is no removal list.
A user present in the terminal's cache but **absent from a well-formed
response is dropped locally**, faceprints and all. Consequences:

- Never return a partial set — not on a slow query, not on a partial DB
  failure, not with pagination. A truncated `200` silently de-authorises
  everyone missing from it.
- If you cannot serve the complete set, **fail loudly**: return `5xx`. The
  terminal keeps its previous cache intact and retries. A failed sync never
  clears, expires or invalidates cached users; only a *successful,
  well-formed* response replaces them.

---

## 6. `POST /devices/{device_id}/status` — heartbeat and events

Bearer authenticated. Sent every `heartbeat_interval_sec`. Carries device
state, and piggybacks telemetry events on the same connection — events never
open their own.

### Request

```jsonc
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
      { "event_id": "b2c4...-uuid", "type": "access_granted",
        "ts": "2026-07-27T15:04:11Z", "user_id": "u-8f2c1a", "method": "card" }
    ]
  }
}
```

`metadata` shall be treated as an **open object**. Its keys are diagnostic and
will grow between device releases; store it as JSON and do not validate its
shape or reject unknown keys. `events` is the one key with contractual
meaning — pull it out and handle it per [§6.1](#61-events--acknowledgement-and-idempotency).

### Response — `200`

```jsonc
{ "ok": true, "server_time": "2026-07-27T15:04:12Z" }
```

The terminal ignores the body's contents; only the status code matters. It does
**not** currently read configuration back out of the heartbeat response —
retuning `device_mode` or `heartbeat_interval_sec` mid-binding is explicitly
future work, and a device does not change mode while bound (mode changes mean
revoke → re-bind → restart).

### 6.1 Events — acknowledgement and idempotency

Events are buffered on the device in a bounded in-memory ring (200 entries,
drop-oldest) and are removed **only after a `2xx`**. Therefore:

- **`2xx` means "I have durably accepted every event in that array."** Persist
  them before responding. The terminal drops them immediately afterwards, and
  they are gone.
- **Any non-`2xx` leaves them buffered**, and the terminal resends them on the
  next beat. Never return `2xx` on partial ingestion.
- **Deduplicate by `event_id`** — `INSERT OR IGNORE` on a unique index. A beat
  can be delivered while its response is lost, in which case the terminal
  resends the same `event_id`s. Without dedup you get duplicate door records.

Every event carries `event_id` (uuid4), `type` and `ts` (device-supplied UTC),
plus a small number of type-specific context fields. Record your own
`received_at` alongside, since a device clock can drift.

### 6.2 Event catalogue

Store the `type` as an opaque string. The terminal is permissive at runtime and
this list will grow; an unknown type shall be stored, never rejected.

| Type | Meaning |
|---|---|
| `access_granted` | Door opened. `user_id`, `method` |
| `access_denied` | Denied. `user_id` where known, plus a `reason`: `face_mismatch` (a real denial), `no_faceprints_on_file` or `face_extraction_failed` (usually **your data**, not the person) |
| `access_output_failed` | Approved, but the relay pulse failed |
| `auth_matched` | Biometric match, before the access decision |
| `relay_opened` | Relay actuated |
| `card_unregistered` | Card not in the local set |
| `attendance_event` | IN/OUT registration: `user_id`, `direction`, `ts` — see [§8](#8-attendance--timeregistry-mode-not-yet-active) |
| `device_boot` / `device_shutdown` | Lifecycle |
| `device_revoked` | Sent while still bound, immediately before the terminal destroys its credential |
| `init_mode_entered` | Entered the provisioning scan window |
| `qr_accepted` / `qr_rejected` | Provisioning QR outcome; `qr_rejected` carries a `reason` |
| `db_sync_ok` / `db_sync_failed` | Sync outcome, with counts / `reason` |
| `db_sync_invalid_record` / `db_sync_skipped_entries` | Records you sent that the terminal could not use — **watch these; they mean your payload is malformed** |
| `db_users_revoked` | Users dropped because they were absent from a payload |
| `hardware_error` | Camera / reader / relay fault |
| `storage_low` / `storage_ok` | Free-space threshold crossings |

Access events reference a person by `user_id` only. Card ids and names are
never used as the subject identifier in telemetry.

### 6.3 `410 Gone` — device removal, and what it destroys

**`410` is the single most consequential response in this API. Do not reuse it
for anything else.** Not for an unknown `device_id`, not for a deleted door,
not as a generic "gone" from a framework default.

On receiving `410` from this endpoint the terminal performs an irreversible,
fail-secure teardown, in this order:

1. Emits `device_revoked` and makes one best-effort synchronous flush of its
   buffered events — **while still holding a valid credential**, so the
   transition is auditable on your side. Expect one final heartbeat *after*
   the `410`, and accept its events.
2. Stops heartbeating.
3. Deletes its stored identity, so a restart cannot silently rebind.
4. **Purges the entire local user database, including all faceprints.**
5. **Denies all access from that point on.**
6. Returns to the provisioning scan window in-process, so a technician can
   re-provision it with a new QR without a power cycle.

Revocation is equivalent to a factory reset of the binding, and the biometric
data is not recoverable from the device — you are the master copy, and the
first sync after re-provisioning restores the set.

Implementation guidance: keep the removed device row as a **tombstone** rather
than deleting it, so the next heartbeat can be answered with `410` at all.
Delete a tombstone only after the device has acknowledged (i.e. after you have
answered `410` at least once and, ideally, received its `device_revoked`
event). A device that is powered off at removal time will be told on its next
boot; a device that never comes back keeps its tombstone harmlessly.

---

## 7. Revocation — removing a terminal

Revocation is one of the two product operations on a terminal (the other is
binding). **It is not a device endpoint** — the terminal never calls anything
to be revoked, and never polls to ask. It finds out by being answered `410` on
its next heartbeat.

So what this section mandates is an *effect*, not a URL. The route shape is
yours to choose, since nothing device-facing depends on it. The reference
harness uses `DELETE /devices/{device_id}`, admin-authenticated and idempotent
(`server/main.py`).

### 7.1 The obligation

Provide an operator action that puts a device into a state where **its next
heartbeat is answered `410`**. That single response is the entire mechanism.

### 7.2 The state machine

A revoked device must be **soft-deleted** — kept as a tombstone — because the
row is what lets you answer that heartbeat at all.

| State | Meaning | `POST /status` answers |
|---|---|---|
| `active` | Normal operation | `200` |
| `suspended` | Operator has revoked it; the device has not been told yet | `410`, and flip to `revoked_ack` |
| `revoked_ack` | The device has been told at least once | `410` |
| *(purged)* | Row hard-deleted after acknowledgement | `401` (token unknown) |

Purge `revoked_ack` rows whenever convenient. Never purge a `suspended` one.

### 7.3 Why a hard delete silently defeats revocation

This is the one implementation choice that looks correct and is not.

If you delete the device row the moment the operator clicks *remove*, the
terminal's bearer token resolves to nothing, so its heartbeat comes back `401`
or `404`. The terminal treats that as an ordinary rejected beat: it backs off,
**retries forever, keeps its cached users and faceprints, and keeps opening
the door.** Nothing in the device's teardown path is triggered, because only
`410` triggers it.

Deleting the row is the most natural way to implement "remove a device", and it
produces a terminal that can never be revoked. Keep the tombstone.

### 7.4 Revocation is not instantaneous

It lands on the device's **next heartbeat** — within `heartbeat_interval_sec`
(default 30 s) for a live terminal, and on next boot for one that is powered
off. Consequences:

- A tombstone must be retained **indefinitely** for a device that never comes
  back. It costs one row.
- Your operator UI should distinguish "revoke pending" from "acknowledged", so
  an operator knows whether the terminal has actually wiped itself.
- **`GET /devices/{device_id}/users` should also answer `410`** once a device is
  suspended. Otherwise a revoked terminal can still refresh its full user set
  in the window before its next heartbeat.

### 7.5 What the device does, and what you will see

The six-step teardown is in [§6.3](#63-410-gone--device-removal-and-what-it-destroys).
The one part that concerns your implementation: **expect one final heartbeat
after you answer `410`**, carrying a `device_revoked` event, sent while the
terminal still holds a valid credential. Accept it and ingest its events — it
is the audit record that the revocation actually took effect.

After that the terminal has no identity, no users and no faceprints, and denies
everyone.

### 7.6 Re-provisioning after revocation

A revoked terminal returns to its provisioning scan window, so a technician
re-binds it by showing a new QR. It registers fresh and receives a **new**
`device_id`.

There is no un-revoke. Revocation is equivalent to a factory reset of the
binding; if you want to disable a person rather than a terminal, set their
record `active: false` ([§5.1](#51-record-fields)) instead.

---

## 8. Attendance — `time_registry` mode (not yet active)

Specified for planning only. Neither side implements it yet; nothing here is
required for the current release.

In `time_registry` mode the terminal shows an IN/OUT selection screen instead
of a screensaver. The user picks a direction, then taps a card; the terminal
optionally verifies their face (per-door `face_policy`: `none` or `verify`)
and registers the direction. **No relay is actuated** — this mode is
attendance-only.

Server obligations when it lands:

- **API-ATT-01** Accept `attendance_event` events (`user_id`, `direction` of
  `in`/`out`, `ts`) on the normal heartbeat channel, with the same
  `event_id` idempotency.
- **API-ATT-02** Persist them and expose a per-person working-hours journal.
- **API-ATT-03** Accept `time_registry` as a provisionable `device_mode`, and
  carry a `face_policy` field alongside it from generate-QR through to the
  registration response.

Note the differing durability expectation: ordinary telemetry may be lost if
the device restarts during an outage, but **a lost check-in is a payroll
error**. The device will queue attendance events on disk. Treat them as
records, not telemetry.

