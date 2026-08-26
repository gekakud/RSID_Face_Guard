# RSID Face Guard — Implementation Plan

**Code reconciliation and ordered task list**

| Item | Detail |
|---|---|
| Document ID | PLAN-FG-001 |
| Revision | 1.1 |
| Date | 2026-08-26 |
| Specification | [`SOFTWARE_REQUIREMENTS.md`](SOFTWARE_REQUIREMENTS.md) rev 1.3 |
| Basis | Static audit of the working tree; every status below carries `file:line` evidence |
| Scope | Device application **and** the reference server (`server/`) |
| Delivery model | Small batches (B0..B13, [§3](#3-batches-and-device-validation)); each batch is device-validated by the owner before the next starts |
| Decision | `gui_qt/` is **frozen**: it receives no new features and is not ported to the shared controller. Web UI is the only maintained front-end |

**How to read this.** [§1](#1-reconciliation-table) states, per requirement, what the
code does *today*. [§2](#2-task-list) turns every gap into a task, ordered so that
architectural changes land first and no later task forces a rewrite of an
earlier one. [§3](#3-batches-and-device-validation) is the batch schedule and
per-batch device-validation checklists.

Status legend: **✅ IMPLEMENTED** · **⚠️ PARTIAL** · **❌ MISSING** ·
**➖ DEPRECATED** (withdrawn in SRS rev 1.2, no work required).

---

## 1. Reconciliation table

### 1.1 Summary

| Area | ✅ | ⚠️ | ❌ | ➖ |
|---|---|---|---|---|
| FR-STATE (12) | 6 | 0 | 0 | 4 *(06/08/10/12)* |
| FR-MODE (11) | 3 | 0 | 8 | 0 |
| FR-SESS (8) | 7 | 1 | 0 | 0 |
| FR-FACE (7) | 4 | 3 | 0 | 0 |
| FR-CARD (6) | 6 | 0 | 0 | 0 |
| FR-OUT (6) | 5 | 0 | 1 | 0 |
| FR-DB (8) | 7 | 1 | 0 | 0 |
| FR-PROV (11) | 7 | 3 | 1 | 0 |
| FR-NET (5) | 4 | 1 | 0 | 0 |
| FR-HB (10) | 8 | 1 | 1 | 0 |
| FR-LOG (5) | 4 | 1 | 0 | 0 |
| FR-CAM (4) | 4 | 0 | 0 | 0 |
| FR-UI (12) | 7 | 2 | 1 | 2 *(02/10)* |
| FR-API (15) | 9 | 3 | 1 | 1 *(12)* |
| FR-DATA (7) | 2 | 3 | 2 | 0 |
| BR (7) | 6 | 1 | 0 | 0 |
| NFR (22) | 19 | 3 | 0 | 0 |
| **Total (156)** | **108** | **23** | **15** | **7** |

Active requirements: 149. **Compliance today: 108/149 = 72 %.**

### 1.2 Operating states — FR-STATE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-STATE-01](SOFTWARE_REQUIREMENTS.md#fr-state-01) | ✅ | `provisioning/binding.py:44-61` `start_if_bound()` resumes from persisted identity |
| [FR-STATE-02](SOFTWARE_REQUIREMENTS.md#fr-state-02) | ⚠️ | Deny-all in `revoked` not implemented — see [FR-HB-10](#fr-hb-10-row) → **T6** |
| [FR-STATE-03](SOFTWARE_REQUIREMENTS.md#fr-state-03) | ✅ | Session guarded by `_session_active` / `auth_in_progress` (`gui_web/web_window.py:579,600-602`) |
| [FR-STATE-04](SOFTWARE_REQUIREMENTS.md#fr-state-04) | ✅ | Full flow runs from local cache; no server call on the door path |
| [FR-STATE-05](SOFTWARE_REQUIREMENTS.md#fr-state-05) | ✅ | `db/user_database.py:148-150` in-memory cache lookup |
| FR-STATE-06 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-07](SOFTWARE_REQUIREMENTS.md#fr-state-07) | ✅ | Buffered in ring, drained on ack (`provisioning/heartbeat.py:92-119`) |
| FR-STATE-08 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-09](SOFTWARE_REQUIREMENTS.md#fr-state-09) | ✅ | `db/user_database.py:93-134` periodic background sync |
| FR-STATE-10 | ➖ | Deprecated rev 1.2 |
| [FR-STATE-11](SOFTWARE_REQUIREMENTS.md#fr-state-11) | ✅ | Only 410 raises `DeviceRevokedError` (`provisioning/client.py:134-137`) |
| FR-STATE-12 | ➖ | Deprecated rev 1.2 |

### 1.3 Operating modes — FR-MODE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-MODE-01](SOFTWARE_REQUIREMENTS.md#fr-mode-01) | ❌ | `device_mode` has **zero hits** repo-wide; absent from `server/models.py:104-111` `RegisterResponse` and `provisioning/identity.py:40-53`. Mode is the local boolean `config.py:33` → **T4** |
| [FR-MODE-02](SOFTWARE_REQUIREMENTS.md#fr-mode-02) | ✅ | `face_auth/auth_service.py:154-158,335-341` DB-only check, no camera |
| [FR-MODE-03](SOFTWARE_REQUIREMENTS.md#fr-mode-03) | ❌ | No `card_only`; every card path calls `authenticate_with_card` (`web_window.py:639-642`) → **T7** |
| [FR-MODE-04](SOFTWARE_REQUIREMENTS.md#fr-mode-04) | ✅ | `face_auth/auth_service.py:160-216` 1:1 against cardholder |
| [FR-MODE-05](SOFTWARE_REQUIREMENTS.md#fr-mode-05) | ✅ | `face_auth/auth_service.py:219-293`, gated by `AUTH_ONLY_ON_CARD` |
| [FR-MODE-06](SOFTWARE_REQUIREMENTS.md#fr-mode-06) | ❌ | No IN/OUT screen or latch. `demo_ui/app.js:249-275` toggle is cosmetic — never read by Python → **T8** |
| [FR-MODE-07](SOFTWARE_REQUIREMENTS.md#fr-mode-07) | ❌ | No `attendance_event` emission anywhere → **T8** |
| [FR-MODE-08](SOFTWARE_REQUIREMENTS.md#fr-mode-08) | ❌ | No relay-suppressed mode → **T8** |
| [FR-MODE-09](SOFTWARE_REQUIREMENTS.md#fr-mode-09) | ❌ | Depends on T8 → **T8** |
| [FR-MODE-10](SOFTWARE_REQUIREMENTS.md#fr-mode-10) | ❌ | No durable disk queue; only the in-memory deque `observability/events.py:41-44` → **T5** |
| [FR-MODE-11](SOFTWARE_REQUIREMENTS.md#fr-mode-11) | ❌ | Pointer to [FR-API-15](#fr-api-15-row) → **T8** |

### 1.4 Session orchestration — FR-SESS

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-SESS-01](SOFTWARE_REQUIREMENTS.md#fr-sess-01) | ✅ | `gui_web/web_window.py:488-528`, `gui_web/frame_server.py:150-175` same-origin loopback |
| [FR-SESS-02](SOFTWARE_REQUIREMENTS.md#fr-sess-02) | ✅ | Preview paused at startup `web_window.py:334-335`, resumed per session `:588` |
| [FR-SESS-03](SOFTWARE_REQUIREMENTS.md#fr-sess-03) | ⚠️ | Two gaps: (a) no different-card pre-emption — flag cleared only after the hold (`auth_service.py:332-333`, `web_window.py:677,691`); (b) card path omits the init-mode guard (`web_window.py:579`) → **T9** |
| [FR-SESS-04](SOFTWARE_REQUIREMENTS.md#fr-sess-04) | ✅ | `web_window.py:590-598` retry/timeout; card session ends on first failure `:679-692` |
| [FR-SESS-05](SOFTWARE_REQUIREMENTS.md#fr-sess-05) | ✅ | `web_window.py:600-602,630-633` |
| [FR-SESS-06](SOFTWARE_REQUIREMENTS.md#fr-sess-06) | ✅ | `web_window.py:658-672` timers stopped before result |
| [FR-SESS-07](SOFTWARE_REQUIREMENTS.md#fr-sess-07) | ✅ | Worker thread `:634` → `_SignalBridge.auth_result` `:244-247` |
| [FR-SESS-08](SOFTWARE_REQUIREMENTS.md#fr-sess-08) | ✅ | `web_window.py:611-624` |

### 1.5 Face authentication — FR-FACE

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-FACE-01](SOFTWARE_REQUIREMENTS.md#fr-face-01) | ✅ | `face_auth/auth_service.py:71-77` connect; 1:1 `:160`, 1:N `:219` |
| [FR-FACE-02](SOFTWARE_REQUIREMENTS.md#fr-face-02) | ✅ | `face_auth/auth_service.py:170,184-195` |
| [FR-FACE-03](SOFTWARE_REQUIREMENTS.md#fr-face-03) | ⚠️ | Rule correct (`:197,266-268`) but threshold/score **never logged** → **T12** |
| [FR-FACE-04](SOFTWARE_REQUIREMENTS.md#fr-face-04) | ⚠️ | `auth_service.py:29-37,198,275` calls the relay **directly** from the match callback — no distinct decision stage → **T2** |
| [FR-FACE-05](SOFTWARE_REQUIREMENTS.md#fr-face-05) | ✅ | `:179-181`, `:186-188`, `:202`, `:279` distinct reasons |
| [FR-FACE-06](SOFTWARE_REQUIREMENTS.md#fr-face-06) | ⚠️ | Backoff + reconnect + event exist (`:212-215,289-292`) but the gate is only on the face-only path (`:229-232`), **not** the card path; no UI feedback → **T9** |
| [FR-FACE-07](SOFTWARE_REQUIREMENTS.md#fr-face-07) | ✅ | All exception paths return deny (`:210-216,287-293`) |

### 1.6 Card reader — FR-CARD

All ✅. `hardware/card_reader_api.py:35-76` backend selection; `face_auth/auth_service.py:314-357` monitor thread, 2 s cooldown `:317,329-330`, DB check `:154-158,335`, separate callbacks `:339-346`, exception recovery `:348-351`.

> Note: [FR-CARD-04](SOFTWARE_REQUIREMENTS.md#fr-card-04) is ✅ *as coded* but the SRS requires reads to resume **during** the result hold; the flag clears only after it (`web_window.py:677,691`). Corrected under **T9** together with [FR-SESS-03](SOFTWARE_REQUIREMENTS.md#fr-sess-03).

### 1.7 Access output — FR-OUT

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-OUT-01](SOFTWARE_REQUIREMENTS.md#fr-out-01) | ✅ | `config.py:164-168`, `hardware/relay_api.py:110-113` |
| [FR-OUT-02](SOFTWARE_REQUIREMENTS.md#fr-out-02) | ✅ | Daemon thread, 3 s default (`auth_service.py:37`, `relay_api.py:71-82`) |
| [FR-OUT-03](SOFTWARE_REQUIREMENTS.md#fr-out-03) | ✅ | `relay_api.py:119` |
| [FR-OUT-04](SOFTWARE_REQUIREMENTS.md#fr-out-04) | ✅ | `auth_service.py:79-84` non-fatal |
| [FR-OUT-05](SOFTWARE_REQUIREMENTS.md#fr-out-05) | ✅ | `relay_api.py:49-59` degrades gracefully |
| [FR-OUT-06](SOFTWARE_REQUIREMENTS.md#fr-out-06) | ❌ | No `access_output_failed`; worse, `access_granted` is emitted **before** the relay result is known (`auth_service.py:198-199`) → **T2** |

### 1.8 User DB & sync — FR-DB

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-DB-01](SOFTWARE_REQUIREMENTS.md#fr-db-01) | ⚠️ | Atomic write ✅ (`db/local_provider.py:35-42`), but schema is `{badge: {name, permission_level, faceprints}}` (`db/remote_provider.py:146-150`): no `user_id`, no `active`, faceprints is a **dict not a list**; no old-schema discard (`local_provider.py:24-33`) → **T3** |
| [FR-DB-02](SOFTWARE_REQUIREMENTS.md#fr-db-02) | ✅ | `auth_service.py:158,170,200`; `permission_level` never gates |
| [FR-DB-03](SOFTWARE_REQUIREMENTS.md#fr-db-03) | ✅ | `db/user_database.py:93-134` |
| [FR-DB-04](SOFTWARE_REQUIREMENTS.md#fr-db-04) | ✅ | `db/user_database.py:148-150` |
| [FR-DB-05](SOFTWARE_REQUIREMENTS.md#fr-db-05) | ✅ | `db/remote_provider.py:134-145`; failed fetch is a no-op `db/user_database.py:74-76` |
| [FR-DB-06](SOFTWARE_REQUIREMENTS.md#fr-db-06) | ✅ | All four events present (`remote_provider.py:86,67,143,88`) |
| [FR-DB-07](SOFTWARE_REQUIREMENTS.md#fr-db-07) | ✅ | `db/remote_provider.py:55-57` |
| [FR-DB-08](SOFTWARE_REQUIREMENTS.md#fr-db-08) | ✅ | `db/user_database.py:78-90` full replace + `db_users_revoked` |

### 1.9 Provisioning & QR trust — FR-PROV

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-PROV-01](SOFTWARE_REQUIREMENTS.md#fr-prov-01) | ⚠️ | Init mode runs only when `INIT_MODE_ENABLED` (`web_window.py:549`); SRS rev 1.2 requires it on **every** start, config controlling duration only → **T16** |
| [FR-PROV-02](SOFTWARE_REQUIREMENTS.md#fr-prov-02) | ✅ | `qr_scanner/qr_scanner.py:13-34,129,134,162,173` |
| [FR-PROV-03](SOFTWARE_REQUIREMENTS.md#fr-prov-03) | ⚠️ | All four offline checks present (`:129,140-144,152-157,162-171`) but an in-process **nonce set** remains (`:116,173-178`); rev 1.2 moved replay protection server-side → **T6** |
| [FR-PROV-04](SOFTWARE_REQUIREMENTS.md#fr-prov-04) | ✅ | `qr_scanner.py:74-97`; empty store rejects all |
| [FR-PROV-05](SOFTWARE_REQUIREMENTS.md#fr-prov-05) | ✅ | Warning vs `SECURITY:` error `:130,136,143,149,155`; events `:229,233` |
| [FR-PROV-06](SOFTWARE_REQUIREMENTS.md#fr-prov-06) | ❌ | `command` is documented (`qr_scanner.py:16`) but **never checked** → **T11** |
| [FR-PROV-07](SOFTWARE_REQUIREMENTS.md#fr-prov-07) | ✅ | `provisioning/client.py:52-58`, async `binding.py:65-75` |
| [FR-PROV-08](SOFTWARE_REQUIREMENTS.md#fr-prov-08) | ✅ | `web_window.py:384-389` 3 s / 6 s; reason from `client.py:83-92` |
| [FR-PROV-09](SOFTWARE_REQUIREMENTS.md#fr-prov-09) | ⚠️ | Atomic ✅ `identity.py:89-96`, gitignored ✅; **no `chmod 0600`** anywhere → **T10** |
| [FR-PROV-10](SOFTWARE_REQUIREMENTS.md#fr-prov-10) | ✅ | `provisioning/binding.py:77-100` (device side) |
| [FR-PROV-11](SOFTWARE_REQUIREMENTS.md#fr-prov-11) | ✅ | Token not persisted (`identity.py:40-53`) |

### 1.10 Network profile — FR-NET

[FR-NET-01](SOFTWARE_REQUIREMENTS.md#fr-net-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-net-02)/[04](SOFTWARE_REQUIREMENTS.md#fr-net-04)/[05](SOFTWARE_REQUIREMENTS.md#fr-net-05) ✅ — `provisioning/network.py:65-69,84-118`, timeout `:106,113`.
[FR-NET-03](SOFTWARE_REQUIREMENTS.md#fr-net-03) ⚠️ — `config.py:148` ships `APPLY_NETWORK_PROFILE = True`; SRS requires default-disabled → **T15**.

### 1.11 Heartbeat & telemetry — FR-HB

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-HB-01](SOFTWARE_REQUIREMENTS.md#fr-hb-01)..[04](SOFTWARE_REQUIREMENTS.md#fr-hb-04) | ✅ | `provisioning/heartbeat.py:53-59,77-97`; `observability/events.py:43-73` |
| [FR-HB-05](SOFTWARE_REQUIREMENTS.md#fr-hb-05) | ⚠️ | `events.ack(len(pending))` by **count** (`heartbeat.py:119`, `events.py:86-97`) → **T5** |
| [FR-HB-06](SOFTWARE_REQUIREMENTS.md#fr-hb-06)..[09](SOFTWARE_REQUIREMENTS.md#fr-hb-09) | ✅ | uuid4 `:62`; cap 200 `:41-44`; backoff `heartbeat.py:134`; shutdown flush `binding.py:148-183` |
| [FR-HB-10](SOFTWARE_REQUIREMENTS.md#fr-hb-10) <a id="fr-hb-10-row"></a> | ❌ | On 410 the code deletes the identity and shows a message (`binding.py:125-141`, `web_window.py:391-401`). **No** `device_revoked`, **no** flush, **no** DB purge, **no** deny-all, **no** self-restart → **T6** |

### 1.12 Logging & storage — FR-LOG

[FR-LOG-01](SOFTWARE_REQUIREMENTS.md#fr-log-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-log-02)/[03](SOFTWARE_REQUIREMENTS.md#fr-log-03)/[05](SOFTWARE_REQUIREMENTS.md#fr-log-05) ✅ — `observability/logging_setup.py:74-151`, `storage_monitor.py:44-98`.
[FR-LOG-04](SOFTWARE_REQUIREMENTS.md#fr-log-04) ⚠️ — no secret is *logged*, but the Wi-Fi password is passed on the `nmcli` command line (`provisioning/network.py:101`), exposing it in the process list → **T15**.

### 1.13 Camera — FR-CAM

All ✅. `hardware/camera_preview.py:24,152-177`; `gui_web/frame_server.py:63-142`; JS stall watchdog `web_window.py:94-112`; extraction pause/resume `:637,651-653`.

### 1.14 User interface — FR-UI

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-UI-01](SOFTWARE_REQUIREMENTS.md#fr-ui-01) | ⚠️ | All screens exist except the **IN/OUT selection screen**; `demo_ui/index.html:75-79` has only a cosmetic toggle → **T8** |
| FR-UI-02 | ➖ | Deprecated rev 1.2 |
| [FR-UI-03](SOFTWARE_REQUIREMENTS.md#fr-ui-03) | ✅ | `web_window.py:673-678` host overrides the JS default |
| [FR-UI-04](SOFTWARE_REQUIREMENTS.md#fr-ui-04)..[08](SOFTWARE_REQUIREMENTS.md#fr-ui-08) | ✅ | `web_window.py:567-573,679-692,604-609`; generic failure text `demo_ui/app.js:120-125` |
| [FR-UI-09](SOFTWARE_REQUIREMENTS.md#fr-ui-09) | ⚠️ | Keypad exists (`demo_ui/index.html:91-108`, hardcoded `1234` at `app.js:103` **and** `web_window.py:232-238`); no auth effect and the entry button is commented out, but no production flag → **T18** |
| FR-UI-10 | ➖ | Deprecated rev 1.2 |
| [FR-UI-11](SOFTWARE_REQUIREMENTS.md#fr-ui-11) | ✅ | `config.py:47,174-175`; `web_window.py:482-530` |
| [FR-UI-12](SOFTWARE_REQUIREMENTS.md#fr-ui-12) | ❌ | No "temporarily unavailable" state; backoff reuses the generic failure → **T9** |

### 1.15 Server contract — FR-API

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-API-01](SOFTWARE_REQUIREMENTS.md#fr-api-01)/[02](SOFTWARE_REQUIREMENTS.md#fr-api-02)/[03](SOFTWARE_REQUIREMENTS.md#fr-api-03)/[05](SOFTWARE_REQUIREMENTS.md#fr-api-05)/[06](SOFTWARE_REQUIREMENTS.md#fr-api-06) | ✅ | `provisioning/client.py:44-50,75,124-128`; `server/main.py:279,352,385`; `server/signing.py:58-83`; no `verify=False` in repo code |
| [FR-API-04](SOFTWARE_REQUIREMENTS.md#fr-api-04) | ⚠️ | Timeouts ✅ but only network-vs-410-vs-other branching; 4xx and 5xx treated alike (`client.py:139-142`) → **T13** |
| [FR-API-07](SOFTWARE_REQUIREMENTS.md#fr-api-07) | ⚠️ | Single-use + actionable reasons ✅ (`server/main.py:291-299,329-333`), but re-registration **creates a new device row** (`:301-326`); the old binding lingers → **T14** |
| [FR-API-09](SOFTWARE_REQUIREMENTS.md#fr-api-09)/[10](SOFTWARE_REQUIREMENTS.md#fr-api-10)/[11](SOFTWARE_REQUIREMENTS.md#fr-api-11)/[14](SOFTWARE_REQUIREMENTS.md#fr-api-14) | ✅ | `heartbeat.py:92-119`; `server/main.py:84-118,399-412`; `db/remote_provider.py:134-145` |
| FR-API-12 | ➖ | Deprecated rev 1.2 |
| [FR-API-13](SOFTWARE_REQUIREMENTS.md#fr-api-13) | ⚠️ | Per-device scoping exists (`server/main.py:352-364`, `server/user_store.py:44-48`) but every new device is seeded from the **same default template** (`main.py:335-339`) — not real door scoping → **T14** |
| [FR-API-15](SOFTWARE_REQUIREMENTS.md#fr-api-15) <a id="fr-api-15-row"></a> | ❌ | No attendance concept server-side; the generic events table would accept it but nothing emits or journals it → **T8** |

### 1.16 Data model — FR-DATA

| ID | Status | Evidence / gap |
|---|---|---|
| [FR-DATA-01](SOFTWARE_REQUIREMENTS.md#fr-data-01) | ⚠️ | Faceprint validity checked (`remote_provider.py:29-32`), but **no `active` field** exists → **T3** |
| [FR-DATA-02](SOFTWARE_REQUIREMENTS.md#fr-data-02) | ⚠️ | Never logged ✅; **not deleted on revocation** — deliberately kept (`web_window.py:394-397`) → **T6** |
| [FR-DATA-03](SOFTWARE_REQUIREMENTS.md#fr-data-03) | ⚠️ | Atomic + deleted on revocation ✅; **no `0600`** → **T10** |
| [FR-DATA-04](SOFTWARE_REQUIREMENTS.md#fr-data-04) | ✅ | `provisioning/identity.py:78-83` |
| [FR-DATA-05](SOFTWARE_REQUIREMENTS.md#fr-data-05) | ✅ | `observability/events.py:60-67` |
| [FR-DATA-06](SOFTWARE_REQUIREMENTS.md#fr-data-06) | ❌ | Events carry `user=<name>` and `card_id` (`auth_service.py:199,276`) → **T3** |
| [FR-DATA-07](SOFTWARE_REQUIREMENTS.md#fr-data-07) | ❌ | No `active` field device- or server-side → **T3** |

### 1.17 Non-functional — NFR

Mostly ✅ (threading `NFR-01`, offline `NFR-05..08`, atomic writes `NFR-09/10`, shutdown watchdog `NFR-11` at `main_web.py:160-186`, security `NFR-12/13/15/16`, maintainability `NFR-20/22`).

| ID | Status | Gap |
|---|---|---|
| [NFR-14](SOFTWARE_REQUIREMENTS.md#nfr-14) | ⚠️ | Device still keeps a nonce set; rev 1.2 puts replay protection server-side → **T6** |
| [NFR-19](SOFTWARE_REQUIREMENTS.md#nfr-19) | ⚠️ | Hardware/business layers are shared, but the **session state machine is duplicated** between `gui_web/web_window.py` (736 lines) and `gui_qt/main_window_qt.py` (642 lines) → **T1** |
| [NFR-21](SOFTWARE_REQUIREMENTS.md#nfr-21) | ⚠️ | `docs/rsid-host-mode.service` points at a nonexistent `host_mode_cli.py`; `face-guard.service` launches `main_qt.py`, not `main_web.py` → **T17** |

### 1.18 Architecture finding (drives the ordering)

The single most consequential result of the audit:

> **The session/UI state machine exists twice.** `gui_web/web_window.py:558-692` and
> `gui_qt/main_window_qt.py:449-484` independently implement `start_session`,
> `_end_session`, `_on_card_detected`, `_on_auth_complete`, init-mode and metadata
> logic. Only the layers *below* the GUI (`HostModeService`, `PreviewController`,
> `BindingManager`, `QRScanner`, `config`) are shared.

Consequence: implementing `card_only`, `time_registry` or the session fixes
directly would mean writing each behaviour **twice** and testing it twice.
[T1](#t1) removes that duplication before any behavioural work starts.

Secondary finding: `face_auth/auth_service.py` opens the relay itself
(`:29-37,198,275`), so the biometric layer currently *is* the access-decision
layer. [T2](#t2) separates them, which [T7](#t7) and [T8](#t8) then depend on.

---

## 2. Task list

Ordered so architectural change lands first. **Each task assumes its
dependencies are done**; following the order avoids reworking earlier tasks.

| # | Task | Scope | Depends on | Requirements |
|---|---|---|---|---|
| [T1](#t1) | Extract shared `SessionController` | device | — | NFR-19, FR-SESS-* |
| [T2](#t2) | Separate access decision from biometrics | device | T1 | FR-FACE-04, FR-OUT-06 |
| [T3](#t3) | User-record schema v2 | device+server | — | FR-DB-01, FR-DATA-01/06/07 |
| [T4](#t4) | `device_mode` / `face_policy` plumbing | device+server | T1 | FR-MODE-01 |
| [T5](#t5) | Event pipeline: ack-by-id + durable queue | device | — | FR-HB-05, FR-MODE-10 |
| [T6](#t6) | Fail-secure revocation | device | T5 | FR-HB-10, FR-DATA-02, FR-PROV-03 |
| [T7](#t7) | `card_only` mode | device | T1,T2,T4 | FR-MODE-03 |
| [T8](#t8) | `time_registry` mode | device+server | T1,T2,T3,T4,T5 | FR-MODE-06..11, FR-API-15 |
| [T9](#t9) | Session edge cases + unavailable screen | device | T1 | FR-SESS-03, FR-CARD-04, FR-FACE-06, FR-UI-12 |
| [T10](#t10) | Identity file `0600` | device | — | FR-PROV-09, FR-DATA-03 |
| [T11](#t11) | Validate `command` field | device | — | FR-PROV-06 |
| [T12](#t12) | Log score/threshold | device | — | FR-FACE-03 |
| [T13](#t13) | HTTP error classification | device | — | FR-API-04 |
| [T14](#t14) | Server rebinding + door scoping | server | T3 | FR-API-07, FR-API-13 |
| [T15](#t15) | Network profile defaults + password handling | device | — | FR-NET-03, FR-LOG-04 |
| [T16](#t16) | Init mode unconditional | device | T1 | FR-PROV-01 |
| [T17](#t17) | systemd unit | ops | T6 | NFR-21 |
| [T18](#t18) | Retire keypad demo path | device | — | FR-UI-09 |

### Phase A — Architecture

#### <a id="t1"></a>T1. Extract a shared `SessionController` *(scope reduced: web UI only, `gui_qt` frozen)*

**Why first.** Every behavioural task below touches session logic. While it
lives twice, each of those tasks costs double and can drift.

**Do.** Create a UI-agnostic session controller (suggested
`session/controller.py`) owning: session lifecycle, retry/timeout timers,
card and tap triggers, result holds, auth dispatch to a worker thread, and the
idle-screen return. Define a narrow view interface
(`show_camera / show_success / show_failure / show_idle / show_overlay`) that
`gui_web` implements. Reduce `web_window.py` to a view adapter + platform
glue. **`gui_qt` is frozen by decision** (2026-08-26): it keeps its current
duplicated logic, receives no new features, and is not ported.

**Files.** New `session/`; `gui_web/web_window.py:558-692`.

**Accept.** No session/timer logic remains in `gui_web/web_window.py`; the web
GUI drives the controller; existing behaviour unchanged (FR-SESS-01..08 still
pass); `web_window.py` materially smaller. `gui_qt` untouched.

---

#### <a id="t2"></a>T2. Separate the access decision from biometrics

**Do.** Remove `_open_access_point()` from `face_auth/auth_service.py`; the
service returns a match result only. The controller (T1) makes the access
decision, calls the Access Output Service, and emits `access_granted` **only
after** a successful pulse; on failure emit the new `access_output_failed`.

**Files.** `face_auth/auth_service.py:29-37,198-199,275`; `hardware/relay_api.py`;
`session/controller.py`.

**Accept.** `auth_service` contains no relay import or call; `access_granted`
never precedes actuation; relay failure after approval yields
`access_output_failed`, distinct from `access_denied`.

---

#### <a id="t3"></a>T3. User-record schema v2

**Do.** Device and server adopt `{user_id, name, active, permission_level,
faceprints: []}`. Server emits the new shape; device validates it; a cache file
in the old shape is **discarded at startup** and repopulated by the next sync
(no migration code). Events switch from `name`/`card_id` to `user_id`.
Honour `active: false` as "never authorises".

**Files.** `server/user_store.py`, `server/main.py:335-339,352-364`;
`db/remote_provider.py:29-32,134-150`, `db/local_provider.py:24-42`,
`db/user_database.py`; `face_auth/auth_service.py:199,276`.

**Accept.** Round-trip server→device→match works on v2; a v1 file on disk is
discarded without a crash; an `active: false` user is denied; no event carries
a cardholder name or raw card id.

---

#### <a id="t4"></a>T4. `device_mode` / `face_policy` plumbing

**Do.** Add both to the register response, the QR envelope and the identity
file; resolve at runtime as *server value → `config.py` fallback*. Introduce
`DEVICE_MODE`, `FACE_POLICY`, `DIRECTION_SELECT_TIMEOUT_SEC` in `config.py`.
Mark `AUTH_ONLY_ON_CARD` deprecated and route its remaining callers through the
new mode.

**Files.** `server/models.py:104-111`, `server/main.py:227-276,301-326`;
`provisioning/identity.py:40-53`, `provisioning/client.py`; `config.py:33`;
`session/controller.py`.

**Accept.** A device provisioned `card_and_face` runs it regardless of local
config; with no server value the config fallback applies; identity file
round-trips both fields.

---

#### <a id="t5"></a>T5. Event pipeline — ack by id, durable attendance queue

**Do.** (a) Change `ack(count)` to `ack(event_ids)` so eviction during an
in-flight beat cannot drop undelivered events. (b) Add an on-disk queue for
durable events; attendance uses it, telemetry keeps the bounded ring.

**Files.** `observability/events.py:41-97`; `provisioning/heartbeat.py:92-119`;
new `observability/durable_queue.py`.

**Accept.** Events emitted *during* an in-flight heartbeat are never acked;
attendance events survive a process kill and are delivered after restart;
telemetry still capped at 200 drop-oldest.

---

### Phase B — Behaviours

#### <a id="t6"></a>T6. Fail-secure revocation

**Do.** On HTTP 410, in order: emit `device_revoked` + best-effort flush →
stop heartbeat → delete identity → **purge the user DB incl. faceprints** →
deny all → orderly `sys.exit` so systemd restarts into init mode. Also delete
the in-process nonce set (replay protection is server-side from rev 1.2).

**Files.** `provisioning/binding.py:125-141`; `provisioning/heartbeat.py:101-111`;
`db/user_database.py`; `qr_scanner/qr_scanner.py:114-116,173-178`;
`gui_web/web_window.py:391-401`; `main_web.py`.

**Accept.** After a simulated 410: `device_revoked` reaches the server, the
user DB file is empty, a card tap is denied, the process exits and comes back
in init mode. No nonce set remains in `qr_scanner`.

---

#### <a id="t7"></a>T7. `card_only` mode

**Do.** In `card_only`, a card in the local DB opens the relay immediately —
no session, no preview, no biometric call. Path: `CardReader → SessionController
→ AccessOutput`.

**Files.** `session/controller.py`; `config.py`.

**Accept.** With `card_only`, a valid card opens the door with the camera
never powered; an unknown card is still rejected pre-camera; `card_and_face`
is unaffected.

---

#### <a id="t8"></a>T8. `time_registry` mode

**Do.** Device: IN/OUT selection screen replacing the screensaver; direction
latched until the card tap or `DIRECTION_SELECT_TIMEOUT_SEC`; face policy
`none` | `verify`; emit `attendance_event {user_id, direction, ts}` through the
durable queue (T5); **relay never actuated**. Server: accept and persist
attendance events, expose a per-user journal.

**Files.** `demo_ui/index.html:75-79`, `demo_ui/app.js:249-275`;
`session/controller.py`; `observability/durable_queue.py`; `server/main.py`,
`server/db.py`, `server/models.py`.

**Accept.** Selecting IN then tapping a registered card records exactly one
attendance event with the right direction and **no relay pulse**; timeout
returns to idle recording nothing; a mismatch under `verify` records nothing;
events survive a restart and appear in the server journal.

---

#### <a id="t9"></a>T9. Session edge cases and the unavailable screen

**Do.** (a) A *different* card during a result hold pre-empts it and starts a
new session; the cooldown stays a per-card debounce. (b) Card path respects the
init-mode guard. (c) Apply the 20 s biometric backoff on the **card** path too
and surface it as a distinct "temporarily unavailable" screen.

**Files.** `session/controller.py`; `face_auth/auth_service.py:229-232,332-333`;
`demo_ui/` (new screen); `gui_web/web_window.py:579`.

**Accept.** Card B during card A's failure hold starts a session for B; the
same card within 2 s is still ignored; a card tap during backoff shows the
unavailable screen (visually distinct from a mismatch) and never opens the door;
no session can start during init mode.

---

### Phase C — Isolated fixes (no refactoring)

#### <a id="t10"></a>T10. Identity file permissions ✅ *(implemented 2026-08-26, batch B0 — pending device validation)*
`os.chmod(path, 0o600)` inside the atomic write in `provisioning/identity.py:89-96`.
**Accept.** File mode is `0600` after registration and after rewrite.
**Done.** Temp file now created `0o600` via `os.open` before content is written; mode re-asserted on the final path after `os.replace`. Verified off-Pi: fresh save → `0600`; rewrite of a pre-existing `0644` file → `0600`.

#### <a id="t11"></a>T11. Validate the QR `command` ✅ *(implemented 2026-08-26, batch B0 — pending device validation)*
Reject envelopes whose `command != "provision_device"`, classified benign, emitting `qr_rejected` (`qr_scanner/qr_scanner.py`).
**Accept.** A signed envelope with any other command is rejected and logged.
**Done.** `EXPECTED_COMMAND` check added in `_verify` after the schema check, warning-level (benign per FR-PROV-05). Verified off-Pi with re-signed envelopes: good command accepted; `factory_reset` and missing command rejected; all 62 server tests (incl. QR device-compat round-trips) pass.

#### <a id="t12"></a>T12. Log score and threshold ✅ *(implemented 2026-08-26, batch B0 — pending device validation)*
Record the score and `CUSTOM_THRESHOLD` on every decision (`face_auth/auth_service.py:197,266-268`).
**Accept.** Grant and denial log lines both show score vs threshold.
**Done.** One decision log line per path: `1:1 decision: card=… sdk_success=… score=… threshold=… -> GRANT/DENY` and `1:N decision: …`. Compile-checked only (rsid_py unavailable off-Pi) — device validation confirms.

#### <a id="t13"></a>T13. HTTP error classification
Distinguish connect error / timeout / permanent 4xx / transient 5xx in `provisioning/client.py:80-92,130-142` and `db/remote_provider.py:65-73`; only transient classes back off.
**Accept.** A 400 and a 503 are logged and retried differently; 410 keeps its dedicated path.

#### <a id="t14"></a>T14. Server rebinding and real door scoping
Re-registration **replaces** the prior device row rather than creating a second (`server/main.py:301-326`); stop seeding every device from `default_user_database.json` (`:335-339`) — scope users to the door.
**Accept.** Rebinding the same physical terminal leaves exactly one active row; a new device starts with the users of *its* door, not a template.

#### <a id="t15"></a>T15. Network profile defaults and password handling
Ship `APPLY_NETWORK_PROFILE = False` (`config.py:148`); pass the Wi-Fi password to `nmcli` via stdin/file instead of argv (`provisioning/network.py:101`).
**Accept.** A dev machine is never reconfigured by default; the password is absent from the process list.

#### <a id="t16"></a>T16. Init mode on every start
Enter init mode unconditionally; `INIT_MODE_ENABLED` becomes duration-only (0 = skip) (`gui_web/web_window.py:549-552`, `gui_qt/main_window_qt.py:280`).
**Accept.** An already-bound terminal still scans at startup, so a technician can re-provision without a reset.

#### <a id="t17"></a>T17. systemd unit
One maintained unit running `main_web.py` with `Restart=always`; delete or fix `docs/rsid-host-mode.service`.
**Accept.** `systemctl start` launches the web UI; a T6 revocation exit is restarted automatically.

#### <a id="t18"></a>T18. Retire the keypad demo path
Remove the hardcoded `1234` from `demo_ui/app.js:103` and `gui_web/web_window.py:232-238`, or gate the whole path behind an explicitly non-production flag.
**Accept.** No credential constant ships in production assets; no code path grants on PIN entry.

---

## 3. Batches and device validation

Delivery model: each batch is a small, independently revertable change set.
After every batch the owner validates on the real device using the checklist
below; the next batch starts only after sign-off. The existing server test
suite (`server/tests/`, 62 tests) must stay green after every batch — run
with `APPLY_NETWORK_PROFILE = False` on dev machines without `nmcli`
([D17](SOFTWARE_REQUIREMENTS.md#d17)).

| Batch | Content | Status |
|---|---|---|
| **B0** | [T10](#t10) + [T11](#t11) + [T12](#t12) | **Implemented — awaiting device validation** |
| B1 | [T1](#t1)a: `session/controller.py`, web UI ported | pending |
| B2 | [T1](#t1)b: freeze notice in `gui_qt` + [T16](#t16) | pending |
| B3 | [T2](#t2) decision separation | pending |
| B4 | [T5](#t5)a ack-by-`event_id` | pending |
| B5 | [T6](#t6) fail-secure revocation | pending |
| B6 | [T3](#t3) schema v2 (server, then device) | pending |
| B7 | [T4](#t4) `device_mode` / `face_policy` | pending |
| B8 | [T7](#t7) `card_only` | pending |
| B9 | [T9](#t9) pre-emption + backoff + unavailable screen | pending |
| B10 | [T5](#t5)b durable attendance queue | pending |
| B11 | [T8](#t8) server half (attendance intake + journal) | pending |
| B12 | [T8](#t8) device half (IN/OUT flow) | pending |
| B13 | [T13](#t13) + [T14](#t14) + [T15](#t15) + [T17](#t17) + [T18](#t18) | pending |

### B0 device checklist

1. **T10** — provision (or re-provision) the terminal, then:
   `ls -l device_identity.json` → must show `-rw-------`.
2. **T11** — scan a QR with a non-`provision_device` command → rejected with
   log line `QR rejected (unsupported command …)` at WARNING; a normal QR
   still binds. To mint a wrong-command QR, use `other/qr_code_poc/gen_qr_code.py`
   with the command edited, or skip this step (covered by automated tests
   off-device).
3. **T12** — perform one grant and one deny → the log shows
   `1:1 decision: card=… sdk_success=… score=… threshold=400 -> GRANT` and
   `… -> DENY` respectively.

*(Per-batch checklists for B1+ are added when each batch is implemented.)*
