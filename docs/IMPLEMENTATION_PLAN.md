# RSID Face Guard — Implementation Plan

**Code reconciliation and ordered task list**

| Item | Detail |
|---|---|
| Document ID | PLAN-FG-001 |
| Revision | 1.2 |
| Date | 2026-08-31 |
| Specification | [`SOFTWARE_REQUIREMENTS.md`](SOFTWARE_REQUIREMENTS.md) rev 1.4 |
| Basis | Static audit of the working tree. Evidence cites **file + symbol**, never line numbers — rev 1.1 used `file:line` and every reference rotted the moment [T1](#delivered) moved the session machine |
| Scope | Device application **and** the reference server (`server/`) |
| Delivery model | Small batches (B0..B14, [§3](#3-batches-and-device-validation)); each batch is device-validated by the owner before the next starts |
| Front-end | Web UI only. `gui_qt/` and `main_qt.py` were **deleted** on 2026-08-31 (rev 1.1 called them "frozen"). PySide6/QtWebEngine remains the runtime host of the web UI — the *widgets* front-end is what went away |

**How to read this.** [§1](#1-reconciliation-table) states, per requirement, what
the code does *today*. [§2](#2-task-list) is the live queue of remaining work,
plus a [Delivered](#delivered) roll-up of what has shipped.
[§3](#3-batches-and-device-validation) is the batch schedule and per-batch
device-validation checklists.

Status legend: **✅ IMPLEMENTED** · **⚠️ PARTIAL** · **❌ MISSING** ·
**➖ DEPRECATED** (withdrawn in SRS rev 1.2, no work required).

---

## 1. Reconciliation table

### 1.1 Summary

| Area | ✅ | ⚠️ | ❌ | ➖ |
|---|---|---|---|---|
| FR-STATE (12) | 8 | 0 | 0 | 4 *(06/08/10/12)* |
| FR-MODE (11) | 3 | 0 | 8 | 0 |
| FR-SESS (8) | 7 | 1 | 0 | 0 |
| FR-FACE (7) | 6 | 1 | 0 | 0 |
| FR-CARD (6) | 5 | 1 | 0 | 0 |
| FR-OUT (6) | 6 | 0 | 0 | 0 |
| FR-DB (8) | 7 | 1 | 0 | 0 |
| FR-PROV (11) | 10 | 1 | 0 | 0 |
| FR-NET (5) | 4 | 1 | 0 | 0 |
| FR-HB (10) | 10 | 0 | 0 | 0 |
| FR-LOG (5) | 4 | 1 | 0 | 0 |
| FR-CAM (4) | 4 | 0 | 0 | 0 |
| FR-UI (12) | 7 | 2 | 1 | 2 *(02/10)* |
| FR-API (15) | 9 | 4 | 1 | 1 *(12)* |
| FR-DATA (7) | 6 | 1 | 0 | 0 |
| BR (7) | 6 | 1 | 0 | 0 |
| NFR (22) | 21 | 1 | 0 | 0 |
| **Total (156)** | **123** | **16** | **10** | **7** |

Active requirements: 149. **Compliance today: 123/149 = 83 %** (rev 1.1 stated
72 %, against a summary table whose FR-STATE row summed to 10 of 12 and whose
FR-DATA row contradicted its own detail section; both are corrected here).

### 1.2 Operating states — FR-STATE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-STATE-01](SOFTWARE_REQUIREMENTS.md#fr-state-01) | ✅ | `provisioning/binding.py` `start_if_bound()` resumes from the persisted identity |
| [FR-STATE-02](SOFTWARE_REQUIREMENTS.md#fr-state-02) | ✅ | Revocation resets to init mode in-process — no identity + init active = deny-all (`binding.py` `_handle_revoked()`); see [FR-HB-10](#fr-hb-10-row) |
| [FR-STATE-03](SOFTWARE_REQUIREMENTS.md#fr-state-03) | ✅ | `session/controller.py` guards on `session_active` / `auth_in_progress` |
| [FR-STATE-04](SOFTWARE_REQUIREMENTS.md#fr-state-04) | ✅ | Full flow runs from the local cache; no server call on the door path |
| [FR-STATE-05](SOFTWARE_REQUIREMENTS.md#fr-state-05) | ✅ | `db/user_database.py` `get_user()` reads the in-memory cache |
| FR-STATE-06 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-07](SOFTWARE_REQUIREMENTS.md#fr-state-07) | ✅ | Buffered in the ring, drained on ack (`provisioning/heartbeat.py` `_run()`) |
| FR-STATE-08 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-09](SOFTWARE_REQUIREMENTS.md#fr-state-09) | ✅ | `db/user_database.py` `start_auto_sync()` background loop |
| FR-STATE-10 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-11](SOFTWARE_REQUIREMENTS.md#fr-state-11) | ✅ | Only 410 raises `DeviceRevokedError` (`provisioning/client.py` `post_status()`) |
| FR-STATE-12 | ➖ | Deprecated rev 1.2 |

### 1.3 Operating modes — FR-MODE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-MODE-01](SOFTWARE_REQUIREMENTS.md#fr-mode-01) | ❌ | `device_mode` / `face_policy` have **zero hits** across `config.py`, `provisioning/`, `session/` and `server/`; absent from `server/models.py` `RegisterResponse` and from `provisioning/identity.py` `DeviceIdentity`. Mode is still the local boolean `config.AUTH_ONLY_ON_CARD` → **[T4](#t4)** |
| [FR-MODE-02](SOFTWARE_REQUIREMENTS.md#fr-mode-02) | ✅ | `face_auth/auth_service.py` `card_is_registered()` — DB-only check, no camera |
| [FR-MODE-03](SOFTWARE_REQUIREMENTS.md#fr-mode-03) | ❌ | No `card_only`; `controller.py` `on_card_detected()` always starts a session → **[T7](#t7)** |
| [FR-MODE-04](SOFTWARE_REQUIREMENTS.md#fr-mode-04) | ✅ | `auth_service.py` `authenticate_with_card_and_face()` — 1:1 against the cardholder |
| [FR-MODE-05](SOFTWARE_REQUIREMENTS.md#fr-mode-05) | ✅ | `auth_service.py` `authenticate_face_only()`, gated by `AUTH_ONLY_ON_CARD` via `controller.py` `on_user_tapped()` |
| [FR-MODE-06](SOFTWARE_REQUIREMENTS.md#fr-mode-06) | ❌ | No IN/OUT screen or latch. The `demo_ui/app.js` `setAttendanceMode()` toggle is cosmetic — never read by Python → **[T8](#t8)** |
| [FR-MODE-07](SOFTWARE_REQUIREMENTS.md#fr-mode-07) | ❌ | No `attendance_event` emission anywhere → **[T8](#t8)** |
| [FR-MODE-08](SOFTWARE_REQUIREMENTS.md#fr-mode-08) | ❌ | No relay-suppressed mode → **[T8](#t8)** |
| [FR-MODE-09](SOFTWARE_REQUIREMENTS.md#fr-mode-09) | ❌ | Depends on T8 → **[T8](#t8)** |
| [FR-MODE-10](SOFTWARE_REQUIREMENTS.md#fr-mode-10) | ❌ | No durable disk queue — `observability/durable_queue.py` does not exist; only the bounded `deque` in `observability/events.py` → **[T5b](#t5b)** |
| [FR-MODE-11](SOFTWARE_REQUIREMENTS.md#fr-mode-11) | ❌ | Pointer to [FR-API-15](#fr-api-15-row) → **[T8](#t8)** |

### 1.4 Session orchestration — FR-SESS

All session logic now lives in `session/controller.py`; `gui_web/web_window.py`
holds only the `WebSessionView` adapter, `QtScheduler` and platform glue.

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-SESS-01](SOFTWARE_REQUIREMENTS.md#fr-sess-01) | ✅ | `gui_web/frame_server.py` `WebServer` + `CameraStreamer` serve page and MJPEG from one loopback origin |
| [FR-SESS-02](SOFTWARE_REQUIREMENTS.md#fr-sess-02) | ✅ | Preview paused at construction (`GUIWeb.__init__`), resumed per session in `controller.start_session()` |
| [FR-SESS-03](SOFTWARE_REQUIREMENTS.md#fr-sess-03) | ⚠️ | Two gaps. (a) No different-card pre-emption: the card flag clears in `controller._end_session()`, which the result hold schedules, so reads stay suppressed through the hold. (b) `on_card_detected()` → `start_session()` guards only on `_session_active` / `_is_page_ready()` — the **init-mode guard present on `on_user_tapped()` and `on_card_rejected()` is missing on the registered-card path**, so a card tap during init mode starts a session → **[T9](#t9)** |
| [FR-SESS-04](SOFTWARE_REQUIREMENTS.md#fr-sess-04) | ✅ | `controller.py` retry/timeout handles; card session ends on first mismatch in `_on_auth_complete()` (BR-05) |
| [FR-SESS-05](SOFTWARE_REQUIREMENTS.md#fr-sess-05) | ✅ | `controller._authenticate()` skips a tick while `_auth_in_progress` |
| [FR-SESS-06](SOFTWARE_REQUIREMENTS.md#fr-sess-06) | ✅ | `_cancel_session_timers()` runs before every result render in `_on_auth_complete()` |
| [FR-SESS-07](SOFTWARE_REQUIREMENTS.md#fr-sess-07) | ✅ | `_run_authentication()` on a worker thread → `QtScheduler.post_to_ui()` → `_SignalBridge` |
| [FR-SESS-08](SOFTWARE_REQUIREMENTS.md#fr-sess-08) | ✅ | `controller._end_session()` pauses preview, clears the card flag, returns to idle |

### 1.5 Face authentication — FR-FACE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-FACE-01](SOFTWARE_REQUIREMENTS.md#fr-face-01) | ✅ | `auth_service.AuthService.__init__` connects; 1:1 `authenticate_with_card_and_face()`, 1:N `authenticate_face_only()` |
| [FR-FACE-02](SOFTWARE_REQUIREMENTS.md#fr-face-02) | ✅ | `authenticate_with_card_and_face()` matches only the cardholder's stored faceprints |
| [FR-FACE-03](SOFTWARE_REQUIREMENTS.md#fr-face-03) | ✅ | **T12 done.** One decision line per path: `1:1 decision: card=… sdk_success=… score=… threshold=… -> GRANT/DENY`, and the 1:N equivalent |
| [FR-FACE-04](SOFTWARE_REQUIREMENTS.md#fr-face-04) | ✅ | **T2 done.** `auth_service.py` emits `auth_matched` only and imports just `disconnect_relay`; `controller._open_access_point()` actuates |
| [FR-FACE-05](SOFTWARE_REQUIREMENTS.md#fr-face-05) | ✅ | Distinct denial reasons: `face_extraction_failed`, `no_faceprints_on_file`, `face_mismatch`, `no_match`, `user_inactive` |
| [FR-FACE-06](SOFTWARE_REQUIREMENTS.md#fr-face-06) | ⚠️ | Backoff + background reconnect + `hardware_error` all exist (`_reconnect()`, `_error_backoff_until`), but the **gate sits inside `authenticate_face_only()` only** — `authenticate_with_card_and_face()` has none, and nothing surfaces the condition to the UI → **[T9](#t9)** |
| [FR-FACE-07](SOFTWARE_REQUIREMENTS.md#fr-face-07) | ✅ | Every exception path returns a deny tuple |

### 1.6 Card reader — FR-CARD

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-CARD-01](SOFTWARE_REQUIREMENTS.md#fr-card-01) | ✅ | `hardware/card_reader_api.py` selects the backend from `config.CARD_READER_BACKEND` |
| [FR-CARD-02](SOFTWARE_REQUIREMENTS.md#fr-card-02) | ✅ | `auth_service.start_card_monitoring()` daemon loop; never touches the UI thread |
| [FR-CARD-03](SOFTWARE_REQUIREMENTS.md#fr-card-03) | ✅ | 2 s per-card cooldown inside the monitor loop |
| [FR-CARD-04](SOFTWARE_REQUIREMENTS.md#fr-card-04) | ⚠️ | ✅ *as coded*, but the SRS requires reads to resume **during** the result hold so a different card can pre-empt it. `mark_card_session_done()` is only called from `controller._end_session()`, which the hold schedules → **[T9](#t9)**, with [FR-SESS-03](SOFTWARE_REQUIREMENTS.md#fr-sess-03). *(Rev 1.1 marked this ✅ while documenting the same gap two lines later.)* |
| [FR-CARD-05](SOFTWARE_REQUIREMENTS.md#fr-card-05) | ✅ | `card_is_registered()` gate; separate `on_card_detected` / `on_card_rejected` callbacks |
| [FR-CARD-06](SOFTWARE_REQUIREMENTS.md#fr-card-06) | ✅ | Monitor loop logs, emits `hardware_error` (`where="card_monitor"`), sleeps and continues |

### 1.7 Access output — FR-OUT

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-OUT-01](SOFTWARE_REQUIREMENTS.md#fr-out-01) | ✅ | `config.RELAY_*`; `hardware/relay_api.RelayController.initialize()` |
| [FR-OUT-02](SOFTWARE_REQUIREMENTS.md#fr-out-02) | ✅ | `controller._open_access_point()` pulses off the UI thread; `open_door(seconds=3.0)` default |
| [FR-OUT-03](SOFTWARE_REQUIREMENTS.md#fr-out-03) | ✅ | `relay_api.RelayController.open_door()` emits `relay_opened` |
| [FR-OUT-04](SOFTWARE_REQUIREMENTS.md#fr-out-04) | ✅ | Wiegand-tx init failure non-fatal in `AuthService.__init__` |
| [FR-OUT-05](SOFTWARE_REQUIREMENTS.md#fr-out-05) | ✅ | `RelayController.initialize()` degrades gracefully when lgpio/pin is unavailable |
| [FR-OUT-06](SOFTWARE_REQUIREMENTS.md#fr-out-06) | ✅ | **T2 done.** `access_granted` only after a successful pulse; a failed/raising pulse yields `access_output_failed` (`controller._on_auth_complete()`) |

### 1.8 User DB & sync — FR-DB

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-DB-01](SOFTWARE_REQUIREMENTS.md#fr-db-01) | ⚠️ | Atomic write ✅ (`db/local_provider.py` `save_all()`); schema v2 ✅ (`remote_provider._add_if_valid()` requires `user_id`, normalises `active`). Remaining gap: `faceprints` is still a **dict, not a list** → **[T3b](#t3b)** |
| [FR-DB-02](SOFTWARE_REQUIREMENTS.md#fr-db-02) | ✅ | Local membership authorises; `permission_level` never gates |
| [FR-DB-03](SOFTWARE_REQUIREMENTS.md#fr-db-03) | ✅ | `user_database.start_auto_sync()` on `DB_SYNC_INTERVAL_SEC` |
| [FR-DB-04](SOFTWARE_REQUIREMENTS.md#fr-db-04) | ✅ | `get_user()` / `get_all_users()` read the cache only |
| [FR-DB-05](SOFTWARE_REQUIREMENTS.md#fr-db-05) | ✅ | `remote_provider.load_all()` + `_parse_users()`; a failed fetch is a no-op in `sync_from_remote()` |
| [FR-DB-06](SOFTWARE_REQUIREMENTS.md#fr-db-06) | ✅ | All four events emitted from `db/remote_provider.py` |
| [FR-DB-07](SOFTWARE_REQUIREMENTS.md#fr-db-07) | ✅ | Skipped while unbound, and **re-armed on bind** via `attach_remote()` / `AuthService.enable_remote_sync()` — see [Delivered #12](#delivered) |
| [FR-DB-08](SOFTWARE_REQUIREMENTS.md#fr-db-08) | ✅ | `sync_from_remote()` full replace + `db_users_revoked` |

### 1.9 Provisioning & QR trust — FR-PROV

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-PROV-01](SOFTWARE_REQUIREMENTS.md#fr-prov-01) | ✅ | **T16 done.** `controller.start_init_mode()` always enters and emits `init_mode_entered`, then either runs the scan window or ends at delay 0; called unconditionally from `GUIWeb._on_load_finished()`. `INIT_MODE_ENABLED` sizes the window only |
| [FR-PROV-02](SOFTWARE_REQUIREMENTS.md#fr-prov-02) | ✅ | Envelope schema documented and parsed in `qr_scanner/qr_scanner.py` |
| [FR-PROV-03](SOFTWARE_REQUIREMENTS.md#fr-prov-03) | ⚠️ | All four offline checks present in `QRScanner._verify()`, but the in-process **nonce set** (`_seen_nonces`) remains; SRS rev 1.2 moved replay protection server-side → **[T19](#t19)** *(re-targeted: T6 shipped deliberately keeping it)* |
| [FR-PROV-04](SOFTWARE_REQUIREMENTS.md#fr-prov-04) | ✅ | `_load_public_keys()`; an empty trust store rejects all |
| [FR-PROV-05](SOFTWARE_REQUIREMENTS.md#fr-prov-05) | ✅ | Warning vs `SECURITY:` error inside `_verify()`; `qr_accepted` / `qr_rejected` from `scan()` |
| [FR-PROV-06](SOFTWARE_REQUIREMENTS.md#fr-prov-06) | ✅ | **T11 done.** `EXPECTED_COMMAND` check in `_verify()`, benign classification |
| [FR-PROV-07](SOFTWARE_REQUIREMENTS.md#fr-prov-07) | ✅ | `provisioning/network.apply()` then `client.register()`, off the UI thread via `binding.bind_async()` |
| [FR-PROV-08](SOFTWARE_REQUIREMENTS.md#fr-prov-08) | ✅ | `GUIWeb._on_binding_result()` holds 3 s ok / 6 s fail; reason surfaced from `client.register()` |
| [FR-PROV-09](SOFTWARE_REQUIREMENTS.md#fr-prov-09) | ✅ | **T10 done.** `identity.save()` creates the temp file `0o600` via `os.open` and re-asserts the mode after `os.replace`; gitignored |
| [FR-PROV-10](SOFTWARE_REQUIREMENTS.md#fr-prov-10) | ✅ | `binding._bind()` replaces the prior identity (device side) |
| [FR-PROV-11](SOFTWARE_REQUIREMENTS.md#fr-prov-11) | ✅ | Token not persisted by `DeviceIdentity` |

### 1.10 Network profile — FR-NET

[FR-NET-01](SOFTWARE_REQUIREMENTS.md#fr-net-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-net-02)/[04](SOFTWARE_REQUIREMENTS.md#fr-net-04)/[05](SOFTWARE_REQUIREMENTS.md#fr-net-05)
✅ — `provisioning/network.py` `apply()`, `_have_nmcli()`, `_is_connected()`, bounded timeouts throughout.

[FR-NET-03](SOFTWARE_REQUIREMENTS.md#fr-net-03) ⚠️ — `config.APPLY_NETWORK_PROFILE = True`
is checked in; the SRS requires default-disabled → **[T15](#t15)**.

### 1.11 Heartbeat & telemetry — FR-HB

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-HB-01](SOFTWARE_REQUIREMENTS.md#fr-hb-01)..[04](SOFTWARE_REQUIREMENTS.md#fr-hb-04) | ✅ | `heartbeat.HeartbeatWorker.start()` / `_run()` / `_collect()`; `events.emit()` |
| [FR-HB-05](SOFTWARE_REQUIREMENTS.md#fr-hb-05) | ✅ | **T5a done (B4).** Ack **by `event_id`**, never by position — `events.ack(event_ids)`, called from `heartbeat._run()` and `binding._flush_events()` |
| [FR-HB-06](SOFTWARE_REQUIREMENTS.md#fr-hb-06)..[09](SOFTWARE_REQUIREMENTS.md#fr-hb-09) | ✅ | uuid4 `event_id` in `emit()`; `_MAX_EVENTS = 200` drop-oldest; backoff in `_run()`; shutdown flush in `binding.shutdown()` |
| [FR-HB-10](SOFTWARE_REQUIREMENTS.md#fr-hb-10) <a id="fr-hb-10-row"></a> | ✅ | **T6 done (B5).** `binding._handle_revoked()`: emit + flush `device_revoked` while bound → stop heartbeat/sync → delete identity → purge user DB incl. faceprints → in-process return to init mode. **Step 6 (systemd self-restart) is not implemented** — the reset is in-process; recorded as SRS [D21](SOFTWARE_REQUIREMENTS.md#d21) → **[T20](#t20)** |

### 1.12 Logging & storage — FR-LOG

[FR-LOG-01](SOFTWARE_REQUIREMENTS.md#fr-log-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-log-02)/[03](SOFTWARE_REQUIREMENTS.md#fr-log-03)/[05](SOFTWARE_REQUIREMENTS.md#fr-log-05)
✅ — `observability/logging_setup.py` `setup_logging()` + `install_native_log_bridge()`;
`storage_monitor.check_storage()` / `get_storage_metadata()`.

[FR-LOG-04](SOFTWARE_REQUIREMENTS.md#fr-log-04) ⚠️ — no secret is *logged*, but
`network.apply()` passes the Wi-Fi password on the `nmcli` argv, exposing it in
the process list → **[T15](#t15)**.

### 1.13 Camera — FR-CAM

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-CAM-01](SOFTWARE_REQUIREMENTS.md#fr-cam-01) | ✅ | `hardware/camera_preview.PreviewController` background thread with `pause()` / `resume()` / `restart()`, feeding both the UI and the QR scanner |
| [FR-CAM-02](SOFTWARE_REQUIREMENTS.md#fr-cam-02) | ✅ | `frame_server.CameraStreamer` + `WebServer._stream_mjpeg()` re-serve frames as MJPEG on the loopback origin |
| [FR-CAM-03](SOFTWARE_REQUIREMENTS.md#fr-cam-03) | ✅ | JS stall watchdog in the `gui_web/web_window.py` bridge JS reconnects a stalled `<img>` |
| [FR-CAM-04](SOFTWARE_REQUIREMENTS.md#fr-cam-04) | ✅ | Extraction pause/resume around each attempt in `controller._run_authentication()` |

### 1.14 User interface — FR-UI

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-UI-01](SOFTWARE_REQUIREMENTS.md#fr-ui-01) | ⚠️ | All screens exist except the **IN/OUT selection screen**; `demo_ui/index.html` `#attendance` is a cosmetic toggle → **[T8](#t8)** |
| FR-UI-02 | ➖ | Deprecated rev 1.2 |
| [FR-UI-03](SOFTWARE_REQUIREMENTS.md#fr-ui-03) | ✅ | `WebSessionView.show_idle()` returns to the screensaver, overriding the JS default |
| [FR-UI-04](SOFTWARE_REQUIREMENTS.md#fr-ui-04)..[08](SOFTWARE_REQUIREMENTS.md#fr-ui-08) | ✅ | `controller.on_card_rejected()` (no camera), `_on_auth_complete()` denial branches, `on_user_tapped()` demo-only wake; generic failure text in `demo_ui/app.js` |
| [FR-UI-09](SOFTWARE_REQUIREMENTS.md#fr-ui-09) | ⚠️ | Keypad exists with the code hardcoded in **two** places — `demo_ui/app.js` (`expectedCode`) and `web_window.py` `Bridge.codeSubmitted()`. No auth effect today, but no production flag either → **[T18](#t18)** |
| FR-UI-10 | ➖ | Deprecated rev 1.2 |
| [FR-UI-11](SOFTWARE_REQUIREMENTS.md#fr-ui-11) | ✅ | `config.KIOSK_BORDERLESS` / `RUN_ON_REAL_SCREEN`; `GUIWeb._place_on_small_display()` |
| [FR-UI-12](SOFTWARE_REQUIREMENTS.md#fr-ui-12) | ❌ | **Seam built, deliberately stubbed.** `session/view.py` `show_unavailable()` documents "front-ends may alias this to `show_failure` until T9", and `WebSessionView.show_unavailable()` does exactly that. `controller.py` never calls it, and there is no dedicated screen → **[T9](#t9)** (smaller than it looks) |

### 1.15 Server contract — FR-API

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-API-01](SOFTWARE_REQUIREMENTS.md#fr-api-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-api-02)/[03](SOFTWARE_REQUIREMENTS.md#fr-api-03)/[05](SOFTWARE_REQUIREMENTS.md#fr-api-05)/[06](SOFTWARE_REQUIREMENTS.md#fr-api-06) | ✅ | `client.register()` / `post_status()`; `server/main.py` `register_device()`, `get_device_users()`, `post_status()`; `server/signing.py`; no `verify=False` in repo code |
| [FR-API-04](SOFTWARE_REQUIREMENTS.md#fr-api-04) | ⚠️ | Timeouts ✅, but `client.post_status()` branches only network / 410 / not-ok — 4xx and 5xx are treated alike → **[T13](#t13)** |
| [FR-API-07](SOFTWARE_REQUIREMENTS.md#fr-api-07) | ⚠️ | Single-use tokens + actionable reasons ✅, but `register_device()` mints `str(uuid.uuid4())` on every call, so re-registration **creates a new device row** and the old binding lingers → **[T14](#t14)** |
| [FR-API-08](SOFTWARE_REQUIREMENTS.md#fr-api-08) | ⚠️ | *New row — untracked in rev 1.1.* Re-registration must **replace** the prior binding; the server does not (same root cause as FR-API-07) → **[T14](#t14)** |
| [FR-API-09](SOFTWARE_REQUIREMENTS.md#fr-api-09)/[10](SOFTWARE_REQUIREMENTS.md#fr-api-10)/[11](SOFTWARE_REQUIREMENTS.md#fr-api-11)/[14](SOFTWARE_REQUIREMENTS.md#fr-api-14) | ✅ | `heartbeat._run()` acks on 2xx only; `server/main.py` `_ingest_events()` insert-or-ignore, `post_status()` 410 tombstone; `remote_provider._add_if_valid()` skips-and-counts |
| FR-API-12 | ➖ | Deprecated rev 1.2 |
| [FR-API-13](SOFTWARE_REQUIREMENTS.md#fr-api-13) | ⚠️ | Per-device scoping exists (`get_device_users()`, `user_store.get_for_device()`), but every new device is seeded from the **same default template** (`user_store.load_default_template()`) — not real door scoping → **[T14](#t14)** |
| [FR-API-15](SOFTWARE_REQUIREMENTS.md#fr-api-15) <a id="fr-api-15-row"></a> | ❌ | No attendance concept server-side — `attendance` has zero hits under `server/`. The generic events table would accept it, but nothing emits or journals it → **[T8](#t8)** |

### 1.16 Data model — FR-DATA

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-DATA-01](SOFTWARE_REQUIREMENTS.md#fr-data-01) | ⚠️ | Faceprint validity checked (`remote_provider._is_valid_faceprints()`) and `active` enforced on both auth paths ✅; still a single faceprints **dict**, so "list with ≥1 entry" and empty-list-in-`card_only` are unrepresentable → **[T3b](#t3b)** |
| [FR-DATA-02](SOFTWARE_REQUIREMENTS.md#fr-data-02) | ✅ | Never logged; deleted on revocation via `UserDatabase.clear()` (faceprints live in the one JSON cache) |
| [FR-DATA-03](SOFTWARE_REQUIREMENTS.md#fr-data-03) | ✅ | **T10 done.** Atomic + `0600` + deleted on revocation (`identity.save()` / `clear()`) |
| [FR-DATA-04](SOFTWARE_REQUIREMENTS.md#fr-data-04) | ✅ | `identity.load()` tolerates unknown keys |
| [FR-DATA-05](SOFTWARE_REQUIREMENTS.md#fr-data-05) | ✅ | `events.emit()` stamps `event_id`, `type`, UTC `ts` |
| [FR-DATA-06](SOFTWARE_REQUIREMENTS.md#fr-data-06) | ✅ | All access events carry `user_id` only — no name, no raw card id (`AuthService.last_user_id` → `controller`); an unregistered tap emits `reason="card_unregistered"` with no user fields |
| [FR-DATA-07](SOFTWARE_REQUIREMENTS.md#fr-data-07) | ✅ | `active` normalised at sync (defaults `True` when absent) and enforced both paths: card tap → `user_inactive`, face match → record skipped |

### 1.17 Business rules — BR

*New section: rev 1.1 tallied `BR (7)` in its summary but had no detail table,
so BR-04's gap was visible only inside the FR-SESS row.*

| ID | Status | Evidence / gap |
|---|---|---|
| [BR-01](SOFTWARE_REQUIREMENTS.md#br-01) | ✅ | Local membership = authorisation; sound because the server door-scopes ([FR-API-13](SOFTWARE_REQUIREMENTS.md#fr-api-13)) |
| [BR-02](SOFTWARE_REQUIREMENTS.md#br-02) | ✅ | `card_is_registered()` gate before any preview |
| [BR-03](SOFTWARE_REQUIREMENTS.md#br-03) | ✅ | SDK success **or** score ≥ `CUSTOM_THRESHOLD`, logged per decision (T12) |
| [BR-04](SOFTWARE_REQUIREMENTS.md#br-04) | ⚠️ | Same-card cooldown ✅, but a **different** card during a result hold is swallowed rather than pre-empting → **[T9](#t9)** |
| [BR-05](SOFTWARE_REQUIREMENTS.md#br-05) | ✅ | Card session shows the denial once and returns to idle (`_on_auth_complete()`) |
| [BR-06](SOFTWARE_REQUIREMENTS.md#br-06) | ✅ | All error paths deny; a failed pulse yields `access_output_failed`, not a grant |
| [BR-07](SOFTWARE_REQUIREMENTS.md#br-07) | ✅ | Revocation purges user data and denies all (T6) |

### 1.18 Non-functional — NFR

Mostly ✅ — threading `NFR-01`, responsiveness `NFR-02..04` (incl. the
`PREVIEW_LEAD_IN_MS` lead-in), offline `NFR-05..08`, atomic writes `NFR-09/10`,
shutdown watchdog `NFR-11` in `main_web.py` `main()`, security
`NFR-12/13/15..18`, maintainability `NFR-20/22`.

| ID | Status | Gap |
|---|---|---|
| [NFR-14](SOFTWARE_REQUIREMENTS.md#nfr-14) | ⚠️ | Device still keeps a nonce set; rev 1.2 puts replay protection server-side → **[T19](#t19)** |
| [NFR-19](SOFTWARE_REQUIREMENTS.md#nfr-19) | ✅ | **T1 + Qt removal.** One state machine in `session/controller.py`, dependent only on the `SessionView`/`Scheduler` protocols; `session/tests/` (27 cases) exercises it with no Qt, browser or `rsid_py` |
| [NFR-21](SOFTWARE_REQUIREMENTS.md#nfr-21) | ✅ | **T17 done.** `face-guard.service` runs `main_web.py` with `Restart=always` and the `rpi_py_build_lib` `LD_LIBRARY_PATH`; the dead `docs/rsid-host-mode.service` was deleted. *(The revocation self-restart clause depends on [T20](#t20).)* |

### 1.19 Architecture baseline as delivered

Rev 1.1 opened with two findings that drove the task ordering. **Both are now
resolved**, and the ordering they justified has happened — recorded here as the
baseline every remaining task builds on:

- **One session state machine.** `session/controller.py` owns session lifecycle,
  timers, triggers, result holds, auth dispatch and init mode, behind
  `session/view.py` (`SessionView`) and `session/scheduler.py`.
  `gui_web/web_window.py` is a view adapter. *(Was: duplicated across
  `gui_web` and the now-deleted `gui_qt`.)*
- **Decision separated from actuation.** `face_auth/auth_service.py` recognises
  and emits `auth_matched`; the controller decides, pulses the relay, and only
  then emits `access_granted`. *(Was: the biometric layer opened the relay.)*

> **Downstream contract — read before T8/T5b.** `access_granted` is emitted
> **post-pulse by the controller**, not by `auth_service`. Any new consumer —
> attendance ([T8](#t8)), grant-driven telemetry ([T5b](#t5b)) — must key off
> the controller's `access_granted` / `access_output_failed` and treat
> `auth_matched` as a decision breadcrumb only.

---

## 2. Task list

Live queue: **12 open tasks**. Delivered work is rolled up in
[Delivered](#delivered). The `Depends on` column lists only *open*
dependencies — everything else has shipped.

| # | Task | Scope | Depends on | Requirements |
|---|---|---|---|---|
| [T19](#t19) | Remove the device-side nonce set | device | — | FR-PROV-03, NFR-14 |
| [T20](#t20) | Resolve D21 (revocation restart semantics) | spec/ops | — | FR-HB-10, NFR-21 |
| [T4](#t4) | `device_mode` / `face_policy` plumbing | device+server | — | FR-MODE-01 |
| [T3b](#t3b) | `faceprints` as a list | device+server | — | FR-DB-01, FR-DATA-01 |
| [T7](#t7) | `card_only` mode | device | T4 | FR-MODE-03 |
| [T9](#t9) | Session edge cases + unavailable screen | device | — | FR-SESS-03, FR-CARD-04, FR-FACE-06, FR-UI-12, BR-04 |
| [T5b](#t5b) | Durable event queue | device | — | FR-MODE-10 |
| [T8](#t8) | `time_registry` mode | device+server | T4, T5b | FR-MODE-06..11, FR-API-15 |
| [T13](#t13) | HTTP error classification | device | — | FR-API-04 |
| [T14](#t14) | Server rebinding + door scoping | server | — | FR-API-07/08/13 |
| [T15](#t15) | Network profile defaults + password handling | device | — | FR-NET-03, FR-LOG-04 |
| [T18](#t18) | Retire keypad demo path | device | — | FR-UI-09 |

### Phase A — Hygiene and open decisions

#### <a id="t19"></a>T19. Remove the device-side nonce set

**Why now.** SRS rev 1.2 moved replay protection server-side: the provisioning
token is one-time ([FR-API-07](SOFTWARE_REQUIREMENTS.md#fr-api-07)), so a
replayed QR passes local checks and is refused at registration. The surviving
`_seen_nonces` set is dead weight *and* misleading — it is process-local, so it
gives no protection across a restart while looking like it does.

**Do.** Delete `_seen_nonces` and its rejection branch from
`qr_scanner.QRScanner` (`__init__` and `_verify()`). Keep the `nonce` field
parsed and forwarded to registration. Update the module docstring, which still
lists "replayed nonce" among the SECURITY-level rejections.

**Files.** `qr_scanner/qr_scanner.py`.

**Accept.** No nonce state on the device; the same QR presented twice passes
local verification both times and is refused by the server the second time with
an actionable reason. SRS [D12](SOFTWARE_REQUIREMENTS.md#d12) closes.

---

#### <a id="t20"></a>T20. Resolve D21 — revocation restart semantics

**The conflict.** [FR-HB-10](SOFTWARE_REQUIREMENTS.md#fr-hb-10) step 6 requires
an orderly **self-restart under systemd**. The build performs an **in-process**
return to init mode (`controller.start_init_mode()`, the same entry point used at
boot) — which was the design approved for T6, and which is deny-all and needs no
reboot, but is not what the spec says.

**Do.** Pick one and make the document and the code agree:

- **(a) Spec follows code** — reword FR-HB-10 step 6, the §3 state diagram
  (`Revoked --> InitMode: self-restart`), the §3 `revoked` state row, and NFR-21's
  "including the self-restart after revocation" clause to describe the in-process
  reset. No code change.
- **(b) Code follows spec** — exit after teardown and let `Restart=always` in
  `face-guard.service` bring the process back. Costs a camera/SDK
  re-initialisation on every revocation and needs the watchdog path checked.

**Recommendation: (a).** The in-process reset already satisfies the *intent*
(fail-secure, technician can re-provision without a power cycle) and avoids a
restart loop if a revoked device is left powered on.

**Files.** (a) `docs/SOFTWARE_REQUIREMENTS.md` only.
(b) `provisioning/binding.py` `_handle_revoked()`, `gui_web/web_window.py`
`_on_device_revoked()`, `face-guard.service`.

**Accept.** SRS [D21](SOFTWARE_REQUIREMENTS.md#d21) closes with no residual
mismatch between FR-HB-10 and the code.

---

### Phase B — Mode plumbing and data shape

#### <a id="t4"></a>T4. `device_mode` / `face_policy` plumbing

**Do.** Add both to the register response, the QR envelope and the identity
file; resolve at runtime as *server value → `config.py` fallback*. Introduce
`DEVICE_MODE`, `FACE_POLICY`, `DIRECTION_SELECT_TIMEOUT_SEC` in `config.py`
(all three are already specified in
[SRS §9.4](SOFTWARE_REQUIREMENTS.md#94-configuration-parameters) but absent from
the module). Mark `AUTH_ONLY_ON_CARD` deprecated and route its remaining callers
— `controller.on_user_tapped()` and `main_web.main()` — through the new mode.

**Files.** `server/models.py` (`RegisterResponse`), `server/main.py`
(`generate_qr()`, `register_device()`); `provisioning/identity.py`
(`DeviceIdentity`), `provisioning/client.py`; `config.py`;
`session/controller.py`.

**Accept.** A device provisioned `card_and_face` runs it regardless of local
config; with no server value the config fallback applies; the identity file
round-trips both fields and an older file without them still loads
([FR-DATA-04](SOFTWARE_REQUIREMENTS.md#fr-data-04)).

---

#### <a id="t3b"></a>T3b. `faceprints` as a list

**Do.** [SRS §9.1](SOFTWARE_REQUIREMENTS.md#91-local-user-record) specifies
`faceprints` as a list of zero or more SDK-shaped objects; the code still stores
and validates a single dict. Accept a list at sync (validating each entry), allow
an **empty** list, and iterate a user's faceprints when matching.

**Why it is separate from T3.** [T7](#t7) `card_only` needs "a user with no
faceprints" to be representable, which a mandatory dict forbids — so this lands
with `card_only` rather than having blocked schema v2.

**Files.** `db/remote_provider.py` (`_is_valid_faceprints()`, `_add_if_valid()`);
`face_auth/auth_service.py` (`_to_rsid_faceprints()` and both match loops);
`server/default_user_database.json`.

**Accept.** A record with `faceprints: []` syncs and is valid in `card_only`; a
record with two faceprints matches on either; a legacy single-dict record is
rejected or coerced, never crashed on.

---

#### <a id="t7"></a>T7. `card_only` mode

**Do.** In `card_only`, a card present in the local DB opens the relay
immediately — no session, no preview, no biometric call. Path:
`CardReader → SessionController → AccessOutput`.

**Files.** `session/controller.py`; `config.py`.

**Accept.** With `card_only`, a valid card opens the door with the camera never
powered; an unknown card is still rejected pre-camera
([BR-02](SOFTWARE_REQUIREMENTS.md#br-02)); `card_and_face` is unaffected.

---

#### <a id="t9"></a>T9. Session edge cases and the unavailable screen

**Do.** Four related corrections, all in the controller except the new screen:

1. A *different* valid card during a result hold pre-empts it and starts a new
   session; the cooldown stays a per-card debounce, not a global input lock
   ([BR-04](SOFTWARE_REQUIREMENTS.md#br-04)). Requires releasing the card flag
   when the *session* ends rather than when the *hold* ends.
2. `on_card_detected()` must respect the init-mode guard that
   `on_user_tapped()` and `on_card_rejected()` already apply.
3. Apply the 20 s biometric backoff on the **card** path
   (`authenticate_with_card_and_face()`), not just the face-only path.
4. Surface that condition as a distinct "temporarily unavailable" screen. **The
   seam already exists**: `SessionView.show_unavailable()` is declared and
   aliased to `show_failure` in `WebSessionView` pending this task — so the work
   is a real `demo_ui` screen plus a controller call, not new plumbing.

**Files.** `session/controller.py`; `face_auth/auth_service.py` (backoff gate on
the card path); `demo_ui/` (new screen); `gui_web/web_window.py`
(`WebSessionView.show_unavailable()` — drop the alias).

**Accept.** Card B during card A's failure hold starts a session for B; the same
card within 2 s is still ignored; a card tap during backoff shows the unavailable
screen — visually distinct from a mismatch — and never opens the door; no session
can start during init mode.

---

### Phase C — Attendance

#### <a id="t5b"></a>T5b. Durable event queue

**Do.** Add an on-disk queue for durable events; attendance uses it, telemetry
keeps the bounded ring ([FR-MODE-10](SOFTWARE_REQUIREMENTS.md#fr-mode-10): losing
a check-in is a payroll error, not a lost telemetry line). Ack-by-`event_id` is
already in place from T5a, so the queue plugs into an interface that cannot drop
undelivered entries.

**Files.** new `observability/durable_queue.py`; `observability/events.py`;
`provisioning/heartbeat.py`.

**Accept.** Attendance events survive a process kill and are delivered after
restart; telemetry stays capped at 200 drop-oldest; events emitted *during* an
in-flight heartbeat are still never acked.

---

#### <a id="t8"></a>T8. `time_registry` mode

**Do.** *Device:* IN/OUT selection screen replacing the screensaver; direction
latched until the card tap completes or `DIRECTION_SELECT_TIMEOUT_SEC` elapses;
face policy `none` | `verify`; emit `attendance_event {user_id, direction, ts}`
through the durable queue ([T5b](#t5b)); **relay never actuated**.
*Server:* accept and persist attendance events, expose a per-user journal.

**Files.** `demo_ui/index.html` + `demo_ui/app.js` (the existing `#attendance`
toggle becomes functional); `session/controller.py`;
`observability/durable_queue.py`; `server/main.py`, `server/db.py`,
`server/models.py`.

**Accept.** Selecting IN then tapping a registered card records exactly one
attendance event with the right direction and **no relay pulse**; a selection
timeout returns to idle recording nothing; a mismatch under `verify` records
nothing; events survive a restart and appear in the server journal.

---

### Phase D — Isolated fixes (no refactoring)

#### <a id="t13"></a>T13. HTTP error classification
Distinguish connect error / timeout / permanent 4xx / transient 5xx in
`provisioning/client.py` (`register()`, `post_status()`) and
`db/remote_provider.py` (`load_all()`); only transient classes back off.
**Accept.** A 400 and a 503 are logged and retried differently; 410 keeps its
dedicated `DeviceRevokedError` path.

#### <a id="t14"></a>T14. Server rebinding and real door scoping
`register_device()` must **replace** the prior device row rather than minting a
second `uuid4`; stop seeding every device from `load_default_template()` — scope
users to the door.
**Accept.** Rebinding the same physical terminal leaves exactly one active row;
a new device starts with the users of *its* door, not a template.

#### <a id="t15"></a>T15. Network profile defaults and password handling
Ship `APPLY_NETWORK_PROFILE = False`; pass the Wi-Fi password to `nmcli` via
stdin/file instead of argv (`network.apply()`).
**Accept.** A dev machine is never reconfigured by default; the password is
absent from the process list.

#### <a id="t18"></a>T18. Retire the keypad demo path
Remove the hardcoded code from `demo_ui/app.js` **and**
`web_window.py` `Bridge.codeSubmitted()`, or gate the whole path behind an
explicitly non-production flag. `demo_ui/README.md` documents the constant too.
**Accept.** No credential constant ships in production assets; no code path
grants on PIN entry.

---

### <a id="delivered"></a>2.1 Delivered

Full write-ups are in git history; these are the load-bearing summaries. Every
item is **implemented and awaiting (or covered by) device validation** —
see [§3](#3-batches-and-device-validation).

| # | Task | Outcome |
|---|---|---|
| 1 | **T1** Extract `SessionController` | Session machine moved to `session/controller.py` behind `SessionView` / `Scheduler`; `web_window.py` reduced to a view adapter. Satisfies NFR-19; covered by `session/tests/` with a manual-clock scheduler. *(B1)* |
| 2 | **T1b** `gui_qt` freeze notice | Delivered 2026-08-26 as a comment-only banner, then **superseded**: `gui_qt/` and `main_qt.py` were deleted outright on 2026-08-31, so the artefact no longer exists. Nothing to maintain. |
| 3 | **T2** Decision ↔ actuation split | See the [downstream contract](#119-architecture-baseline-as-delivered) — `access_granted` is post-pulse; `relay_api.open_door()` returns `bool`; `access_output_failed` is the fail-secure outcome. *(B3)* |
| 4 | **T3** User-record schema v2 | `{user_id, name, active, permission_level, faceprints}`; `active` enforced on both auth paths; every emit rewritten to `user_id` via `AuthService.last_user_id`. `faceprints`-as-a-list carved out as [T3b](#t3b). *(B6)* |
| 5 | **T5a** Ack by `event_id` | `events.ack(event_ids)` removes by id, never by position, so a ring eviction during an in-flight beat cannot drop undelivered events. *(B4)* |
| 6 | **T6** Fail-secure revocation | On 410: emit + flush `device_revoked` **while still bound** → stop heartbeat/sync → delete identity → purge user DB incl. faceprints → in-process init-mode reset. Server-authoritative lifecycle below. **Step 6 diverges from the SRS** → [T20](#t20). *(B5)* |
| 7 | **T10** Identity file `0600` | Temp file created `0o600` via `os.open`; mode re-asserted after `os.replace`. *(B0)* |
| 8 | **T11** Validate QR `command` | `EXPECTED_COMMAND` check in `_verify()`, warning-level per FR-PROV-05. *(B0)* |
| 9 | **T12** Log score and threshold | One decision line per path carrying `sdk_success`, `score`, `threshold` and the verdict. *(B0)* |
| 10 | **T16** Init mode on every start | `start_init_mode()` always enters and emits `init_mode_entered`; `INIT_MODE_ENABLED` sizes the window only (0-length = enter-then-end, `end_init_mode()` idempotent). *(B2)* |
| 11 | **T17** systemd unit | `face-guard.service` runs `main_web.py`, `Restart=always`, with the `rpi_py_build_lib` `LD_LIBRARY_PATH` that the old unit lacked; dead `docs/rsid-host-mode.service` deleted. *(2026-08-31)* |
| 12 | **Fix** Remote DB sync re-armed on bind (FR-DB-07) | Detail below. |

**T6 — server-authoritative revocation lifecycle** *(kept in full: T14 and T20
both depend on it).* Revocation is a server-side tombstone handshake, so the
device's local cleanup never needs to be guaranteed:

1. Operator removes the device → its row flips `active` → `suspended`
   (`server/main.py` `delete_device()`, soft delete — the row is kept as a
   tombstone). From that instant the device is untrusted: `post_status()` refuses
   every heartbeat from a `suspended` / `revoked_ack` row and always answers 410.
2. The device's next heartbeat is answered 410; the row flips to `revoked_ack`
   and the device runs its local teardown.
3. The tombstone is purged on the next device-list load (`_purge_acknowledged()`).

Because the server is the sole authority, a comms failure or a failed local DB
wipe cannot leave the device trusted: if it never sees the 410 it stays locked out
server-side, and `_handle_revoked()` destroys the identity even if the DB wipe
throws. Re-enrollment is collision-free by construction — every bind mints a fresh
`device_id` and token. **No separate revoke-ack endpoint is needed.**

**Fix — remote DB sync not re-armed after runtime binding (FR-DB-07).** A device
that booted **unbound** and then bound via QR never fetched its remote user DB,
leaving a bound `remote`-mode device with no source of truth for door access:
`AuthService.__init__` decided remote-vs-local once at boot, and QR binding
saved the identity without re-arming the provider. Fixed by
`UserDatabase.attach_remote()` / `is_remote_enabled()` (wire a provider *after*
construction) plus `AuthService.enable_remote_sync()` (load the fresh
identity, attach, do one **immediate blocking** `sync_from_remote()` so users
appear right after pairing, then start auto-sync), called off the UI thread from
`GUIWeb._on_binding_result()`. Covered by `db/test_remote_sync.py`.

---

## 3. Batches and device validation

Delivery model: each batch is a small, independently revertable change set. After
every batch the owner validates on the real device using the checklist below; the
next batch starts only after sign-off.

**Green gate.** The server suite (`server/tests/`, **66 tests**) must stay green
after every batch, alongside `session/tests/` (**27**),
`db/test_remote_sync.py` (**7**), `provisioning/tests/test_revocation.py` (**4**)
and `db/tests/test_revocation_wipe.py` (**2**). Run with
`APPLY_NETWORK_PROFILE = False` on dev machines without `nmcli`
([D17](SOFTWARE_REQUIREMENTS.md#d17)). Note `pytest` is not installed on every
dev box — the Pi or the project venv is the reference environment.

| Batch | Content | Status |
|---|---|---|
| **B0** | [T10](#delivered) + [T11](#delivered) + [T12](#delivered) | Implemented — awaiting device validation |
| **B1** | [T1](#delivered)a: `session/controller.py`, web UI ported | Implemented — awaiting device validation |
| **B2** | [T1b](#delivered) freeze notice *(since superseded by the `gui_qt/` deletion)* + [T16](#delivered) | Implemented — awaiting device validation |
| **B3** | [T2](#delivered) decision separation | Implemented — awaiting device validation |
| **B4** | [T5a](#delivered) ack-by-`event_id` | Implemented — awaiting device validation |
| **B5** | [T6](#delivered) fail-secure revocation | Implemented — awaiting device validation |
| **B6** | [T3](#delivered) schema v2 (server, then device) | Implemented — awaiting device validation |
| **B7** | [T19](#t19) nonce removal + [T20](#t20) D21 ruling | pending |
| B8 | [T4](#t4) `device_mode` / `face_policy` | pending |
| B9 | [T7](#t7) `card_only` + [T3b](#t3b) `faceprints` as a list | pending |
| B10 | [T9](#t9) pre-emption + card-path backoff + unavailable screen | pending |
| B11 | [T5b](#t5b) durable event queue | pending |
| B12 | [T8](#t8) server half (attendance intake + journal) | pending |
| B13 | [T8](#t8) device half (IN/OUT flow) | pending |
| B14 | [T13](#t13) + [T14](#t14) + [T15](#t15) + [T18](#t18) | pending |

### B0 device checklist

1. **T10** — provision (or re-provision) the terminal, then
   `ls -l device_identity.json` → must show `-rw-------`.
2. **T11** — scan a QR with a non-`provision_device` command → rejected with
   `QR rejected (unsupported command …)` at WARNING; a normal QR still binds. To
   mint a wrong-command QR use `other/qr_code_poc/gen_qr_code.py` with the command
   edited, or skip (covered off-device by automated tests).
3. **T12** — one grant and one deny → the log shows
   `1:1 decision: card=… sdk_success=… score=… threshold=400 -> GRANT` and
   `… -> DENY`.

### B1 device checklist

The session machine moved to `session/controller.py` (pure Python — no Qt or
`rsid_py`) with `web_window.py` reduced to view/scheduler adapters. Confirm the
ported web UI is behaviourally unchanged:

1. **Grant** — tap a registered card with the matching face → camera view, then
   "Welcome, `<name>`", then automatic return to idle. Log shows the unchanged
   `1:1 decision: … -> GRANT` line.
2. **Card mismatch** — registered card, wrong/absent face → one brief failure
   screen, then idle; no retry loop, no relay pulse.
3. **Unregistered card** — unknown card → brief failure only, with **no** camera
   preview or auth attempt.
4. **Session timeout / demo mode** — with `AUTH_ONLY_ON_CARD=False`, a tap starts
   a face-only session that retries on cadence and falls back to idle after
   `AUTH_SESSION_TIMEOUT_SEC`.
5. **Init mode + provisioning** — "Init Mode" overlay + camera; a valid QR binds
   once and scanning stops; the overlay times out back to idle.

### B2 device checklist

Init mode is the entry state on **every** start (T16); `INIT_MODE_ENABLED` only
sizes the scan window.

1. **Already-bound terminal still scans** — boot a bound device with
   `INIT_MODE_ENABLED=True` → overlay + camera at startup; a valid QR
   re-provisions without a factory reset.
2. **Window timeout** — nothing presented → overlay times out after
   `INIT_MODE_DURATION_SEC`, UI returns to idle.
3. **Disabled = zero-length window** — `INIT_MODE_ENABLED=False` → no lingering
   overlay or preview, straight to idle, and `init_mode_entered` still emitted
   (single path).

*(The rev 1.1 item "`gui_qt` unchanged" is dropped — the directory no longer
exists.)*

### B3 device checklist

Decision (recognition) is separated from actuation (T2). With `RUN_WITH_RELAY=True`
and a strike wired:

1. **Grant opens the door before "Welcome"** — the strike pulses, *then* the
   welcome screen. Event order `auth_matched` → `relay_opened` → `access_granted`,
   never `access_granted` first.
2. **Relay failure fails secure** — strike disconnected/faulted → a **failure**
   screen, an `access_output_failed` event, and **no** `access_granted`.
3. **Denial never pulses** — wrong/absent face → failure screen, `access_denied`,
   no `relay_opened`.
4. **Relay-off demo still grants** — `RUN_WITH_RELAY=False` → "Welcome" and
   `access_granted` with no physical pulse.

### B4 device checklist

Acknowledgement is by `event_id`, not position (FR-HB-05). Bound to a reachable
server:

1. **Events deliver end-to-end** — a grant, a denial and a QR rejection each
   appear exactly once server-side after the next beat.
2. **Nothing lost under load** — a burst of rapid taps so several ride one beat;
   every one lands and the pending buffer drains to 0.
3. **Failed beat keeps events** — block the server mid-beat; events stay buffered
   and go out on the next successful beat.
4. **Shutdown flush** — stop the app with pending events; the final flush
   delivers them and acks by id.

### B5 device checklist

Fail-secure revocation (T6). With the device bound and online:

1. **Revoke from the dashboard** → on the next heartbeat the device logs the 410,
   and the server shows `device_revoked` **received** (flushed while the
   credential was still valid).
2. **Local wipe** — `device_identity.json` is gone and the user DB file is empty
   on disk.
3. **Deny-all** — a previously working card tap is refused; no relay pulse.
4. **Re-provision without a power cycle** — present a fresh QR → the device binds,
   syncs its users immediately (FR-DB-07 fix) and grants again.
5. **Note for [T20](#t20)** — confirm whether the process stayed up (in-process
   reset, current behaviour) or restarted; this is the observation that settles
   D21.

### B6 device checklist

User-record schema v2 (T3).

1. **Round trip** — a server-side user with `user_id` syncs and grants normally.
2. **`active: false` denies** — suspend a user server-side, re-sync → the card tap
   is refused with `access_denied` / `reason="user_inactive"`, no relay pulse.
3. **Malformed record skipped** — a record without `user_id` is skipped and
   counted (`db_sync_invalid_record` / `db_sync_skipped_entries`), and the rest of
   the payload still applies.
4. **No PII in telemetry** — server event log shows `user_id` only: no cardholder
   name, no raw card id, including for an unregistered tap
   (`reason="card_unregistered"`).

*(Per-batch checklists for B7+ are added when each batch is implemented.)*
