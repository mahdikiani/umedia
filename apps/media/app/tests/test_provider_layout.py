"""Core-owned dump paths on the local provider."""

import pytest

from apps.media_files.provider_layout import (
    is_umedia_managed_reference,
    local_upload_parent,
    plugin_upload_parent,
)


def test_local_upload_parent_namespaces_by_owner_and_file_uid() -> None:
    assert local_upload_parent(
        owner_id="user-1", media_file_uid="file-aaa",
    ) == ".umedia/users/user-1/file-aaa"


def test_plugin_upload_parent_only_namespaces_local() -> None:
    assert plugin_upload_parent(
        provider_type="local",
        owner_id="user-1",
        media_file_uid="file-aaa",
    ) == ".umedia/users/user-1/file-aaa"
    assert plugin_upload_parent(
        provider_type="s3",
        owner_id="user-1",
        media_file_uid="file-aaa",
    ) is None
    assert plugin_upload_parent(
        provider_type="telegram",
        owner_id="user-1",
        media_file_uid="file-aaa",
    ) is None


def test_umedia_managed_references_are_the_dump_tree() -> None:
    assert is_umedia_managed_reference(".umedia")
    assert is_umedia_managed_reference(".umedia/users/user-1/file-aaa/notes.txt")
    assert not is_umedia_managed_reference("docs/notes.txt")
    assert not is_umedia_managed_reference("users/notes.txt")


def test_local_upload_parent_rejects_path_segments() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        local_upload_parent(owner_id="user/../x", media_file_uid="file-aaa")
