"""Unit tests for the rclone connection-string builders (no subprocess)."""

import pytest

from plugins.contracts import ConnectionFailedError
from plugins.rclone.backend import (
    _build_fs,
    _dropbox_fs,
    _google_drive_fs,
    _onedrive_fs,
    _quote,
    _s3_fs,
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
