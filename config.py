# =====================================================
# General System Configuration
# =====================================================

# Database file (local cache / source of truth for face auth)
USER_DB_FILE = "user_database.json"

# "local"  -> only read/write the local JSON file, never contacts the server.
# "remote" -> periodically syncs from the server into the local JSON cache
#             (existing behavior); auth lookups still always read the local
#             cache, the remote fetch just keeps it fresh in the background.
DB_MODE = "local"

# =====================================================
# Hardware Simulation / Mode Flags
# =====================================================

# Simulate card reader / relay hardware (useful for dev off the Pi)
SIMULATE_CARD_READER = True

# Set True on RPi5 with the small 720x720 touch screen
RUN_ON_REAL_SCREEN = True

# Enable card reader monitoring (auto-authenticate when a card is tapped)
AUTH_ONLY_ON_CARD = False

# Auto-auth interval (seconds): the app periodically re-authenticates on its own.
AUTO_AUTH_INTERVAL_SEC = 5.0

# Kiosk mode switch (governs BOTH the Qt-widgets and web front-ends):
#   True  -> fullscreen, no title bar / border on the small display (kiosk).
#   False -> normal draggable, bordered window (handy for editor-side debugging).
KIOSK_BORDERLESS = False

# =====================================================
# Remote Sync
# =====================================================

# How often to sync with server (seconds)
DB_SYNC_INTERVAL_SEC = 600   # 10 minutes

# Server URL used to fetch remote users/faceprints by device MAC address
SERVER_URL = "https://geine-server.onrender.com/getTicketDeviceAccessByMacAdress"

# Network timeout (seconds)
REMOTE_TIMEOUT_SEC = 10

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