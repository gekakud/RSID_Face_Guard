"""
UserDatabase -- the single facade the rest of the app depends on.

GUI and business logic (face_auth) only call this class; they never
know or care whether the data came from the local JSON file or the
cloud server. Internally:
  - The local provider is the source of truth used for offline auth.
  - sync_from_remote() pulls fresh data from the remote provider and
    merges/persists it into the local provider.
"""

import logging
import threading
from typing import Callable, Dict, Optional

from .local_provider import LocalUserDataProvider
from .remote_provider import RemoteUserDataProvider

from observability.logging_setup import get_logger

log = get_logger("db")


class UserDatabase:
    """Thread-safe user database backed by a local cache + optional remote sync."""

    def __init__(self, db_file: str, server_url: Optional[str] = None,
                 remote_timeout_sec: float = 10):
        self._lock = threading.Lock()
        self._local = LocalUserDataProvider(db_file)
        self._remote = RemoteUserDataProvider(server_url, remote_timeout_sec) if server_url else None
        self.users: Dict[str, dict] = {}
        self.reload()

        self._sync_stop_event = threading.Event()
        self._sync_thread: Optional[threading.Thread] = None

    # =====================================================
    # Load / persist local cache
    # =====================================================

    def reload(self):
        """(Re)load the in-memory dict from the local provider."""
        with self._lock:
            self.users = self._local.load_all()

    def _save(self):
        with self._lock:
            self._local.save_all(self.users)

    # =====================================================
    # Remote sync
    # =====================================================

    def sync_from_remote(self, overwrite_existing: bool = True) -> int:
        """Pull users from the remote provider and merge into the local cache.

        Returns the number of users updated. No-op (returns 0) if no
        remote provider was configured.
        """
        if self._remote is None:
            return 0

        remote_users = self._remote.load_all()
        if not remote_users:
            return 0

        updated_count = 0
        with self._lock:
            for badge_id, user_data in remote_users.items():
                if overwrite_existing or badge_id not in self.users:
                    self.users[badge_id] = user_data
                    updated_count += 1

        if updated_count > 0:
            self._save()
            self.reload()
        return updated_count

    def start_auto_sync(self, interval_sec: float, on_updated: Optional[Callable[[int], None]] = None):
        """Start a background daemon thread that periodically calls
        sync_from_remote() every interval_sec seconds.

        Fully self-contained: the database is responsible for keeping
        itself fresh. Safe to call even if no remote provider is
        configured (sync_from_remote() will just no-op each tick).
        """
        if self._sync_thread is not None:
            return  # already running

        self._sync_stop_event.clear()

        def _loop():
            log.info("UserDatabase auto-sync started (interval=%ds)", interval_sec)
            while not self._sync_stop_event.wait(interval_sec):
                try:
                    updated = self.sync_from_remote()
                    if updated > 0:
                        log.info("UserDatabase auto-sync: %d user(s) updated", updated)
                        if on_updated:
                            on_updated(updated)
                except Exception as e:
                    log.error("UserDatabase auto-sync error: %s", e)
            log.info("UserDatabase auto-sync stopped")

        self._sync_thread = threading.Thread(target=_loop, daemon=True)
        self._sync_thread.start()

    def stop_auto_sync(self):
        """Stop the background auto-sync thread (if running)."""
        if self._sync_thread is None:
            return
        self._sync_stop_event.set()
        self._sync_thread.join(timeout=2)
        self._sync_thread = None

    # =====================================================
    # Getters
    # =====================================================

    def get_user(self, badge_id) -> Optional[dict]:
        with self._lock:
            return self.users.get(str(badge_id))

    def get_all_users(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self.users)

    def count(self) -> int:
        with self._lock:
            return len(self.users)

    # =====================================================
    # Setters
    # =====================================================

    def set_user(self, badge_id, user_data: dict):
        with self._lock:
            self.users[str(badge_id)] = user_data
        self._save()

    def remove_user(self, badge_id):
        with self._lock:
            self.users.pop(str(badge_id), None)
        self._save()

    def clear(self):
        with self._lock:
            self.users = {}
        self._save()