# RSID Face Guard — Functional Test Plan (Target / Final State)

| Item | Detail |
|---|---|
| Document ID | TEST-FG-001 |
| Revision | 1.0 |
| Basis | `SOFTWARE_REQUIREMENTS.md` rev 1.4, `IMPLEMENTATION_PLAN.md` rev 1.2 |
| Scope | **Target state** — assumes all open tasks (T3b, T4, T5b, T7–T9, T13–T15, T18–T20) are delivered. Every flow below describes final intended behaviour, not the current build. |
| Test basis | Device application + reference server (`server/`) |

**How to read this.** Each flow has a precondition, numbered steps, an
expected result, the requirements it verifies, and a pass/fail line. Flows
are grouped by service area, matching the SRS structure, so a failure maps
straight back to an SRS/plan section. A final traceability table cross-checks
coverage.

---

## 1. Test Environment & Preconditions

| Item | Setup |
|---|---|
| Hardware | Raspberry Pi 5, RealSense ID F45x, GWIOT card reader, relay + strike wired, 720×720 display |
| Server | Reference `server/` reachable over HTTPS, seeded with a test customer/site/door |
| Config | `APPLY_NETWORK_PROFILE=False` unless a network-specific flow states otherwise ([T15](#)) |
| Test cards | ≥3 registered cards (different users), ≥1 unregistered card |
| Test users | ≥1 user with 0 faceprints (for `card_only`), ≥1 with 2 faceprints, ≥1 `active:false` |
| Regression gate | `server/tests/` (66), `session/tests/` (27), `db/test_remote_sync.py` (7), `provisioning/tests/test_revocation.py` (4), `db/tests/test_revocation_wipe.py` (2) — all green before device testing starts |

Unless stated otherwise, every flow is run twice: once with the server
reachable, once with the terminal offline (network cable/Wi-Fi pulled),
per **FR-STATE-04/05**: the access decision must be identical in both cases.

---

## 2. Provisioning & QR Trust

### 2.1 Fresh device — init mode entry
**Pre:** Unbound device, fresh boot.
**Steps:** Power on; observe screen.
**Expected:** `init_mode` entered unconditionally (`init_mode_entered` emitted); camera preview live; scans for `INIT_MODE_DURATION_SEC` (8 s); with no QR presented, falls through to `unbound` (dev) with no relay/session activity.
**Verifies:** FR-PROV-01, FR-STATE (entry state).
**Pass/Fail:** Pass if init mode runs on every boot regardless of binding state.

### 2.2 Valid QR — bind
**Pre:** Unbound device in init mode; server issues a signed QR (`device_mode: card_and_face`).
**Steps:** Present QR to camera within the scan window.
**Expected:** All 5 offline checks pass silently → `qr_accepted` → network profile step (no-op if `local`) → `POST /devices/register` on a background thread → success banner shown ≥3 s → identity persisted (`0600`, atomic, gitignored) → terminal enters `idle`.
**Verifies:** FR-PROV-01/02/04/06/07/08/09, FR-API-06/07.
**Pass/Fail:** Pass if binding completes without blocking the UI thread and the identity file has mode `-rw-------`.

### 2.3 Invalid QR — each rejection class
**Pre:** Bound or unbound device in init mode.
**Steps:** Present, in separate runs: (a) expired QR, (b) QR signed by an untrusted key, (c) tampered payload (bad signature), (d) QR with `command != provision_device`, (e) a **replayed** QR (same token presented twice).
**Expected:** (a)–(d) rejected locally with a classified `qr_rejected` log line at the correct level (SECURITY for a/b/c, WARNING for benign command mismatch); scanning continues. (e) passes local checks both times (no local nonce state per T19) but the **second** registration attempt is refused **server-side** with an actionable reason.
**Verifies:** FR-PROV-03/04/05/06, T19 (nonce removed from device).
**Pass/Fail:** Pass if replay protection is enforced by the server, not the device, and every rejection is logged at the correct level.

### 2.4 Re-scan on an already-bound terminal
**Pre:** Terminal already bound to Door A.
**Steps:** Present a fresh, valid QR for Door B.
**Expected:** Binding is **replaced**, not duplicated; server shows exactly one active row for this terminal, now scoped to Door B; local user DB re-syncs immediately to Door B's users.
**Verifies:** FR-PROV-10, FR-API-08, T14 (server rebinding replaces, no orphaned rows).
**Pass/Fail:** Pass if the server has one row per physical terminal, never two.

### 2.5 Wi-Fi network profile
**Pre:** `APPLY_NETWORK_PROFILE=True`; unbound device with no Ethernet.
**Steps:** Present a QR carrying `network_profile: {mode: wifi, ssid, password}`.
**Expected:** Terminal joins via NetworkManager before registering, within a bounded timeout; on failure, registration fails with a clear join-failure message. The Wi-Fi password never appears in the process list (`ps aux` during join) — passed via stdin/file, not argv.
**Verifies:** FR-NET-01/02/04/05, T15.
**Pass/Fail:** Pass if `ps aux | grep nmcli` never shows the plaintext password.

### 2.6 Network profile default-off
**Pre:** Fresh install, default config, no override.
**Steps:** Inspect `config.APPLY_NETWORK_PROFILE`.
**Expected:** `False` out of the box; a `local`-profile or disabled-feature QR is a no-op regardless.
**Verifies:** FR-NET-03, T15.
**Pass/Fail:** Pass if a dev machine is never reconfigured without an explicit opt-in.

---

## 3. Operating Modes

### 3.1 `card_only` — grant
**Pre:** Door provisioned `device_mode: card_only`; registered card with **zero** faceprints (valid per FR-DATA-01).
**Steps:** Tap the card.
**Expected:** Relay opens immediately; camera is **never powered**; `access_granted` references the correct `user_id`; no session/preview events fire.
**Verifies:** FR-MODE-01/02/03, FR-DATA-01, T7, T3b (empty faceprint list).
**Pass/Fail:** Pass if camera power/frame events show zero activity for the whole flow.

### 3.2 `card_only` — unregistered card
**Pre:** `card_only` mode; unregistered card.
**Steps:** Tap the card.
**Expected:** Rejected on DB lookup alone, before any camera activity; failure screen for `FAIL_DURATION_MS`; returns to idle.
**Verifies:** FR-MODE-02, BR-02.
**Pass/Fail:** Pass if rejection is camera-free and reason logged as `card_unregistered`.

### 3.3 `card_and_face` — grant
**Pre:** `card_and_face` mode; registered card + matching live face.
**Steps:** Tap card, present face.
**Expected:** Session starts, camera on; 1:1 match against **only** the cardholder's faceprints; on match: relay pulses, *then* "Welcome, `<name>`", event order `auth_matched → relay_opened → access_granted`; returns to idle automatically.
**Verifies:** FR-MODE-04, FR-FACE-01/02/04, FR-OUT-02/03, FR-SESS-06/08.
**Pass/Fail:** Pass if `access_granted` never precedes `relay_opened`.

### 3.4 `card_and_face` — mismatch
**Pre:** Registered card, wrong or absent face.
**Steps:** Tap card, present a different face (or none).
**Expected:** One denial shown for `FAIL_DURATION_MS` (no retry loop — card sessions end on first mismatch per BR-05); no relay pulse; `access_denied` with reason `face_mismatch` or `face_extraction_failed`; returns to idle.
**Verifies:** FR-SESS-04, FR-FACE-05, FR-UI-05, BR-05.
**Pass/Fail:** Pass if exactly one failure screen appears — not repeated attempts.

### 3.5 `card_and_face` — different-card pre-emption
**Pre:** Card A fails a session and is in its result-screen hold.
**Steps:** During the hold, tap Card B (a different, registered card).
**Expected:** Card B's tap pre-empts the hold and starts a new session immediately; Card A within its 2 s cooldown is still ignored if tapped again; no session can start while `init_mode` is active.
**Verifies:** FR-SESS-03, FR-CARD-04, BR-04, T9(1)(2).
**Pass/Fail:** Pass if Card B's session starts without waiting for A's hold to finish naturally.

### 3.6 Biometric backoff — "temporarily unavailable"
**Pre:** Force an SDK/hardware exception (disconnect the RealSense device or trigger a fault).
**Steps:** Tap a registered card in `card_and_face` during the 20 s backoff window.
**Expected:** A visually distinct "temporarily unavailable, try again shortly" screen for `FAIL_DURATION_MS`; **no relay pulse**; `hardware_error` event emitted; background reconnect attempted; internal cause never shown to the user.
**Verifies:** FR-FACE-06, FR-UI-12, FR-OUT-06 (fail-secure), T9(3)(4).
**Pass/Fail:** Pass if this screen is distinguishable from a mismatch screen (different message/visual) in a side-by-side comparison.

### 3.7 `time_registry` — IN/OUT with face policy `none`
**Pre:** Door provisioned `device_mode: time_registry`, `face_policy: none`.
**Steps:** From idle (IN/OUT screen), tap "IN", then tap a registered card.
**Expected:** Direction latches on tap; card alone registers the event — no camera, no session; `attendance_event {user_id, direction: in, ts}` emitted; **no relay actuation**; returns to the IN/OUT screen (not screensaver).
**Verifies:** FR-MODE-06/07/08/09, FR-UI-03, A6.
**Pass/Fail:** Pass if the relay never fires and the correct direction is recorded.

### 3.8 `time_registry` — face policy `verify`
**Pre:** `face_policy: verify`.
**Steps:** Select "OUT", tap card, present matching face.
**Expected:** Session runs exactly as `card_and_face` 1:1 verification; on match, `attendance_event {direction: out}` emitted, still no relay; on mismatch, nothing is registered.
**Verifies:** FR-MODE-07, table in §4.3 of the SRS.
**Pass/Fail:** Pass if a mismatch produces zero attendance events.

### 3.9 `time_registry` — selection timeout
**Pre:** `time_registry` mode, idle at the IN/OUT screen.
**Steps:** Select a direction, then wait past `DIRECTION_SELECT_TIMEOUT_SEC` (15 s) without tapping a card.
**Expected:** Screen reverts to the IN/OUT selection screen; nothing recorded.
**Verifies:** FR-MODE-06.
**Pass/Fail:** Pass if no `attendance_event` fires and no card read is consumed late.

### 3.10 Attendance durability
**Pre:** `time_registry` mode; server unreachable (or kill the process mid-write).
**Steps:** Record an IN event while offline; kill the process before the next heartbeat; restart the process and restore connectivity.
**Expected:** The event survives the process kill (on-disk durable queue, not the volatile ring), is delivered on the next heartbeat, and appears exactly once in the server's per-user journal.
**Verifies:** FR-MODE-10, T5b, FR-API-15.
**Pass/Fail:** Pass if a `kill -9` between record and delivery never loses the event.

---

## 4. Session, Card Reader & Access Output

### 4.1 Card cooldown vs. session lock
**Pre:** `card_and_face` mode, idle.
**Steps:** Tap the same registered card twice within 2 s; then tap it once, wait for the session to start, and tap it again mid-session.
**Expected:** Both repeats are ignored (BR-04); no duplicate sessions.
**Verifies:** FR-CARD-03/04.

### 4.2 Face-only demo mode retry cadence
**Pre:** `REQUIRE_CARD_TO_START_SESSION=False` (demo config only — not a production door mode).
**Steps:** Tap the idle screen to start a 1:N session; present no face.
**Expected:** Retries every `AUTH_RETRY_INTERVAL_SEC` (3 s) until `AUTH_SESSION_TIMEOUT_SEC` (30 s), then falls back to idle **silently** (no failure screen per FR-UI-06).
**Verifies:** FR-SESS-04/05, FR-MODE-05, FR-UI-06.

### 4.3 Relay failure
**Pre:** Strike physically disconnected or relay fault forced; `card_and_face`, matching face.
**Steps:** Tap card, present matching face.
**Expected:** Failure screen shown, **not** "Welcome"; `access_output_failed` emitted, distinct from `access_denied`; no `access_granted`.
**Verifies:** FR-OUT-05/06, BR-06.

### 4.4 Relay-off demo
**Pre:** `RUN_WITH_RELAY=False`.
**Steps:** Grant flow as in 3.3.
**Expected:** "Welcome" and `access_granted` fire with no physical pulse and no error.
**Verifies:** FR-OUT-02/05.

### 4.5 Card reader fault tolerance
**Pre:** Force a reader read exception (unplug reader mid-poll, if simulable).
**Steps:** Observe application state after the fault.
**Expected:** `hardware_error` logged, monitor thread survives, retries after a delay; application does not crash.
**Verifies:** FR-CARD-06.

---

## 5. User Database & Sync

### 5.1 Periodic refresh
**Pre:** `remote` mode, bound.
**Steps:** Add/remove a user server-side; wait one `DB_SYNC_INTERVAL_SEC` (600 s), or trigger sync manually if a test hook exists.
**Expected:** New user becomes authorisable; removed user is dropped and `db_users_revoked` emitted; access decisions never touch the network directly (verify via traffic capture during a card tap).
**Verifies:** FR-DB-03/04/08.

### 5.2 Malformed payload handling
**Pre:** Server returns one malformed user record (missing `user_id`) alongside valid ones.
**Steps:** Trigger a sync.
**Expected:** Malformed record skipped and counted (`db_sync_invalid_record`, `db_sync_skipped_entries`); valid records still applied; previous cache retained if the **whole** payload is invalid.
**Verifies:** FR-DB-01/05/06, FR-API-14.

### 5.3 Faceprints as a list
**Pre:** Server-side user with two faceprints; another with an empty list; another with a legacy single-object shape (if simulable).
**Steps:** Sync, then attempt authentication for each.
**Expected:** Two-faceprint user matches on **either** faceprint; empty-list user is valid only in `card_only`; legacy single-object shape is coerced or rejected — never crashes the sync.
**Verifies:** FR-DATA-01, T3b.

### 5.4 Inactive user
**Pre:** Server-side user with `active: false`.
**Steps:** Sync; tap that user's card.
**Expected:** Denied with reason `user_inactive`; no relay pulse; record retained locally (not deleted) but non-authorising.
**Verifies:** FR-DB-02, event catalogue.

### 5.5 No network dependency for door decisions
**Pre:** Server unreachable throughout.
**Steps:** Run flows 3.1–3.9 fully offline.
**Expected:** Identical behaviour to the online case in every mode; only telemetry/DB freshness differs.
**Verifies:** FR-STATE-04/05/09/11.

---

## 6. Revocation

### 6.1 Fail-secure revocation sequence
**Pre:** Bound, online terminal with cached users and at least one buffered event.
**Steps:** Operator removes the device server-side; wait for the next heartbeat.
**Expected, in strict order:**
1. `device_revoked` emitted and flushed **while still bound** (visible server-side).
2. Heartbeat and sync stop.
3. Identity file deleted.
4. Local user DB purged, including all faceprints.
5. All access denied from this point.
6. Terminal performs the resolved restart behaviour — see 6.2.

**Verifies:** FR-HB-10 steps 1–5, FR-DATA-02, BR-07.

### 6.2 Restart semantics (D21 resolved)
**Pre:** Following 6.1.
**Steps:** Observe whether the terminal restarts the process or resets in-process.
**Expected:** Matches whichever option T20 settled on:
- **If (a) spec-follows-code:** terminal returns to `init_mode` in-process, no process restart, and the SRS §3 diagram/NFR-21 wording describes this exactly.
- **If (b) code-follows-spec:** terminal exits and `Restart=always` under systemd brings it back up, camera/SDK re-initialise cleanly.

Whichever was chosen, verify the *other* is **not** silently occurring (no orphaned process, no double restart).
**Verifies:** FR-HB-10 step 6, NFR-21, T20.
**Pass/Fail:** Pass only if code and the (updated) SRS agree — this test should be re-checked against whichever option D21 closed with.

### 6.3 Re-provision after revocation
**Pre:** Revoked terminal, back at `init_mode`.
**Steps:** Present a fresh, valid QR.
**Expected:** Binds without a power cycle; immediately syncs its door's users; grants access normally afterward.
**Verifies:** FR-PROV-10, "Fix" (FR-DB-07 re-arm on bind).

### 6.4 Loss of connectivity is not revocation
**Pre:** Bound, previously-working terminal.
**Steps:** Disconnect network for an extended period (well beyond the heartbeat interval); do not trigger a 410.
**Expected:** No revocation; terminal keeps granting/denying normally from cache; only a 410 response ever triggers 6.1.
**Verifies:** FR-STATE-11.

---

## 7. Heartbeat & Telemetry

### 7.1 Event delivery under load
**Pre:** Bound, online.
**Steps:** Generate a burst of taps so several events queue inside one heartbeat interval.
**Expected:** All events delivered exactly once; pending buffer drains to 0; ack is by `event_id`, not position — verify by forcing a ring eviction mid-beat and confirming no in-flight event is lost.
**Verifies:** FR-HB-04/05/06/07.

### 7.2 Backoff and recovery
**Pre:** Block the server temporarily.
**Steps:** Let several heartbeats fail, then restore connectivity.
**Expected:** Interval backs off exponentially up to a cap while failing, then resets to normal on the first success; events accumulated during the outage are still delivered.
**Verifies:** FR-HB-08.

### 7.3 Shutdown flush
**Pre:** Bound, pending events buffered.
**Steps:** Stop the application via its normal shutdown path.
**Expected:** `device_shutdown` emitted; one best-effort synchronous flush attempted; shutdown is never blocked waiting on the network.
**Verifies:** FR-HB-09.

### 7.4 HTTP error classification
**Pre:** Simulate a 400 and a 503 from the server in separate runs.
**Steps:** Trigger a heartbeat / sync in each condition.
**Expected:** 400 (permanent) and 503 (transient) are logged and retried differently; only the transient class backs off; 410 still takes its dedicated revocation path regardless of this change.
**Verifies:** FR-API-04, T13.

---

## 8. Logging, Storage & Security Hygiene

### 8.1 Secret hygiene
**Pre:** Full provisioning + Wi-Fi-join + normal operation cycle, with debug logging enabled.
**Steps:** Grep all logs and process listings for the bearer token, Wi-Fi password, private key material and raw faceprint vectors.
**Expected:** None appear anywhere in logs; the Wi-Fi password does not appear in `ps aux` output either (see 2.5).
**Verifies:** FR-LOG-04, T15.

### 8.2 Security-relevant events always logged
**Pre:** Any module-level log verbosity, including the most restrictive.
**Steps:** Perform a QR accept/reject, an access decision, a bind, and a revocation.
**Expected:** All four are logged regardless of the verbosity setting.
**Verifies:** FR-LOG-03.

### 8.3 Storage threshold
**Pre:** Fill (or simulate) disk usage past the configured minimum free-space threshold.
**Steps:** Observe logs and the next heartbeat.
**Expected:** One warning + one `storage_low` event per crossing (not repeated every check); the latest free-space reading is attached to every heartbeat.
**Verifies:** FR-LOG-05.

### 8.4 Keypad path retired
**Pre:** Production build.
**Steps:** Attempt the PIN/keypad entry path in the UI, using any code including the formerly-hardcoded one.
**Expected:** Either the path is entirely absent from production assets, or it exists but has zero authorisation effect under any code; the hardcoded constant does not ship.
**Verifies:** FR-UI-09, T18.

---

## 9. UI Screens

| Screen | Trigger | Must show | Must NOT show |
|---|---|---|---|
| Idle / screensaver | Card modes, idle | Screensaver | Camera preview |
| IN/OUT selection | `time_registry`, idle | Direction buttons | — |
| Session / live camera | Session start | Live MJPEG preview | — |
| Success ("Welcome, `<name>`") | Grant | User's name if known | Internal decision detail (score, SDK status) |
| Failure ("not authorized") | Denial | Generic denial | Internal reason (FR-UI-07) |
| Unavailable | Biometric backoff (3.6) | Distinct "try again shortly" message | Anything resembling the mismatch screen |
| Provisioning/status overlay | Init mode / binding | Progress or result, ≥3 s ok / ≥6 s fail | Bare HTTP status codes |

**Test:** Walk every trigger in the table above and confirm the corresponding screen renders with the correct content, and that after a hold the UI returns to the correct *idle* screen for the active mode (screensaver vs. IN/OUT) — never to the live-camera state.
**Verifies:** FR-UI-01/03/04/05/07/12, FR-PROV-08.

---

## 10. Server API Contract

| Test | Steps | Expected |
|---|---|---|
| Register — happy path | Valid token + nonce | 200, full identity payload including `device_mode` |
| Register — reused token | Redeem the same token twice | Second attempt fails with an actionable reason |
| Register — already bound, new token | Fresh token on a bound terminal | Succeeds, replaces prior binding, no duplicate row (T14) |
| Status — bearer required | Omit/garble the bearer token | Rejected, not silently accepted |
| Status — event dedup | Deliver the same `event_id` twice (e.g. retried beat) | Server stores it once |
| Status — 410 handling | Server marks device revoked | Device follows 6.1 exactly |
| Users — door scoping | Two doors, two devices | Each device receives **only** its own door's users, never the other's (FR-API-13, T14) |
| Users — malformed payload | Return one broken record | Terminal skips + counts it, keeps the rest, keeps prior cache if the whole payload is broken |
| TLS | Production config | Certificate validation enabled, no `verify=False` in code |

---

## 11. Non-Functional Test Criteria

| NFR area | Test criteria |
|---|---|
| Performance (NFR-01–04) | UI thread never blocks during a face match or a network call; camera preview lead-in feels immediate on the physical display |
| Availability (NFR-05–08) | All of §5.5 offline behaviour confirmed with the network physically disconnected, not just simulated |
| Reliability (NFR-09–11) | Power-cut simulation (kill mid-write) never leaves a truncated identity file or user DB; shutdown watchdog exits cleanly |
| Security (NFR-12–18) | 8.1 hygiene test, QR trust tests (2.3), atomic + `0600` identity file (2.2) |
| Maintainability (NFR-19/20/22) | `session/tests/` runs with no Qt/browser/`rsid_py` dependency; regression suite (§1 table) stays green after each change |
| Restart discipline (NFR-21) | `face-guard.service` restarts the app automatically after a crash (`Restart=always`); revocation restart matches whatever 6.2 settled on |

---

## 12. Traceability Summary

Every flow above cites the SRS/plan IDs it exercises. Before sign-off, confirm
the union of all "Verifies" lines covers:

- All FR-STATE, FR-MODE, FR-SESS, FR-FACE, FR-CARD, FR-OUT, FR-DB, FR-PROV,
  FR-NET, FR-HB, FR-LOG, FR-CAM, FR-UI, FR-API, FR-DATA IDs that are not
  marked `➖ DEPRECATED` in `IMPLEMENTATION_PLAN.md` §1.1.
- All BR-01..07 and NFR-01..22.
- All twelve open tasks (T3b, T4, T5b, T7, T8, T9, T13, T14, T15, T18, T19,
  T20) have at least one dedicated flow above, not just an incidental mention.

Any FR/NFR/task **not** appearing in a "Verifies" line anywhere in this
document is a gap in this test plan, not in the product — add a flow before
sign-off.

## 13. Exit Criteria

- Every flow in §2–§11 passes on the physical device (not just in
  `pytest`), for both online and offline runs where specified.
- The full regression suite (§1 table) is green immediately before the final
  device pass.
- Every open task's **Accept** criterion from `IMPLEMENTATION_PLAN.md` §2 is
  independently confirmed by a flow in this document.
- No `❌`/`⚠️` rows remain in `IMPLEMENTATION_PLAN.md` §1 reconciliation
  table — i.e., the plan itself reports 100% compliance before this test
  plan is executed as the final acceptance pass.
