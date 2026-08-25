"""
Database package: unified user data access.

Usage:
    from db import UserDatabase
    user_db = UserDatabase(USER_DB_FILE, identity=device_identity, remote_timeout_sec=REMOTE_TIMEOUT_SEC)
    user_db.get_user(badge_id)
    user_db.get_all_users()
    user_db.sync_from_remote()

Callers never need to know about LocalUserDataProvider / RemoteUserDataProvider.
"""

from .user_database import UserDatabase

__all__ = ["UserDatabase"]