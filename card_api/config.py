# =====================================================
# General System Configuration
# =====================================================

# Database file
USER_DB_FILE = "user_database.json"

# =====================================================
# Remote Sync
# =====================================================

# How often to sync with server (seconds)
DB_SYNC_INTERVAL_SEC = 600   # 10 minutes

# Server URL
SERVER_URL = "https://geine-server.onrender.com/getAllTicketDeviceAccessByCompanyID"

# Network timeout (seconds)
REMOTE_TIMEOUT_SEC = 10


# =====================================================
# Authentication
# =====================================================

# RealSense matching threshold (if you later use score-based logic)
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

WINDOW_WIDTH = 800
WINDOW_HEIGHT = 480

WELCOME_DURATION_MS = 3000
FAIL_DURATION_MS = 3000


# =====================================================
# Logging
# =====================================================

ENABLE_DEBUG_LOG = True