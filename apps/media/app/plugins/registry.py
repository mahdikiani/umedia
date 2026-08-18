"""Loads provider plugin manifests from disk.

Replaces `providers/catalog.py`'s hardcoded `PROVIDER_CATALOG` dict and
`providers/factory.py`'s `create_provider` -- see
docs/03-provider-system.md. `apps/provider_connections` keeps using the old
catalog/factory until Phase 3 has real plugin directories for it to point
at instead; this module is the loading mechanism, built ahead of that swap.
"""

from pathlib import Path

from .manifest import PluginManifest


class PluginRegistry:
    """In-memory view of every plugin manifest under a plugins directory.

    Layout: `<plugins_dir>/<plugin>/plugin.json` for a plugin with one
    manifest entry (`local`, `telegram`), or
    `<plugins_dir>/<plugin>/manifests/<remote_type>.json` for a plugin
    fronting several remote types under one process (`rclone`).
    """

    def __init__(self, plugins_dir: Path) -> None:
        self._plugins_dir = plugins_dir
        self._manifests: dict[str, PluginManifest] = {}
        self._load()

    def _load(self) -> None:
        paths = [
            *self._plugins_dir.glob("*/plugin.json"),
            *self._plugins_dir.glob("*/manifests/*.json"),
        ]
        for manifest_path in sorted(paths):
            manifest = PluginManifest.load(manifest_path)
            self._manifests[manifest.id] = manifest

    def provider_types(self, *, enabled_only: bool = True) -> list[PluginManifest]:
        """All known provider types, sorted by id (matches the old
        catalog's dict-of-`ProviderDefinition` shape, just sourced from
        disk instead of hardcoded)."""
        manifests = self._manifests.values()
        if enabled_only:
            manifests = (m for m in manifests if m.enabled)
        return sorted(manifests, key=lambda manifest: manifest.id)

    def get(self, provider_type: str) -> PluginManifest | None:
        """The manifest for one provider type id, or None if unknown."""
        return self._manifests.get(provider_type)

    def process_ids(self) -> set[str]:
        """Distinct plugin processes that need to be running to serve
        every *enabled* manifest -- what `PluginProcessManager` should
        spawn at startup."""
        return {manifest.process_key for manifest in self.provider_types()}

    def entrypoint_for(self, process_key: str) -> list[str] | None:
        """The argv to launch the plugin process serving `process_key`."""
        for manifest in self._manifests.values():
            if manifest.process_key == process_key:
                return manifest.entrypoint
        return None
