"""Tool caches and dependency trees that are never library content.

The import walk already skips hidden and dependency folders
(`is_ignored_resource`) and keeps rows indexed under them. These are the
ones that should also leave the index: rows under them become `missing`.
Index only; the provider's files are never touched."""

JUNK_FOLDER_NAMES = frozenset({"node_modules", ".oh-my-zsh", ".antigen", ".cache"})
JUNK_FOLDER_PATHS = frozenset({".kube/cache"})


def is_junk_folder(resource_id: str, name: str) -> bool:
    return name in JUNK_FOLDER_NAMES or any(
        resource_id == path or resource_id.endswith(f"/{path}")
        for path in JUNK_FOLDER_PATHS
    )


def is_under_junk(resource_id: str) -> bool:
    """Whether the path sits inside a junk folder at any depth, including
    one below a hidden folder the walk never enters (".kube" -> "cache",
    ".opencode" -> "node_modules")."""
    parts = resource_id.split("/")
    folders = parts[:-1] if len(parts) > 1 else []
    if any(part in JUNK_FOLDER_NAMES for part in folders):
        return True
    folder_path = "/".join(folders)
    return any(
        folder_path == path
        or folder_path.startswith(f"{path}/")
        or f"/{path}/" in f"/{folder_path}/"
        for path in JUNK_FOLDER_PATHS
    )
