"""Tests for the plugin manifest schema (replaces providers/catalog.py's
hardcoded ProviderDefinition dict, see docs/03-provider-system.md)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from plugins.manifest import ConfigField, PluginManifest


def _write_manifest(path: Path, **overrides: object) -> Path:
    data = {
        "id": "local",
        "name": "Local filesystem",
        "description": "A directory mounted into the container.",
        "entrypoint": ["python", "-m", "plugins.local.main"],
        "capabilities": ["list", "read", "write"],
        "config_fields": [
            {"key": "root_path", "label": "Root path"},
        ],
        **overrides,
    }
    path.write_text(json.dumps(data))
    return path


def test_load_parses_a_manifest_file(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "plugin.json")

    manifest = PluginManifest.load(path)

    assert manifest.id == "local"
    assert manifest.entrypoint == ["python", "-m", "plugins.local.main"]
    assert manifest.config_fields == (ConfigField(key="root_path", label="Root path"),)
    assert manifest.enabled is True


def test_process_key_defaults_to_id(tmp_path: Path) -> None:
    manifest = PluginManifest.load(_write_manifest(tmp_path / "plugin.json"))

    assert manifest.process_key == "local"


def test_process_key_can_be_shared_across_manifests(tmp_path: Path) -> None:
    """The rclone case: several manifest entries, one plugin process."""
    s3 = PluginManifest.load(
        _write_manifest(
            tmp_path / "s3.json",
            id="s3",
            process_id="rclone",
            entrypoint=["python", "-m", "plugins.rclone.main"],
        ),
    )
    drive = PluginManifest.load(
        _write_manifest(
            tmp_path / "drive.json",
            id="google_drive",
            process_id="rclone",
            entrypoint=["python", "-m", "plugins.rclone.main"],
        ),
    )

    assert s3.process_key == drive.process_key == "rclone"


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"id": "local"}))

    with pytest.raises(ValidationError):
        PluginManifest.load(path)
