"""Tests for PluginRegistry, the manifest-loading replacement for
providers/catalog.py + providers/factory.py (see docs/03-provider-system.md).
"""

import json
from pathlib import Path

from plugins.registry import PluginRegistry


def _plugin_dir(plugins_dir: Path, name: str, **manifest: object) -> None:
    directory = plugins_dir / name
    directory.mkdir(parents=True)
    data = {
        "id": name,
        "name": name.title(),
        "description": f"{name} plugin",
        "entrypoint": ["python", "-m", f"plugins.{name}.main"],
        **manifest,
    }
    (directory / "plugin.json").write_text(json.dumps(data))


def _multi_manifest(
    plugins_dir: Path, plugin: str, manifest_id: str, **kw: object
) -> None:
    directory = plugins_dir / plugin / "manifests"
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "id": manifest_id,
        "name": manifest_id.title(),
        "description": f"{manifest_id} via {plugin}",
        "entrypoint": ["python", "-m", f"plugins.{plugin}.main"],
        "process_id": plugin,
        **kw,
    }
    (directory / f"{manifest_id}.json").write_text(json.dumps(data))


def test_loads_single_manifest_plugins(tmp_path: Path) -> None:
    _plugin_dir(tmp_path, "local")
    _plugin_dir(tmp_path, "telegram")

    registry = PluginRegistry(tmp_path)

    ids = {manifest.id for manifest in registry.provider_types()}
    assert ids == {"local", "telegram"}


def test_loads_multi_remote_type_plugins_sharing_one_process(
    tmp_path: Path,
) -> None:
    _multi_manifest(tmp_path, "rclone", "s3")
    _multi_manifest(tmp_path, "rclone", "google_drive")

    registry = PluginRegistry(tmp_path)

    ids = {manifest.id for manifest in registry.provider_types()}
    assert ids == {"s3", "google_drive"}
    assert registry.process_ids() == {"rclone"}
    assert registry.entrypoint_for("rclone") == ["python", "-m", "plugins.rclone.main"]


def test_get_returns_none_for_an_unknown_provider_type(tmp_path: Path) -> None:
    registry = PluginRegistry(tmp_path)

    assert registry.get("does-not-exist") is None


def test_disabled_manifests_are_excluded_by_default(tmp_path: Path) -> None:
    _plugin_dir(tmp_path, "local")
    _plugin_dir(tmp_path, "telegram", enabled=False)

    registry = PluginRegistry(tmp_path)

    assert {m.id for m in registry.provider_types()} == {"local"}
    assert {m.id for m in registry.provider_types(enabled_only=False)} == {
        "local",
        "telegram",
    }
    # A disabled manifest's process doesn't need to be spawned.
    assert registry.process_ids() == {"local"}
    # ... but it's still individually retrievable (e.g. to show it as
    # disabled in the provider-types listing).
    assert registry.get("telegram") is not None


def test_process_ids_are_deduplicated_across_shared_manifests(
    tmp_path: Path,
) -> None:
    _multi_manifest(tmp_path, "rclone", "s3")
    _multi_manifest(tmp_path, "rclone", "google_drive")
    _multi_manifest(tmp_path, "rclone", "webdav")
    _plugin_dir(tmp_path, "local")

    registry = PluginRegistry(tmp_path)

    assert registry.process_ids() == {"rclone", "local"}


def test_empty_plugins_dir_yields_an_empty_registry(tmp_path: Path) -> None:
    registry = PluginRegistry(tmp_path)

    assert registry.provider_types() == []
    assert registry.process_ids() == set()
    assert registry.entrypoint_for("anything") is None
