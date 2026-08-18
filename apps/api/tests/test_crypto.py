from pathlib import Path

from utils.crypto import CredentialCipher


def test_cipher_generates_persistent_key_and_round_trips(tmp_path: Path) -> None:
    cipher = CredentialCipher.from_environment(data_dir=tmp_path, env_key=None)
    encrypted = cipher.encrypt_json({"token": "secret"})

    assert encrypted != '{"token":"secret"}'
    assert cipher.decrypt_json(encrypted) == {"token": "secret"}
    assert (tmp_path / "secrets" / "master.key").exists()


def test_cipher_prefers_environment_key(tmp_path: Path) -> None:
    generated = CredentialCipher.from_environment(data_dir=tmp_path, env_key=None)
    env_key = generated.key.decode()

    restored = CredentialCipher.from_environment(
        data_dir=tmp_path / "other",
        env_key=env_key,
    )

    assert restored.decrypt_json(restored.encrypt_json({"ok": True})) == {"ok": True}

