"""Authenticated encryption for provider credentials."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class CredentialCipher:
    """Encrypt and decrypt structured provider credentials."""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self._fernet = Fernet(key)

    @classmethod
    def from_environment(
        cls,
        *,
        data_dir: Path,
        env_key: str | None,
        secrets_dir: Path | None = None,
    ) -> "CredentialCipher":
        """Load the installation key, or persist a generated one.

        Order: `env_key` (UMEDIA_MASTER_KEY), then `master.key` in
        `secrets_dir` (UMEDIA_SECRETS_DIR, ideally its own volume), then the
        legacy `<data_dir>/secrets/master.key`. A key generated next to the
        database means any backup of the data volume holds both the
        encrypted credentials and the key that opens them, so that case
        logs a warning.
        """
        if env_key:
            return cls(env_key.encode())

        legacy_path = data_dir / "secrets" / "master.key"
        key_path = (
            secrets_dir / "master.key" if secrets_dir is not None else legacy_path
        )
        if key_path.exists():
            return cls(key_path.read_bytes().strip())
        if legacy_path.exists():
            logger.warning(
                "Using the master key at %s; move it to UMEDIA_SECRETS_DIR (%s) "
                "so data backups do not contain it.",
                legacy_path,
                key_path.parent,
            )
            return cls(legacy_path.read_bytes().strip())

        key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
        if key_path == legacy_path:
            logger.warning(
                "Generated a master key inside the data directory (%s). A "
                "backup of that volume contains both the database and its "
                "key. Set UMEDIA_MASTER_KEY or UMEDIA_SECRETS_DIR (separate "
                "volume) to keep them apart.",
                key_path,
            )
        return cls(key)

    def encrypt_json(self, value: dict[str, Any]) -> str:
        """Encrypt a JSON-compatible dictionary."""
        payload = json.dumps(
            value,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return self._fernet.encrypt(payload).decode()

    def decrypt_json(self, token: str) -> dict[str, Any]:
        """Decrypt structured credentials."""
        return json.loads(self._fernet.decrypt(token.encode()))

    def encrypt(self, value: str) -> str:
        """Encrypt a plain string (e.g. a user access-key secret)."""
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypt a plain string encrypted with `encrypt`."""
        return self._fernet.decrypt(token.encode()).decode()
