"""Provider plugin manifest schema.

One `plugin.json` per manifest entry, discovered by `registry.py`. This is
the plugin-era replacement for `providers/catalog.py`'s hardcoded
`PROVIDER_CATALOG` dict -- see docs/03-provider-system.md.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ConfigField(BaseModel):
    """One provider configuration input.

    Same shape as the old `providers.catalog.ConfigField` on purpose --
    `apps/provider_connections` (Phase 1) and the frontend's `StorageDialog`
    already consume this exact structure and don't need to change when
    Phase 3 swaps the catalog source from `providers/catalog.py` to this.
    """

    key: str
    label: str
    input_type: str = "text"
    required: bool = True
    secret: bool = False
    placeholder: str | None = None
    server_managed: bool = False
    environment_variable: str | None = None
    # Stored and sent to the plugin, but set by a flow, not typed into the
    # form -- e.g. sftp `host_key`, pinned by the trust-this-host prompt.
    hidden: bool = False


class PluginManifest(BaseModel):
    """A provider plugin's declared identity, launch command, and config
    contract, loaded from one `plugin.json` file."""

    id: str
    name: str
    description: str
    entrypoint: list[str] = Field(
        description=(
            "Argv used to launch the plugin's ASGI app. "
            "PluginProcessManager appends the socket path as the final "
            "argument -- see plugins/process_manager.py."
        ),
    )
    process_id: str | None = Field(
        default=None,
        description=(
            "Which running plugin *process* serves this manifest entry. "
            "Defaults to `id`. Several manifest entries can share one "
            "process_id so one subprocess serves all of them -- e.g. every "
            "rclone remote type (s3, google_drive, ...) is one manifest "
            "each, sharing the single `rclone` plugin process."
        ),
    )
    status: str = "available"
    capabilities: tuple[str, ...] = ()
    config_fields: tuple[ConfigField, ...] = ()
    remote_type: str | None = Field(
        default=None,
        description=(
            "For plugins fronting multiple remote types (e.g. `rclone`), "
            "which one this manifest entry configures."
        ),
    )
    enabled: bool = True
    connect_flow: Literal["token", "oauth", "session"] = Field(
        default="token",
        description=(
            "Which sub-flow a frontend should offer to configure this "
            "provider type: 'token' (single POST /providers with config "
            "fields — local, s3), 'oauth' (localhost-redirect paste flow "
            "via /providers/oauth/start + /complete — google_drive), or "
            "'session' (multi-step interactive sign-in, e.g. telegram: "
            "phone -> code -> maybe 2FA; not built yet)."
        ),
    )

    @property
    def process_key(self) -> str:
        """The plugin process this manifest entry's connections route to."""
        return self.process_id or self.id

    @classmethod
    def load(cls, path: Path) -> "PluginManifest":
        """Parse and validate one `plugin.json` file."""
        return cls.model_validate_json(Path(path).read_text())
