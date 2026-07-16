"""
Local, file-backed user data provider (JSON).

This is the source of truth used for offline face authentication -- the
app must keep working even without network access, so all reads for
auth go through this provider (via db.user_database.UserDatabase).
"""

import json
import os
from typing import Dict


class LocalUserDataProvider:
    """Reads/writes users to a local JSON file (atomic write)."""

    def __init__(self, db_file: str):
        self.db_file = db_file

    def load_all(self) -> Dict[str, dict]:
        if not os.path.exists(self.db_file):
            print(f"ℹ No local user DB found at {self.db_file}. Starting empty.")
            return {}
        try:
            with open(self.db_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Failed loading local DB ({self.db_file}):", e)
            return {}

    def save_all(self, users: Dict[str, dict]) -> None:
        tmp_file = self.db_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(users, f, indent=2)
            os.replace(tmp_file, self.db_file)
        except Exception as e:
            print(f"❌ Failed saving local DB ({self.db_file}):", e)