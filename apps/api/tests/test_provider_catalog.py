from providers.catalog import PROVIDER_CATALOG


def test_catalog_includes_first_class_and_rclone_drives() -> None:
    provider_ids = set(PROVIDER_CATALOG)

    assert {"local", "s3", "telegram"} <= provider_ids
    assert {
        "google_drive",
        "onedrive",
        "dropbox",
        "webdav",
        "nextcloud",
        "ftp",
        "sftp",
    } <= provider_ids


def test_every_provider_declares_capabilities_and_configuration_fields() -> None:
    for provider in PROVIDER_CATALOG.values():
        assert provider.capabilities
        assert provider.status in {"available", "beta", "planned"}
        assert all(field.key and field.label for field in provider.fields)

