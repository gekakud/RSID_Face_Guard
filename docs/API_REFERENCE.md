# Device ↔ Server API — Reference

Terminal ↔ backend only. Details and rules: [DEVICE_SERVER_API.md](DEVICE_SERVER_API.md).

Timestamps: UTC, `YYYY-MM-DDTHH:MM:SSZ`. Errors: JSON `{"detail": "..."}`.
Bearer auth: `Authorization: Bearer <device_token>`. All calls are made by the terminal.

| # | Endpoint | Auth | When |
|---|---|---|---|
| 1 | `POST /installation/pair` | none | once per QR scan |
| 2 | `GET /devices/{device_id}/users` | Bearer | start-up, after pairing, every 60 s |
| 3 | `POST /devices/{device_id}/status` | Bearer | every `heartbeat_interval_sec` |

## QR content (backend creates it, terminal scans it)

| Field | Required | Notes |
|---|---|---|
| `installationId` | yes | string |
| `deviceType` | yes | string |
| `serverLink` | yes | backend base URL, http(s), no credentials, query or fragment |
| `networkProfile` | no | `{"mode":"wifi","wifi":{"ssid","password"}}`. Left out for local. |

## 1. POST /installation/pair

Request:

| Field | Notes |
|---|---|
| `installationId` | from the QR |
| `mac` | `xx:xx:xx:xx:xx:xx` |
| `serial` | Pi board serial (16 hex), or `dev-<name>` |

Response `200`:

| Field | Notes |
|---|---|
| `_id` | must equal `installationId` |
| `status` | must be `paired` |
| `device` | device ID (required), used in the URLs below |
| `device_token` | bearer token |
| `heartbeat_interval_sec` | positive integer (the terminal runs `int()` on it; null or non-numeric gives "Invalid pair response"). Default on the terminal: 10. |
| `device_mode` | `card_only`, `card_and_face`, `face_only` or `time_registry` |
| `venue` | stored as `site_id` |
| `deviceType` | stored as `device_type` (falls back to the QR value) |
| `timeStamp` | informational, unused |
| `customer`, `gate` | objects, the terminal reads `_id` |

Errors: any non-2xx is final for that scan. The terminal shows `detail`. Reference backend: `404` unknown installation, `409` not active or other hardware, `422` bad fields.

## 2. GET /devices/{device_id}/users

Response `200`: object keyed by card/badge ID.

| Field | Required | Notes |
|---|---|---|
| `user_id` | yes | int or string, not null |
| `faceprints` | yes | see below |
| `name` | no | default `""` |
| `active` | no | JSON boolean, default `true`. Do not send strings: `"false"` is read as true. |
| `permission_level` | no | default `"User"`, informational |

`faceprints`: `version` (9), `features_type` (0), `flags` (3), `adaptive_descriptor_nomask` (515 ints, ±1023).

The list replaces the local one. An empty or fully invalid list changes nothing. Any non-`200` is a failed sync and changes nothing.

## 3. POST /devices/{device_id}/status

Request:

| Field | Notes |
|---|---|
| `status` | `online`, or `shutting_down` |
| `metadata` | open object |
| `metadata.events` | array of events |

Common `metadata` keys: `app_version`, `device_type`, `serial_port`, `user_count`, `camera_available`, `relay_available`, `session_active`, `init_mode_active`, `auth_in_progress`, `storage`.

Event: `event_id` (uuid4), `type`, `ts`, plus the fields of its type.

Response `200`: `{"ok": true, "server_time": "..."}`. The terminal reads only the status code.

| Answer | Terminal does |
|---|---|
| any status below 400 | deletes the sent events. Redirects are followed, so answer `2xx` at the final URL. |
| `410` | device was removed: wipes identity, users and faceprints, waits for a new QR |
| anything else | keeps the events, retries with backoff up to 300 s |

Removal: the backend answers `410` to this call for a removed device. The terminal calls no remove endpoint.

## Event types

| Type | Fields |
|---|---|
| `access_granted` | `user_id`, `method` |
| `access_denied` | `reason`, `user_id`?, `method`?, `status`? (`face_extraction_failed`) |
| `access_output_failed` | `user_id`, `method` |
| `auth_matched` | `user_id`, `method`, `score` |
| `relay_opened` | `seconds` |
| `card_unregistered` | |
| `attendance_event` | `user_id`, `direction`, `method` |
| `device_boot` | `app_version`, `ui` |
| `device_shutdown`, `device_revoked` | |
| `init_mode_entered` | |
| `qr_accepted` | `installation_id` |
| `qr_rejected` | |
| `db_sync_ok` | `users`, `skipped` |
| `db_sync_failed` | `reason`, `status`?, `error`? |
| `db_sync_invalid_record` | `badge_id` |
| `db_sync_skipped_entries` | `count` |
| `db_users_revoked` | `count` |
| `heartbeat_post_failed` | `consecutive_failures`, `server_url` |
| `hardware_error` | `where`?, `error`? |
| `storage_low`, `storage_ok` | `path`, `free_mb`, `min_free_mb`? (`storage_low` only) |

`access_denied` reasons: `card_unregistered`, `user_inactive`, `no_match`, `face_mismatch`, `no_faceprints_on_file`, `face_extraction_failed`.
