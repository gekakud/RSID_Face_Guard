import json
import os
import threading


class UserDatabase:
    """
    Simple thread-safe JSON user database.
    """

    def __init__(self, db_file="user_database.json"):
        self.db_file = db_file
        self._lock = threading.Lock()
        self.users = {}
        self.load()

    # =====================================================
    # Load DB
    # =====================================================

    def load(self):
        with self._lock:
            if not os.path.exists(self.db_file):
                print("ℹ No user DB found. Creating empty DB.")
                self.users = {}
                return

            try:
                with open(self.db_file, "r") as f:
                    self.users = json.load(f)
            except Exception as e:
                print("❌ Failed loading DB:", e)
                self.users = {}

    # =====================================================
    # Save DB (atomic write)
    # =====================================================

    def save(self):
        with self._lock:

            tmp_file = self.db_file + ".tmp"

            try:
                with open(tmp_file, "w") as f:
                    json.dump(self.users, f, indent=2)

                os.replace(tmp_file, self.db_file)

            except Exception as e:
                print("❌ Failed saving DB:", e)

    # =====================================================
    # Getters
    # =====================================================

    def get_user(self, badge_id):
        with self._lock:
            return self.users.get(str(badge_id))

    def get_all_users(self):
        with self._lock:
            return dict(self.users)

    def count(self):
        with self._lock:
            return len(self.users)

    # =====================================================
    # Setters
    # =====================================================

    def set_user(self, badge_id, user_data):
        with self._lock:
            self.users[str(badge_id)] = user_data

    def remove_user(self, badge_id):
        with self._lock:
            self.users.pop(str(badge_id), None)

    def clear(self):
        with self._lock:
            self.users = {}