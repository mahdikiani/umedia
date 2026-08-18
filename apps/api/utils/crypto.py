"""Authenticated encryption for provider credentials."""

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


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
    ) -> "CredentialCipher":
        """Load an environment key or persist a generated installation key."""
        if env_key:
            return cls(env_key.encode())

        secret_dir = data_dir / "secrets"
        key_path = secret_dir / "master.key"
        if key_path.exists():
            return cls(key_path.read_bytes().strip())

        secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        os.chmod(key_path, 0o600)
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

