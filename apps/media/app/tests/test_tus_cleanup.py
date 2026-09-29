import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

from apps.media_files.uploads import _cleanup_expired_uploads, _parse_expires


def _write_upload(
    upload_dir: Path,
    uid: str,
    *,
    expires: str | float | None,
) -> None:
    (upload_dir / uid).write_bytes(b"partial content")
    info = {"metadata": {}, "size": 100, "offset": 16, "created_at": "x"}
    if expires is not None:
        info["expires"] = expires
    (upload_dir / f"{uid}.info").write_text(json.dumps(info))


def test_parse_expires_accepts_rfc7231_and_float() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    assert _parse_expires(format_datetime(now, usegmt=True)) == now
    assert _parse_expires(now.timestamp()) == now


def test_parse_expires_returns_none_for_garbage() -> None:
    assert _parse_expires("not a date") is None


def test_parse_expires_returns_none_for_out_of_range_timestamp() -> None:
    assert _parse_expires(float("inf")) is None


def test_cleanup_keeps_upload_with_invalid_expiry(tmp_path: Path) -> None:
    _write_upload(tmp_path, "invalid-expiry", expires="not a date")

    _cleanup_expired_uploads(tmp_path)

    assert (tmp_path / "invalid-expiry").exists()
    assert (tmp_path / "invalid-expiry.info").exists()


def test_cleanup_removes_expired_uploads(tmp_path: Path) -> None:
    expired_at = format_datetime(
        datetime.now(UTC) - timedelta(days=1),
        usegmt=True,
    )
    _write_upload(tmp_path, "expired-upload", expires=expired_at)

    _cleanup_expired_uploads(tmp_path)

    assert not (tmp_path / "expired-upload").exists()
    assert not (tmp_path / "expired-upload.info").exists()


def test_cleanup_keeps_uploads_not_yet_expired(tmp_path: Path) -> None:
    not_yet = format_datetime(datetime.now(UTC) + timedelta(days=1), usegmt=True)
    _write_upload(tmp_path, "fresh-upload", expires=not_yet)

    _cleanup_expired_uploads(tmp_path)

    assert (tmp_path / "fresh-upload").exists()
    assert (tmp_path / "fresh-upload.info").exists()


def test_cleanup_keeps_uploads_with_no_expiry_recorded(tmp_path: Path) -> None:
    # Never delete something we have no explicit expiry for -- safer to
    # leak a stray file than to silently destroy an in-progress upload
    # whose `.info` hasn't been stamped with `expires` yet for some reason.
    _write_upload(tmp_path, "no-expiry", expires=None)

    _cleanup_expired_uploads(tmp_path)

    assert (tmp_path / "no-expiry").exists()
    assert (tmp_path / "no-expiry.info").exists()


def test_cleanup_skips_malformed_info_files_without_crashing(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.info").write_text("{not json")
    (tmp_path / "broken").write_bytes(b"data")

    _cleanup_expired_uploads(tmp_path)  # must not raise

    assert (tmp_path / "broken").exists()


def test_cleanup_on_a_missing_directory_is_a_no_op(tmp_path: Path) -> None:
    _cleanup_expired_uploads(tmp_path / "does-not-exist")  # must not raise
