"""In-memory WebDAV class-2 locks.

Finder mounts read-write only when the server advertises LOCK, and
Windows locks before it saves. The table is per process: it guards one
user's own clients against each other, it is not a distributed lock.
Locks are keyed by (user, path), and library data is per user, so two
accounts never contend.
"""

import re
import time
import uuid
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"<(opaquelocktoken:[^>\s]+)>")


@dataclass
class Lock:
    token: str
    user_id: str
    path: str  # "/a/b" -- decoded, no trailing slash, "" for the root
    depth_infinity: bool
    owner_xml: str
    expires_at: float
    scope: str = "exclusive"


@dataclass
class LockTable:
    _locks: dict[str, Lock] = field(default_factory=dict)

    def _purge(self) -> None:
        now = time.monotonic()
        for token in [t for t, lock in self._locks.items() if lock.expires_at <= now]:
            del self._locks[token]

    def create(
        self,
        *,
        user_id: str,
        path: str,
        depth_infinity: bool,
        owner_xml: str,
        timeout: int,
    ) -> Lock:
        lock = Lock(
            token=f"opaquelocktoken:{uuid.uuid4()}",
            user_id=user_id,
            path=path,
            depth_infinity=depth_infinity,
            owner_xml=owner_xml,
            expires_at=time.monotonic() + timeout,
        )
        self._purge()
        self._locks[lock.token] = lock
        return lock

    def get(self, token: str, user_id: str) -> Lock | None:
        self._purge()
        lock = self._locks.get(token)
        return lock if lock is not None and lock.user_id == user_id else None

    def refresh(self, token: str, user_id: str, timeout: int) -> Lock | None:
        lock = self.get(token, user_id)
        if lock is not None:
            lock.expires_at = time.monotonic() + timeout
        return lock

    def remove(self, token: str, user_id: str) -> bool:
        if self.get(token, user_id) is None:
            return False
        del self._locks[token]
        return True

    def remove_under(self, user_id: str, path: str) -> None:
        for token, lock in list(self._locks.items()):
            if lock.user_id == user_id and _covers(path, lock.path, True):
                del self._locks[token]

    def covering(self, user_id: str, path: str, *, subtree: bool) -> list[Lock]:
        """Locks that constrain a write to `path` (and, with `subtree`,
        anything below it -- DELETE/MOVE of a folder holding a locked file)."""
        self._purge()
        found = []
        for lock in self._locks.values():
            if lock.user_id != user_id:
                continue
            if _covers(lock.path, path, lock.depth_infinity) or (
                subtree and _covers(path, lock.path, True)
            ):
                found.append(lock)
        return found

    def at(self, user_id: str, path: str) -> list[Lock]:
        self._purge()
        return [
            lock
            for lock in self._locks.values()
            if lock.user_id == user_id and lock.path == path
        ]


def _covers(ancestor: str, path: str, infinity: bool) -> bool:
    if ancestor == path:
        return True
    return infinity and (ancestor == "" or path.startswith(ancestor + "/"))


def submitted_tokens(if_header: str | None, lock_token: str | None) -> set[str]:
    tokens = set(TOKEN_RE.findall(if_header or ""))
    tokens.update(TOKEN_RE.findall(lock_token or ""))
    return tokens
