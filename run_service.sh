#!/bin/bash
#
# systemd launcher for main_web.py (see face-guard.service).
#
# Unlike run_main_web.sh (interactive, waits for Enter at the end), this script
# is non-interactive and waits for the X display to exist before starting:
# lightdm autologs into labwc, which spawns Xwayland on demand, so the service
# can reach graphical.target before :0 is up. Exiting non-zero here lets
# systemd's Restart=always retry cleanly instead of Qt aborting.

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$PROJECT_DIR" || exit 1

export LD_LIBRARY_PATH="$PROJECT_DIR/rpi_py_build_lib:$LD_LIBRARY_PATH"
export DISPLAY="${DISPLAY:-:0}"

# GPU rendering on native Wayland. Override: RSID_GPU=0, QT_QPA_PLATFORM=xcb.
export RSID_GPU="${RSID_GPU:-1}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# Display check: the Wayland socket, or the X server when running on xcb.
display_ready() {
    if [ "$QT_QPA_PLATFORM" = "wayland" ]; then
        [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]
    else
        xdpyinfo -display "$DISPLAY" >/dev/null 2>&1
    fi
}

WAIT_SECS="${FACE_GUARD_DISPLAY_WAIT:-60}"
for ((i = 0; i < WAIT_SECS; i++)); do
    if display_ready; then
        break
    fi
    sleep 1
done

if ! display_ready; then
    echo "Display ($QT_QPA_PLATFORM) not available after ${WAIT_SECS}s -- exiting for retry" >&2
    exit 1
fi

echo "Display ($QT_QPA_PLATFORM) is up; starting main_web.py"

# exec so SIGTERM reaches Python directly (main_web.py handles it: orderly
# shutdown with a 6s watchdog) instead of being absorbed by this shell.
exec "$PROJECT_DIR/.venv/bin/python" main_web.py "$@"
