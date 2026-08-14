"""Cross-cutting observability concerns: logging setup, and (planned) event
telemetry for server-side analysis. See README.md in this folder."""

from observability.logging_setup import get_logger, install_native_log_bridge, setup_logging

__all__ = ["get_logger", "install_native_log_bridge", "setup_logging"]