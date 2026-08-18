"""Public catalog of supported storage connection types."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConfigField:
    """One provider configuration input."""

    key: str
    label: str
    input_type: str = "text"
    required: bool = True
    secret: bool = False
    placeholder: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Provider capabilities and configuration contract."""

    id: str
    name: str
    description: str
    adapter: str
    status: str
    capabilities: tuple[str, ...]
    fields: tuple[ConfigField, ...]


FULL_CAPABILITIES = ("list", "read", "write", "delete", "move", "copy", "sync")


def rclone_provider(
    provider_id: str,
    name: str,
    description: str,
    *fields: ConfigField,
) -> ProviderDefinition:
    """Define a provider backed by the audited rclone adapter."""
    return ProviderDefinition(
        id=provider_id,
        name=name,
        description=description,
        adapter="rclone",
        status="beta",
        capabilities=FULL_CAPABILITIES,
        fields=fields,
    )


PROVIDER_CATALOG = {
    provider.id: provider
    for provider in [
        ProviderDefinition(
            id="local",
            name="Local filesystem",
            description="A directory mounted into the UMedia container.",
            adapter="native",
            status="available",
            capabilities=FULL_CAPABILITIES,
            fields=(
                ConfigField(
                    "root_path",
                    "Root path",
                    placeholder="/storage/library",
                ),
            ),
        ),
        ProviderDefinition(
            id="s3",
            name="S3 compatible",
            description="AWS S3, MinIO, Backblaze B2 and compatible services.",
            adapter="native",
            status="planned",
            capabilities=(*FULL_CAPABILITIES, "multipart", "range_read"),
            fields=(
                ConfigField("endpoint_url", "Endpoint URL"),
                ConfigField("bucket", "Bucket"),
                ConfigField("region", "Region", required=False),
                ConfigField("access_key_id", "Access key", secret=True),
                ConfigField(
                    "secret_access_key",
                    "Secret key",
                    input_type="password",
                    secret=True,
                ),
            ),
        ),
        ProviderDefinition(
            id="telegram",
            name="Telegram",
            description="Store media in a Telegram channel using MTProto.",
            adapter="native",
            status="planned",
            capabilities=("list", "read", "write", "delete", "copy", "sync"),
            fields=(
                ConfigField("api_id", "API ID"),
                ConfigField("api_hash", "API hash", secret=True),
                ConfigField("channel_id", "Channel ID"),
                ConfigField(
                    "session",
                    "Session",
                    input_type="password",
                    secret=True,
                ),
            ),
        ),
        rclone_provider(
            "google_drive",
            "Google Drive",
            "Google Drive including Shared Drives.",
            ConfigField("token", "OAuth token", input_type="password", secret=True),
            ConfigField("root_folder_id", "Root folder ID", required=False),
        ),
        rclone_provider(
            "onedrive",
            "Microsoft OneDrive",
            "OneDrive Personal, Business and SharePoint libraries.",
            ConfigField("token", "OAuth token", input_type="password", secret=True),
            ConfigField("drive_id", "Drive ID", required=False),
        ),
        rclone_provider(
            "dropbox",
            "Dropbox",
            "Dropbox and Dropbox Business storage.",
            ConfigField("token", "OAuth token", input_type="password", secret=True),
        ),
        rclone_provider(
            "webdav",
            "WebDAV",
            "Any standards-compliant WebDAV server.",
            ConfigField("url", "Server URL"),
            ConfigField("username", "Username"),
            ConfigField("password", "Password", input_type="password", secret=True),
        ),
        rclone_provider(
            "nextcloud",
            "Nextcloud",
            "Nextcloud storage through WebDAV.",
            ConfigField("url", "Nextcloud URL"),
            ConfigField("username", "Username"),
            ConfigField(
                "password",
                "App password",
                input_type="password",
                secret=True,
            ),
        ),
        rclone_provider(
            "ftp",
            "FTP / FTPS",
            "Classic FTP and encrypted FTPS servers.",
            ConfigField("host", "Host"),
            ConfigField("username", "Username"),
            ConfigField("password", "Password", input_type="password", secret=True),
            ConfigField("port", "Port", required=False, placeholder="21"),
        ),
        rclone_provider(
            "sftp",
            "SFTP",
            "SSH File Transfer Protocol servers.",
            ConfigField("host", "Host"),
            ConfigField("username", "Username"),
            ConfigField("password", "Password", input_type="password", secret=True),
            ConfigField("port", "Port", required=False, placeholder="22"),
        ),
    ]
}
