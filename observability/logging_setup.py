"""
Single configuration point for application logging.

All three entry points (main.py / main_qt.py / main_web.py) call
setup_logging() exactly once at startup; every module then obtains its own
child logger via get_logger("<short_name>"). This gives a proper logger
hierarchy under the "face_guard" root:

    face_guard.qr_scanner   qr_scanner/qr_scanner.py
    face_guard.auth         face_auth/auth_service.py
    face_guard.relay        hardware/relay_api.py
    face_guard.card         hardware/card_reader_api.py, card_backends_impl/*
    face_guard.preview      hardware/camera_preview.py
    face_guard.db           db/*
    face_guard.gui          gui_qt/*, gui_web/*
    face_guard.native       librsid (C++) via the rsid_py log callback

Benefits of the hierarchy: every line says which module produced it, and
per-module verbosity can be tuned from config.LOG_LEVELS without touching
code (e.g. silencing the very chatty native serial/UVC debug output).

The native RealSense library (librsid.so) logs through its own C++ logger
straight to stdout in a different format. install_native_log_bridge()
copies it into Python logging so native messages also reach face_guard.log
in our format.

Caveat: the prebuilt rpi_py_build_lib/librsid.so was compiled with
RSID_DEBUG_CONSOLE=ON, so it ALSO writes its own "[ts] [level] [Tag] msg"
line directly to stdout -- that output happens inside C++ and cannot be
turned off at runtime. Bridged lines therefore appear twice on the console
(once raw, once formatted), though the log FILE only ever gets our
formatted copy. Rebuilding librsid without RSID_DEBUG_CONSOLE would remove
the raw duplicate (see docs/linux_readme.md for the cmake flow).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

import config

ROOT_LOGGER_NAME = "face_guard"

# Width-aligned so columns line up in the console/file output.
_LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(module_tag)-10s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class _ModuleTagFilter(logging.Filter):
    """Expose a short module tag (logger name minus the "face_guard." prefix)
    as %(module_tag)s, so the format string stays readable."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        prefix = ROOT_LOGGER_NAME + "."
        record.module_tag = name[len(prefix):] if name.startswith(prefix) else name
        return True

def get_logger(name: str) -> logging.Logger:
    """Return the child logger "face_guard.<name>" for a module.

    Args:
        name: short, stable module tag (e.g. "qr_scanner", "relay", "db").
    """
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")

def _resolve_level(value, default=logging.INFO) -> int:
    """Accept either a level name ("DEBUG") or a numeric level."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return getattr(logging, value.upper(), default)
    return default

def setup_logging() -> logging.Logger:
    """Configure the "face_guard" root logger (idempotent) and return it."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    if root.handlers:
        return root

    root.setLevel(_resolve_level(getattr(config, "LOG_LEVEL", "INFO")))
    # Don't propagate to the stdlib root logger -- avoids duplicate lines if
    # anything else (e.g. a library) calls logging.basicConfig().
    root.propagate = False

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    tag_filter = _ModuleTagFilter()

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.addFilter(tag_filter)
    root.addHandler(ch)

    log_file = getattr(config, "LOG_FILE", "face_guard.log")
    if not os.path.isabs(log_file):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_file = os.path.join(project_root, log_file)
    fh = RotatingFileHandler(
        log_file,
        maxBytes=getattr(config, "LOG_MAX_BYTES", 1_000_000),
        backupCount=getattr(config, "LOG_BACKUP_COUNT", 5),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(tag_filter)
    root.addHandler(fh)

    # Per-module overrides, e.g. {"native": "INFO", "preview": "WARNING"}.
    for module_tag, level in getattr(config, "LOG_LEVELS", {}).items():
        get_logger(module_tag).setLevel(_resolve_level(level))

    return root

def install_native_log_bridge() -> bool:
    """Route librsid's C++ log output into Python logging ("face_guard.native").

    Safe to call unconditionally: returns False (and logs a debug note) if
    rsid_py is missing or doesn't expose the log callback API, rather than
    breaking startup.
    """
    log = get_logger("native")
    try:
        import rsid_py
    except ImportError:
        return False

    if not hasattr(rsid_py, "set_log_callback"):
        log.debug("rsid_py has no set_log_callback -- native log bridge unavailable")
        return False

    level_map = {
        rsid_py.LogLevel.Trace: logging.DEBUG,
        rsid_py.LogLevel.Debug: logging.DEBUG,
        rsid_py.LogLevel.Info: logging.INFO,
        rsid_py.LogLevel.Warning: logging.WARNING,
        rsid_py.LogLevel.Error: logging.ERROR,
        rsid_py.LogLevel.Critical: logging.CRITICAL,
    }

    def _callback(level, msg):
        # librsid appends its own newline; strip it so lines don't double-space.
        log.log(level_map.get(level, logging.INFO), "%s", str(msg).rstrip())

    try:
        # do_formatting=False -> bare message; our formatter adds ts/level/tag.
        rsid_py.set_log_callback(_callback, rsid_py.LogLevel.Debug, False)
    except Exception:
        log.exception("Failed installing native (librsid) log bridge")
        return False

    log.debug("Native (librsid) log bridge installed")
    return True