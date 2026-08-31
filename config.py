# =====================================================
# General System Configuration
# =====================================================

# "local"  -> only read/write the local JSON file, never contacts the server.
# "remote" -> periodically syncs from the server into the local JSON cache
#             (existing behavior); auth lookups still always read the local
#             cache, the remote fetch just keeps it fresh in the background.
DB_MODE = "remote"


# Database file (local cache / source of truth for face auth)
USER_DB_FILE = "user_database.json" if DB_MODE == "remote" else "user_database_local_dev.json"
# =====================================================
# Hardware Simulation / Mode Flags
# =====================================================

# Which card-reader backend to use:
#   "gwiot_hid"    -> real GWIOT USB HID keyboard-emulation reader (evdev),
#                     the main reader hardware going forward.
#   "wiegand_gpio" -> real Wiegand GPIO reader (lgpio), older hardware.
#   "simulated"    -> fake reader for dev off the Pi.
CARD_READER_BACKEND = "gwiot_hid"

# Kept for backwards compatibility with any code/checks still referencing
# it directly -- derived from CARD_READER_BACKEND, do not set independently.
SIMULATE_CARD_READER = CARD_READER_BACKEND == "simulated"

# Set True on RPi5 with the small 720x720 touch screen
RUN_ON_REAL_SCREEN = True

# Enable card reader monitoring (auto-authenticate when a card is tapped)
AUTH_ONLY_ON_CARD = True

# Session-based authentication: the camera preview stays OFF while idle and
# only turns on for an active auth "session" -- triggered by a screen
# tap/click (AUTH_ONLY_ON_CARD=False) or a valid card tap (AUTH_ONLY_ON_CARD=True).
# During a session, a face-match attempt is retried every AUTH_RETRY_INTERVAL_SEC
# until either a match succeeds or AUTH_SESSION_TIMEOUT_SEC elapses, at which
# point the preview is paused and the UI returns silently to idle.
AUTH_RETRY_INTERVAL_SEC = 3.0
AUTH_SESSION_TIMEOUT_SEC = 30.0

# Preview lead-in: how long the live preview is shown before the FIRST face
# match attempt takes the camera. Each attempt needs exclusive UVC access, so
# it pauses the preview; firing an attempt the instant the session starts meant
# the camera was killed within milliseconds of being turned on and the user
# only ever saw the paused/placeholder frame. This short window guarantees real
# frames reach the screen first. Set to 0 to restore the old fire-immediately
# behaviour (NFR-03).
PREVIEW_LEAD_IN_MS = 700

# Kiosk mode switch (governs BOTH the Qt-widgets and web front-ends):
#   True  -> fullscreen, no title bar / border on the small display (kiosk).
#   False -> normal draggable, bordered window (handy for editor-side debugging).
KIOSK_BORDERLESS = False

# Init mode: init mode is the entry state on every start (rev 1.3 / T16). The
# app always enters init mode first -- a brief live camera preview that scans
# for a technician QR code (maintenance/reset/config) -- before the normal
# idle/session flow. If a valid, signed QR is detected within
# INIT_MODE_DURATION_SEC, a maintenance flow is entered; otherwise the app
# hands off to regular operation.
#
# INIT_MODE_ENABLED no longer gates *entry* -- it only sizes the scan window:
#   True  -> a real INIT_MODE_DURATION_SEC-long QR-scan window.
#   False -> a zero-length window (enter, then immediately end); provisioning
#            stays reachable through the exact same code path.
INIT_MODE_ENABLED = True
INIT_MODE_DURATION_SEC = 8.0

# Directory of trusted issuer public keys for provisioning QR verification.
# Each file must be named "<key_id>.pem" (Ed25519 SubjectPublicKeyInfo PEM),
# where <key_id> matches the QR payload's signature.key_id. See
# qr_scanner/qr_scanner.py and other/qr_code_poc/issuer_keys.py.
PROVISIONING_PUBLIC_KEYS_DIR = "provisioning_keys"

# =====================================================
# Logging (see observability/README.md)
# =====================================================

# Global default level for the "face_guard" logger tree.
LOG_LEVEL = "INFO"

# Per-module overrides, keyed by the short module tag (the part after
# "face_guard."). Use this to quieten noisy subsystems without code changes.
# Available tags: qr_scanner, auth, relay, card, preview, db, gui, native.
LOG_LEVELS = {
    # librsid's C++ logs are extremely chatty at DEBUG (serial/UVC internals).
    "native": "INFO",
}

# Where the rotating log file is written. A relative path is resolved against
# the project root (/home/geka/RSID_Face_Guard/face_guard.log), so it doesn't
# depend on the process's current working directory (important under systemd).
LOG_FILE = "face_guard.log"

# Log rotation. When face_guard.log reaches LOG_MAX_BYTES (~1 MB) it is renamed
# to face_guard.log.1 and a fresh file is started; older files shift down
# (.1 -> .2 ...) and anything past LOG_BACKUP_COUNT is deleted. So disk usage is
# capped at roughly LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1) ~= 6 MB total --
# important on an SD card that must never fill up.
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

# =====================================================
# Storage Monitoring (observability/storage_monitor.py)
# =====================================================

# Filesystem path to monitor for free space. None -> project root, which is
# where face_guard.log, user_database.json and device_identity.json all live
# on the SD card. A relative path is resolved against the project root.
STORAGE_MONITOR_PATH = None

# Below this many MB free, a "storage_low" event fires (once per crossing)
# and a WARNING is logged on every check until space recovers.
STORAGE_MIN_FREE_MB = 200

# How often the GUI's background timer re-checks disk usage (seconds).
# Independent of HEARTBEAT_INTERVAL_SEC -- the latest result is also embedded
# in every heartbeat's metadata regardless of this interval.
STORAGE_CHECK_INTERVAL_SEC = 300

# =====================================================
# Remote Sync
# =====================================================

# How often to sync with server (seconds)
DB_SYNC_INTERVAL_SEC = 600   # 10 minutes

# Remote user/faceprint sync now reuses the dashboard server this device is
# bound to (provisioning/identity.py's DeviceIdentity.users_url), authenticated
# with the same bearer device_token as heartbeats -- no separate URL/MAC-based
# lookup needed. DB_MODE=remote requires the device to already be bound (see
# provisioning/); if it isn't, sync is skipped until binding completes.

# Network timeout (seconds)
REMOTE_TIMEOUT_SEC = 10

# =====================================================
# Device Binding (dashboard server -- see server/README.md)
# =====================================================

# Where this device's credentials live after it binds by scanning a
# provisioning QR. Holds a bearer token, so it is gitignored. Note the server
# URL is NOT configured here: it comes from the signed QR payload, which is how
# a fresh device learns which deployment it belongs to.
DEVICE_IDENTITY_FILE = "device_identity.json"

# How often a bound device reports its status. The server marks a device
# offline after its own (longer) timeout, so one missed beat won't flap it.
HEARTBEAT_INTERVAL_SEC = 30

# Reported to the dashboard as app_version.
APP_VERSION = "face-guard-0.1.0"

# When True, a provisioning QR carrying a Wi-Fi network_profile causes the
# device to actually join that Wi-Fi (via NetworkManager/nmcli) before it
# registers. Leave False on dev machines -- it would reconfigure the host's
# networking. Set True only on the Raspberry Pi kiosk. A "local" profile (LAN
# cable) is always a no-op regardless of this flag.
APPLY_NETWORK_PROFILE = True

# Seconds to wait for the Wi-Fi connection to come up after nmcli is invoked.
NETWORK_APPLY_TIMEOUT_SEC = 30

# =====================================================
# Authentication
# =====================================================

# RealSense matching threshold (score-based fallback matching)
CUSTOM_THRESHOLD = 400

# =====================================================
# Relay
# =====================================================

RUN_WITH_RELAY = True

RELAY_PIN = 18
RELAY_ACTIVE_LOW = True
RELAY_DEFAULT_OFF = True

# =====================================================
# Display
# =====================================================

WINDOW_WIDTH = 720
WINDOW_HEIGHT = 720

# =====================================================
# Web UI (gui_web / main_web.py)
# =====================================================

# Folder holding the designer-provided web UI (index.html, app.js, styles.css)
WEB_UI_DIR = "demo_ui"

# Loopback port the web UI + MJPEG camera stream are served on.
WEB_FRAME_PORT = 8791

WELCOME_DURATION_MS = 3000
FAIL_DURATION_MS = 3000