#!/bin/bash
#
# Desktop launcher for the RSID Face Guard web UI (main_web.py).
#
# Double-click the "RSID Face Guard (Web).desktop" icon (which runs this in a
# terminal), or run this script directly. It sets up the runtime environment
# and starts the web GUI using the project's virtualenv Python.
#
# The terminal stays open after exit so you can read any error/log output on
# the small screen without a code editor.

# Resolve the project directory (where this script lives) so it works no matter
# where it is launched from.
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$PROJECT_DIR" || exit 1

# --- Runtime environment -----------------------------------------------------
# rsid_py's native libraries live in rpi_py_build_lib/ (see howto.md).
export LD_LIBRARY_PATH="$PROJECT_DIR/rpi_py_build_lib:$LD_LIBRARY_PATH"

# Make sure the GUI shows on the attached display when launched from a desktop
# icon (which may not inherit DISPLAY).
export DISPLAY="${DISPLAY:-:0}"

echo "=================================================="
echo " RSID Face Guard - Web UI"
echo " Project : $PROJECT_DIR"
echo " Python  : $PROJECT_DIR/.venv/bin/python"
echo " Display : $DISPLAY"
echo "=================================================="
echo

# --- Run ---------------------------------------------------------------------
"$PROJECT_DIR/.venv/bin/python" main_web.py "$@"
STATUS=$?

echo
echo "=================================================="
echo " main_web.py exited with status $STATUS"
echo "=================================================="
read -r -p "Press Enter to close this window..." _