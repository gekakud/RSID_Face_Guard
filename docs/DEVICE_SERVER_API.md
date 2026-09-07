# RSID Face Guard — Device ↔ Server API Contract

| Item | Detail |
|---|---|
| Document ID | API-FG-001 |
| Revision | 1.1 (2026-09-07) |
| Audience | Server / dashboard developer |
| Scope | **Only** the endpoints the terminal and the technician app call |
| Companion | [SOFTWARE_REQUIREMENTS.md](SOFTWARE_REQUIREMENTS.md) — device-side spec, not required reading |

## 0. How to read this document

This is the complete contract between the RSID Face Guard terminal and the
dashboard server. It is self-contained: implementing exactly what is written
here is sufficient for a terminal to provision, sync users, open doors and
report telemetry.

Everything *else* about the dashboard — customers, sites, doors, operator
accounts, user enrolment UI, reporting, the database schema — is out of scope
here and entirely yours. The reference implementation in `server/` is a
throwaway test harness used to develop the device; **do not treat it as a
design to copy.** Where its behaviour and this document disagree, this
document wins, and §10 lists the known divergences.

**Shall** = mandatory. **Should** = recommended.

---

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
    the terminal. See §6.3.
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
- `device_mode` is deliberately **not** in the QR. See §3.3.

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
in the **registration response** (§4). Two reasons:

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

### 3.6 Requirements

- **API-QR-01** The envelope shall be Ed25519-signed per §3.2, with a short
  validity window and a fresh single-use nonce per mint.
- **API-QR-02** `command` shall be `provision_device`; no other command shall
  be minted.
- **API-QR-03** The `key_id` shall identify a key whose public half is
  deployed to the target terminals' trust stores.
- **API-QR-04** `device_mode` shall be persisted against the minted token and
  carried through to the registration response.
- **API-QR-05** `time_registry` shall be rejected as a `device_mode` until the
  device half ships.

---

## 4. `POST /devices/register` — redeem the token

The terminal calls this after verifying a QR offline, and after joining the
Wi-Fi network if the QR carried one. **No bearer auth**: the provisioning
token *is* the credential.

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
holding a valid token and still counted as a device at its old door. *(The
reference server currently gets this wrong — see §10.)*

### 4.2 Token expiry is checked twice

The terminal already verified the signed `expires_at` offline before calling
you. Check it again anyway. A terminal could be replaying an old capture, and
the server never delegates that decision — this, plus single-use tokens, is
the *entire* replay defence. Terminals deliberately keep no nonce history:
a replayed QR passes local checks and then fails registration.

### 4.3 Requirements

- **API-REG-01** The provisioning token shall be single-use. Burn it in the
  same transaction that creates or updates the device, so a crash between the
  two cannot leave a redeemable token pointing at a live device.
- **API-REG-02** Token expiry shall be re-checked server-side.
- **API-REG-03** If `nonce` is supplied it shall be cross-checked against the
  token record.
- **API-REG-04** Re-registration shall replace the prior binding and
  invalidate the previous `device_token` (§4.1).
- **API-REG-05** `device_token` shall be stored hashed, never in plaintext,
  and never logged.
- **API-REG-06** Failure responses shall carry a technician-actionable
  `detail`.

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
      "adaptive_descriptor_nomask": [ /* exactly 515 ints */ ],
      "enroll_descriptor":          [ /* exactly 515 ints */ ]
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
| `faceprints` | **yes** | RealSense ID SDK faceprint object, per §5.2. A record without a valid one is **skipped**. |

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
| `version` | int | **yes** | Faceprints schema version, currently **9**. Must match the device firmware's version (§5.3). |
| `features_type` | int | **yes** | `0` = W10, `1` = RGB. `0` in practice. |
| `flags` | int | **yes** | SDK operation flags; `3` (`OpFlagEnrollWithoutMask`) in practice. |
| `adaptive_descriptor_nomask` | array of int | **yes** | **Exactly 515** elements. The vector every match is scored against. |
| `enroll_descriptor` | array of int | **yes** | **Exactly 515** elements. The immutable enrolment vector. |

**The 515 length is a hard requirement, not a guideline.** It is 512
recognition features plus 3 extra slots. Element values are signed and must lie
within **±1023**; the SDK's matcher rejects any vector outside that range.

**Index 512 is a flag slot, not a feature.** It carries `2`
(`VecFlagValidWithoutMask`) in both descriptors; indices 513 and 514 are zero.
A payload that trims the arrays to 512 "real" features destroys this slot and
breaks matching.

#### `adaptive_descriptor_withmask` — do not send

Older data and the vendor's own samples carry a third descriptor,
`adaptive_descriptor_withmask`. **Do not include it.** It is deprecated in the
SDK, it is all zeros in every real record, and it never contributes to a match:
the matcher only consults it when the *live* face is masked **and** the stored
vector is flagged valid, which zeros never are — so every match, masked or not,
scores against `adaptive_descriptor_nomask`.

The terminal ignores the key if it is present, so sending it is harmless but
pure waste — roughly **18%** of the payload for no information at all.

### 5.3 How a malformed record fails

Worth understanding, because a bad record does not always fail cleanly:

| Fault | What the terminal does |
|---|---|
| Missing `user_id`, or `faceprints` missing one of `version` / `features_type` / `flags` / `adaptive_descriptor_nomask` | **Clean skip.** The record is dropped at sync and `db_sync_invalid_record` is emitted. The rest of the payload is unaffected. This is the good outcome. |
| Missing `enroll_descriptor` | Passes sync, then fails when that user presents their face. The failure is reported as a **`hardware_error`** and **blocks all authentication for 20 s**, recurring on every attempt. One bad record degrades the whole door, and the dashboard names the wrong subsystem. |
| Descriptor length ≠ 515 | Identical to the above — the SDK setter rejects the array at match time. |
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

### 5.5 Requirements

- **API-USR-01** The payload shall be door-scoped; a terminal shall never
  receive users from another door.
- **API-USR-02** The payload shall be the complete authorised set for that
  door, or an error. Partial success shall be reported as `5xx`.
- **API-USR-03** `user_id` and a valid `faceprints` object shall be present on
  every record.
- **API-USR-06** Every faceprint descriptor shall be an array of **exactly
  515** integers within ±1023, with all five keys of §5.2 present.
- **API-USR-07** `adaptive_descriptor_withmask` shall not be sent.
- **API-USR-04** The bearer token shall be checked to belong to the
  `device_id` in the path (`403` otherwise), so one terminal cannot read
  another's user set.
- **API-USR-05** Faceprints are biometric data. They shall be transported over
  HTTPS only and shall never be written to ordinary logs.

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
meaning — pull it out and handle it per §6.1.

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
| `attendance_event` | IN/OUT registration: `user_id`, `direction`, `ts` — see §8 |
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

### 6.4 Requirements

- **API-HB-01** `2xx` shall be returned only after every listed event is
  durably persisted.
- **API-HB-02** Events shall be deduplicated by `event_id` (insert-or-ignore).
- **API-HB-03** Unknown event types and unknown `metadata` keys shall be
  stored, never rejected.
- **API-HB-04** `410` shall be returned **only** to mean "this device has been
  removed", and the device row shall be retained as a tombstone so the
  response can be delivered.
- **API-HB-05** The heartbeat that follows a `410` shall be accepted and its
  events ingested.
- **API-HB-06** The bearer token shall be checked to belong to the path
  `device_id` (`403` otherwise).
- **API-HB-07** Transient server-side failures shall be reported as `5xx`, not
  `4xx` — terminals treat `4xx` as permanent.
- **API-HB-08** Status history should be bounded. A terminal beating every
  30 s produces ~2,900 rows per day, indefinitely.

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
  in the window before its next heartbeat. The harness does not do this — see
  [§10](#10-known-divergences-in-the-reference-server).

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

### 7.7 Requirements

- **API-REV-01** An operator-facing revoke action shall exist, and shall cause
  the device's next heartbeat to be answered `410`.
- **API-REV-02** Revocation shall be a **soft delete**: the device row shall be
  retained as a tombstone so the `410` can be delivered.
- **API-REV-03** `POST /devices/{device_id}/status` shall answer `410` for a
  revoked device, in both the pending and acknowledged states.
- **API-REV-04** `GET /devices/{device_id}/users` should answer `410` for a
  revoked device.
- **API-REV-05** The revoke action shall be idempotent — revoking twice shall
  not error, and shall not resurrect an acknowledged removal.
- **API-REV-06** A tombstone shall be purged only after the device has
  acknowledged, and shall be retained indefinitely otherwise.

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

---

## 9. Security requirements

- **API-SEC-01** The Ed25519 **private** key shall exist only on the server.
  No signing key shall ever reach a terminal.
- **API-SEC-02** `device_token` shall be stored hashed and never logged.
  Provisioning tokens likewise.
- **API-SEC-03** Wi-Fi passwords in QR payloads shall never be written to
  logs, and QR validity windows shall be kept short (§3.4).
- **API-SEC-04** Faceprints shall be transported over HTTPS only and never
  logged.
- **API-SEC-05** Bearer tokens shall be scoped to their own `device_id`
  (§5.4a, API-USR-04, API-HB-06).
- **API-SEC-06** Door-scoped data minimisation shall be enforced server-side
  (§5.4a). The device performs no permission check of its own.
- **API-SEC-07** Production shall use HTTPS with certificate validation.
- **API-SEC-08** `key_id` shall support more than one trusted key
  concurrently, so keys can be rotated by deploying a new public key to
  terminals before switching the signer.

---

## 10. Known divergences in the reference server

`server/` is a development harness. These are its deliberate or known-wrong
behaviours — do **not** reproduce them.

| Area | Reference server does | You shall do |
|---|---|---|
| Re-registration (§4.1) | Mints a new `device_id` on every redemption, leaving the old binding live | Replace the existing binding and invalidate its token |
| User seeding | Seeds every new device from one shared default user template | Assign the door's real user set |
| User payload | Returns the bare badge-keyed map (correct), with no per-record validation | Same shape, but validate `user_id` + `faceprints` before serving |
| Revoked device sync (§7.4) | `GET /devices/{id}/users` does not check device state, so a revoked terminal can still refresh its user set until its next heartbeat | Answer `410` on the users endpoint too |
| `device_mode` | Falls back to `card_and_face` for legacy tokens with `NULL` mode | Keep this fallback |
| Auth | Single shared admin credential for all operator routes | Real operator authn/authz |
| Storage | SQLite, whole-file user replacement, no migrations | Your own schema |
| Attendance (§8) | No intake | Implement when the device half ships |

The harness *is* a useful reference for two things: the canonical-JSON signer
(`other/qr_code_poc/qr_common.py`) and the `410` tombstone lifecycle
(`server/main.py`, `post_status`). Read those; ignore the rest.

---

## 11. Checklist

Provisioning
- [ ] Ed25519 private key held server-side only; public key deployed to terminals
- [ ] Canonical JSON exactly as §3.2 — sorted keys, no whitespace, base64url
- [ ] Envelope fields exactly as §3.1; `schema` and `command` literals correct
- [ ] `server_url` is the reachable public base URL
- [ ] QR renders at version 17 / level L and self-decodes before returning
- [ ] Short `validity_minutes`
- [ ] `device_mode` persisted against the token; `time_registry` rejected

Registration
- [ ] Provisioning token single-use, burned in the same transaction
- [ ] Expiry re-checked server-side; `nonce` cross-checked
- [ ] Re-registration **replaces** the binding and invalidates the old token
- [ ] `device_token` stored hashed, returned exactly once
- [ ] `heartbeat_interval_sec` returned
- [ ] Failure `detail` strings are technician-actionable

Users
- [ ] Door-scoped — no cross-door leakage
- [ ] Complete set or `5xx`; never a partial `200`
- [ ] `user_id` + valid `faceprints` on every record
- [ ] Every descriptor exactly **515** ints within ±1023
- [ ] `adaptive_descriptor_withmask` **not** sent
- [ ] Bearer token scoped to the path `device_id`

Revocation
- [ ] Operator revoke action exists and is idempotent
- [ ] **Soft** delete — tombstone retained, never hard-deleted while pending
- [ ] `410` on `POST /status` for a revoked device
- [ ] `410` on `GET /users` for a revoked device
- [ ] Final post-`410` heartbeat accepted, `device_revoked` ingested
- [ ] Tombstone purged only after acknowledgement

Heartbeat
- [ ] `2xx` only after events are durably persisted
- [ ] `event_id` unique index, insert-or-ignore
- [ ] `metadata` stored as opaque JSON; unknown event types accepted
- [ ] `410` used **only** for device removal, with a retained tombstone
- [ ] Post-`410` heartbeat accepted and its events ingested
- [ ] Transient failures are `5xx`, not `4xx`
- [ ] Status history bounded

---

## 12. Revision history

| Rev | Date | Summary |
|---|---|---|
| 1.0 | 2026-09-07 | Initial contract, extracted from SRS-FG-001 rev 1.8 and reconciled against the working tree |
| 1.1 | 2026-09-07 | §5 rewritten against real data: corrected `version` (9), `flags` (3) and `user_id` (opaque scalar), documented the **515**-element descriptor requirement and the ±1023 range, and dropped `adaptive_descriptor_withmask` — deprecated, all-zero and never scored, now removed device-side too. Added §5.3 (how malformed records fail) and **§7 Revocation**, which was previously absent: the state machine, why a hard delete defeats revocation, and `410` on the users endpoint. Sections renumbered from §7 onward |
