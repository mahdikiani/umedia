"""Tool caches and dependency trees that are never library content.

The import walk already skips hidden and dependency folders
(`is_ignored_resource`) and keeps rows indexed under them. These are the
ones that should also leave the index: rows under them become `missing`.
Index only; the provider's files are never touched."""

JUNK_FOLDER_NAMES = frozenset({"node_modules", ".oh-my-zsh", ".antigen"})
JUNK_FOLDER_PATHS = frozenset({".kube/cache"})


def is_junk_folder(resource_id: str, name: str) -> bool:
    return name in JUNK_FOLDER_NAMES or any(
        resource_id == path or resource_id.endswith(f"/{path}")
        for path in JUNK_FOLDER_PATHS
    )
