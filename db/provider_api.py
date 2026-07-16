"""
Common interface for user data providers.

A UserDataProvider is only responsible for loading (and, for local
providers, saving) a dict of {badge_id: user_data}. Callers (GUI,
business logic) never talk to a provider directly -- they use
db.user_database.UserDatabase instead, which doesn't care whether the
data ultimately came from a local file or a cloud server.
"""

from abc import ABC, abstractmethod
from typing import Dict


class UserDataProvider(ABC):
    """Abstract base class for a source of user records."""

    @abstractmethod
    def load_all(self) -> Dict[str, dict]:
        """Return all known users as {badge_id: user_data}."""
        raise NotImplementedError

    def save_all(self, users: Dict[str, dict]) -> None:
        """Persist the given users dict, if this provider supports writing.

        Read-only providers (e.g. a remote/cloud provider) can simply not
        override this method.
        """
        raise NotImplementedError(f"{self.__class__.__name__} is read-only")