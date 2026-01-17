#!/usr/bin/env python3
"""
Shared User Database module for RealSense ID Face Guard
Thread-safe JSON-based user storage
"""

import json
import os
import threading
from typing import Optional, Dict, Any


class UserDatabase:
    """Thread-safe user database manager for JSON file storage"""
    
    def __init__(self, filename: str = 'user_database.json'):
        self.filename = filename
        self.lock = threading.Lock()
        self.users = self.load_users()
    
    def load_users(self) -> Dict[str, Dict[str, Any]]:
        """Load users from JSON file"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    users = json.load(f)
                    print(f"Loaded {len(users)} users from {self.filename}")
                    return users
            except Exception as e:
                print(f"Error loading user database: {e}")
                return {}
        print(f"No user database found at {self.filename}")
        return {}
    
    def save_users(self) -> bool:
        """Save users to JSON file"""
        try:
            with open(self.filename, 'w') as f:
                json.dump(self.users, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving user database: {e}")
            return False
    
    def add_user(self, user_id: str, name: str, permission_level: str, 
                 faceprints: Optional[Dict[str, Any]] = None) -> bool:
        """Add a new user to the database"""
        with self.lock:
            self.users[user_id] = {
                'name': name,
                'id': user_id,
                'permission_level': permission_level,
                'faceprints': faceprints
            }
            return self.save_users()
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user details by ID"""
        with self.lock:
            return self.users.get(user_id, None)
    
    def delete_user(self, user_id: str) -> bool:
        """Delete a user from the database"""
        with self.lock:
            if user_id in self.users:
                del self.users[user_id]
                return self.save_users()
            return False
    
    def clear_all(self) -> bool:
        """Clear all users from the database"""
        with self.lock:
            self.users = {}
            return self.save_users()
    
    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """Get all users (returns a copy for thread safety)"""
        with self.lock:
            return self.users.copy()
    
    def reload(self) -> None:
        """Reload users from file"""
        with self.lock:
            self.users = self.load_users()
