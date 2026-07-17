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
SIMULATE_HW = True

# Set True on RPi5 with the small 720x720 touch screen
RUN_ON_REAL_DEVICE = True

# Enable card reader monitoring (auto-authenticate when a card is tapped)
RUN_WITH_CARD_READER = False

# UI mode:
#   True  -> manual auth via on-screen button
#   False -> periodic auto-auth (no button shown)
WITH_BUTTON = False

# Auto-auth interval (seconds) when WITH_BUTTON=False
AUTO_AUTH_INTERVAL_SEC = 5.0

# Borderless "kiosk-like" window. Required to prevent the WM from moving
# the window to the primary display on a multi-monitor setup.
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

WELCOME_DURATION_MS = 3000
FAIL_DURATION_MS = 3000