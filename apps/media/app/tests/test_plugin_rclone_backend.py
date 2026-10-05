"""Unit tests for the rclone connection-string builders (no subprocess)."""

import shutil

import pytest

from plugins.contracts import ConnectionFailedError
from plugins.rclone.backend import (
    _build_fs,
    _dropbox_fs,
    _ftp_fs,
    _google_drive_fs,
    _obscure,
    _onedrive_fs,
    _quote,
    _s3_fs,
    _sftp_fs,
    _webdav_fs,
)


def test_quote_wraps_values_with_delimiter_characters() -> None:
    assert _quote("plain") == "plain"
    assert _quote("http://host:1234") == "'http://host:1234'"
    assert _quote("a,b") == "'a,b'"
    assert _quote("it's") == "'it''s'"


def test_s3_fs_builds_a_valid_connection_string() -> None:
    fs = _s3_fs({
        "access_key_id": "AKIA",
        "secret_access_key": "secret",
        "bucket": "my-bucket",
        "endpoint_url": "http://127.0.0.1:5001",
        "region": "us-east-1",
    })

    assert fs.startswith(":s3,")
    assert fs.endswith(":my-bucket")
    assert "access_key_id=AKIA" in fs
    assert "endpoint='http://127.0.0.1:5001'" in fs
    assert "force_path_style=true" in fs
    assert "provider=Minio" in fs
    assert "no_head=true" in fs
    assert "no_check_bucket=true" in fs


def test_s3_fs_detects_cloudflare_r2_from_endpoint() -> None:
    fs = _s3_fs({
        "access_key_id": "r2key",
        "secret_access_key": "r2secret",
        "bucket": "media",
        "endpoint_url": "https://abc123.r2.cloudflarestorage.com",
    })
    assert "provider=Cloudflare" in fs
    assert "region=auto" in fs
    assert "endpoint='https://abc123.r2.cloudflarestorage.com'" in fs
    assert "no_head=true" in fs
    assert "no_check_bucket=true" in fs


def test_s3_fs_defaults_aws_without_endpoint() -> None:
    fs = _s3_fs({
        "access_key_id": "AKIA",
        "secret_access_key": "secret",
        "bucket": "prod",
        "region": "eu-west-1",
    })
    assert "provider=AWS" in fs
    assert "endpoint=" not in fs
    assert "no_head=" not in fs


def test_s3_fs_ignores_placeholder_other_region() -> None:
    fs = _s3_fs({
        "access_key_id": "a",
        "secret_access_key": "b",
        "bucket": "x",
        "endpoint_url": "https://rfs.example.com",
        "region": "other-v2-signature",
    })
    assert "provider=Minio" in fs
    assert "region=" not in fs


def test_s3_fs_requires_credentials_and_bucket() -> None:
    with pytest.raises(ConnectionFailedError):
        _s3_fs({"bucket": "x"})
    with pytest.raises(ConnectionFailedError):
        _s3_fs({"access_key_id": "a", "secret_access_key": "b"})


def test_google_drive_fs_builds_a_valid_connection_string() -> None:
    fs = _google_drive_fs({"token": "oauth-token-json", "root_folder_id": "abc123"})

    assert fs.startswith(":drive,")
    assert "token=oauth-token-json" in fs
    assert "root_folder_id=abc123" in fs


def test_google_drive_fs_includes_client_id_and_secret_when_set() -> None:
    fs = _google_drive_fs({
        "token": '{"access_token":"a"}',
        "client_id": "my-client",
        "client_secret": "my-secret",
        "root_folder_id": "folder",
    })
    assert "client_id=my-client" in fs
    assert "client_secret=my-secret" in fs
    assert "root_folder_id=folder" in fs


def test_google_drive_fs_requires_a_token() -> None:
    with pytest.raises(ConnectionFailedError):
        _google_drive_fs({})


def test_onedrive_fs_uses_token_and_selected_drive() -> None:
    fs = _onedrive_fs({
        "token": '{"access_token":"a","refresh_token":"r"}',
        "client_id": "app-id",
        "client_secret": "app-secret",
        "drive_id": "b!drive-id",
        "drive_type": "business",
    })
    assert fs.startswith(":onedrive,")
    assert "token='{" in fs
    assert "drive_id=b!drive-id" in fs
    assert "drive_type=business" in fs
    assert "client_secret=app-secret" in fs


def test_onedrive_fs_requires_token_and_drive_id() -> None:
    with pytest.raises(ConnectionFailedError, match="token"):
        _onedrive_fs({})
    with pytest.raises(ConnectionFailedError, match="drive_id"):
        _onedrive_fs({"token": "token-json"})


def test_dropbox_fs_includes_oauth_token_and_app_credentials() -> None:
    fs = _dropbox_fs({
        "token": '{"access_token":"a","refresh_token":"r"}',
        "client_id": "app-id",
        "client_secret": "app-secret",
    })
    assert fs.startswith(":dropbox,")
    assert "token='{" in fs
    assert "client_id=app-id" in fs
    assert "client_secret=app-secret" in fs


def test_dropbox_fs_requires_token() -> None:
    with pytest.raises(ConnectionFailedError, match="token"):
        _dropbox_fs({})


HOST_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def _reveal(obscured: str) -> str:
    """Decode with the real binary: proves our obscure matches rclone's."""
    import subprocess  # noqa: S404 -- fixed argv, local test binaries

    rclone = shutil.which("rclone")
    if rclone is None:
        pytest.skip("rclone not installed")
    return subprocess.run(  # noqa: S603
        [rclone, "reveal", "--", obscured],  # value may start with "-"
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _param(fs: str, key: str) -> str:
    """Pull one (unquoted) value back out of an inline connection string."""
    import re

    match = re.search(rf"(?:^|,){key}=('(?:[^']|'')*'|[^,:]*)", fs)
    assert match, f"{key} missing from {fs}"
    value = match.group(1)
    if value.startswith("'"):
        value = value[1:-1].replace("''", "'")
    return value


def test_obscure_round_trips_through_rclone_reveal() -> None:
    assert _reveal(_obscure("p@ss,w:rd'")) == "p@ss,w:rd'"


def test_ftp_fs_defaults_to_explicit_tls_and_obscures_the_password() -> None:
    fs = _ftp_fs({"host": "ftp.example.com", "user": "bob", "password": "pw"})

    assert fs.startswith(":ftp,")
    assert fs.endswith(":")
    assert "host=ftp.example.com" in fs
    assert "user=bob" in fs
    assert "explicit_tls=true" in fs
    assert "pass=pw" not in fs
    assert _reveal(_param(fs, "pass")) == "pw"


def test_ftp_fs_supports_implicit_and_plain_modes_port_and_root() -> None:
    implicit = _ftp_fs({"host": "h", "user": "u", "tls": "implicit", "port": "990"})
    assert "tls=true" in implicit
    assert "explicit_tls" not in implicit
    assert "port=990" in implicit

    plain = _ftp_fs({"host": "h", "tls": "none", "root_path": "/srv/media"})
    assert "tls=" not in plain
    assert plain.endswith(":/srv/media")


def test_ftp_fs_rejects_missing_host_and_unknown_tls_mode() -> None:
    with pytest.raises(ConnectionFailedError, match="host"):
        _ftp_fs({})
    with pytest.raises(ConnectionFailedError, match="tls"):
        _ftp_fs({"host": "h", "tls": "maybe"})


def test_sftp_fs_with_password_and_pinned_host_key() -> None:
    fs = _sftp_fs({
        "host": "sftp.example.com",
        "port": "2222",
        "user": "alice",
        "password": "secret",
        "host_key": HOST_KEY,
        "root_path": "media",
    })

    assert fs.startswith(":sftp,")
    assert "port=2222" in fs
    assert _reveal(_param(fs, "pass")) == "secret"
    assert _param(fs, "host_keys") == HOST_KEY
    assert fs.endswith(":media")


def test_sftp_fs_rebuilds_a_pem_key_whose_newlines_were_stripped() -> None:
    # A single-line <input> drops newlines from a pasted key.
    flattened = (
        "-----BEGIN OPENSSH PRIVATE KEY-----"
        "b3BlbnNzaC1rZXktdjEAAAAA"
        "BG5vbmUAAAAEbm9uZQ=="
        "-----END OPENSSH PRIVATE KEY-----"
    )
    fs = _sftp_fs({
        "host": "h",
        "user": "u",
        "private_key": flattened,
        "host_key": HOST_KEY,
    })

    assert _param(fs, "key_pem") == (
        "-----BEGIN OPENSSH PRIVATE KEY-----\\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ==\\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )


def test_sftp_fs_requires_host_user_credential_and_pinned_key() -> None:
    pin = {"host_key": HOST_KEY}
    with pytest.raises(ConnectionFailedError, match="host"):
        _sftp_fs({"user": "u", "password": "p", **pin})
    with pytest.raises(ConnectionFailedError, match="user"):
        _sftp_fs({"host": "h", "password": "p", **pin})
    with pytest.raises(ConnectionFailedError, match="password or private_key"):
        _sftp_fs({"host": "h", "user": "u", **pin})
    # Never build an unpinned remote: rclone would accept any server key.
    with pytest.raises(ConnectionFailedError, match="host_key"):
        _sftp_fs({"host": "h", "user": "u", "password": "p"})


def test_webdav_fs_uses_vendor_and_obscured_password() -> None:
    fs = _webdav_fs({
        "url": "https://cloud.example.com/remote.php/dav/files/me",
        "vendor": "Nextcloud",
        "user": "me",
        "password": "app-password",
    })

    assert fs.startswith(":webdav,")
    assert "url='https://cloud.example.com/remote.php/dav/files/me'" in fs
    assert "vendor=nextcloud" in fs
    assert _reveal(_param(fs, "pass")) == "app-password"


def test_webdav_fs_defaults_vendor_and_accepts_a_bearer_token() -> None:
    fs = _webdav_fs({"url": "https://dav.example.com", "bearer_token": "tok"})
    assert "vendor=other" in fs
    assert "bearer_token=tok" in fs


def test_webdav_fs_rejects_missing_url_and_unknown_vendor() -> None:
    with pytest.raises(ConnectionFailedError, match="url"):
        _webdav_fs({})
    with pytest.raises(ConnectionFailedError, match="vendor"):
        _webdav_fs({"url": "https://x", "vendor": "acme"})


def test_build_fs_dispatches_on_remote_type() -> None:
    fs = _build_fs({
        "remote_type": "s3",
        "access_key_id": "a",
        "secret_access_key": "b",
        "bucket": "x",
    })
    assert fs.startswith(":s3,")


def test_build_fs_rejects_an_unknown_remote_type() -> None:
    with pytest.raises(ConnectionFailedError, match="Unsupported"):
        _build_fs({"remote_type": "not-a-real-backend"})
