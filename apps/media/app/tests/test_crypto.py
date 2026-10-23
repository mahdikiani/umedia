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


def test_key_is_generated_in_secrets_dir_when_set(tmp_path) -> None:  # noqa: ANN001
    data_dir, secrets_dir = tmp_path / "data", tmp_path / "secrets-vol"
    first = CredentialCipher.from_environment(
        data_dir=data_dir, env_key=None, secrets_dir=secrets_dir
    )
    assert (secrets_dir / "master.key").exists()
    assert not (data_dir / "secrets").exists()
    again = CredentialCipher.from_environment(
        data_dir=data_dir, env_key=None, secrets_dir=secrets_dir
    )
    assert again.key == first.key


def test_legacy_key_in_data_dir_is_reused_when_secrets_dir_is_new(tmp_path) -> None:  # noqa: ANN001
    data_dir = tmp_path / "data"
    legacy = CredentialCipher.from_environment(data_dir=data_dir, env_key=None)
    moved = CredentialCipher.from_environment(
        data_dir=data_dir, env_key=None, secrets_dir=tmp_path / "new-vol"
    )
    assert moved.key == legacy.key
    assert not (tmp_path / "new-vol" / "master.key").exists()
