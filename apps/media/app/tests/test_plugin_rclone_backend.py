"""Unit tests for the rclone connection-string builders (no subprocess)."""

import pytest

from plugins.contracts import ConnectionFailedError
from plugins.rclone.backend import _build_fs, _google_drive_fs, _quote, _s3_fs


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


def test_google_drive_fs_requires_a_token() -> None:
    with pytest.raises(ConnectionFailedError):
        _google_drive_fs({})


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
