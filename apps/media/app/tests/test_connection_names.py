"""Connection names double as S3 bucket names in the public S3 API, so
they follow S3 bucket naming rules (minus dots, which break
`{bucket}.{endpoint}` virtual-host routing)."""

import pytest

from apps.provider_connections.names import (
    RESERVED_NAMES,
    InvalidConnectionName,
    slugify_connection_name,
    unique_connection_name,
    validate_connection_name,
)


@pytest.mark.parametrize(
    "name",
    ["a", "7", "ab", "abc", "my-nas", "r2-media", "x" * 63, "0-backup", "media-2"],
)
def test_valid_names(name: str) -> None:
    assert validate_connection_name(name) == name


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("", "1 and 63"),
        ("x" * 64, "1 and 63"),
        ("My-NAS", "lowercase"),
        ("my_nas", "lowercase"),
        ("my nas", "lowercase"),
        ("my.nas", "lowercase"),
        ("-nas", "start and end"),
        ("nas-", "start and end"),
        ("my--nas", "consecutive"),
        ("192-168-1-1", None),  # digits + hyphens are fine
        ("xn--abc", "xn--"),
        ("nas-s3alias", "-s3alias"),
        ("nas--ol-s3", "consecutive"),
        ("sthree-nas", "sthree-"),
        ("umedia", "reserved"),
        ("فایل", "lowercase"),
    ],
)
def test_invalid_names(name: str, reason: str | None) -> None:
    if reason is None:
        assert validate_connection_name(name) == name
        return
    with pytest.raises(InvalidConnectionName, match=reason):
        validate_connection_name(name)


def test_reserved_includes_the_library_bucket() -> None:
    from server.config import Settings

    assert Settings.S3_COMPAT_BUCKET in RESERVED_NAMES


@pytest.mark.parametrize(
    ("raw", "slug"),
    [
        ("Local filesystem", "local-filesystem"),
        ("Microsoft OneDrive", "microsoft-onedrive"),
        ("Google Drive OAuth Demo", "google-drive-oauth-demo"),
        ("cloudflare", "cloudflare"),
        ("SFTP", "sftp"),
        ("  My__NAS..Backup!! ", "my-nas-backup"),
        ("Hugging Face Buckets", "hugging-face-buckets"),
        ("FTP / FTPS", "ftp-ftps"),
        ("ab", "ab"),
        ("X", "x"),
        ("", "storage"),
        ("فایل‌های من", "storage"),
        ("umedia", "umedia-storage"),
        ("xn--test", "xn-test"),
        ("sthree-x", "x"),
        ("y" * 80, "y" * 63),
    ],
)
def test_slugify_always_yields_a_valid_name(raw: str, slug: str) -> None:
    assert slugify_connection_name(raw) == slug
    assert validate_connection_name(slug) == slug


def test_unique_name_appends_a_counter_within_63_chars() -> None:
    taken = {"nas", "nas-2", "z" * 63}
    assert unique_connection_name("nas", taken) == "nas-3"
    assert unique_connection_name("free", taken) == "free"
    fitted = unique_connection_name("z" * 63, taken)
    assert fitted == "z" * 61 + "-2"
    assert validate_connection_name(fitted) == fitted


@pytest.mark.parametrize(
    ("provider_type", "config", "variant"),
    [
        ("s3", {"endpoint_url": "https://abc.r2.cloudflarestorage.com"}, "cloudflare"),
        ("s3", {"endpoint_url": "https://s3.us-west-002.backblazeb2.com"}, "backblaze"),
        ("s3", {"endpoint_url": "https://s3.wasabisys.com"}, "wasabi"),
        ("s3", {"endpoint_url": "https://fra1.digitaloceanspaces.com"}, "digitalocean"),
        (
            "s3",
            {"endpoint_url": "https://minio.local:9000", "provider": "Minio"},
            "minio",
        ),
        ("s3", {}, "aws"),
        ("s3", {"endpoint_url": "https://garage.example.com"}, None),
        ("webdav", {"vendor": "nextcloud"}, "nextcloud"),
        ("webdav", {"vendor": "OwnCloud"}, "owncloud"),
        ("webdav", {"vendor": "other"}, None),
        ("ftp", {"host": "h"}, None),
    ],
)
def test_connection_variant(
    provider_type: str, config: dict, variant: str | None
) -> None:
    from apps.provider_connections.names import connection_variant

    assert connection_variant(provider_type, config) == variant
