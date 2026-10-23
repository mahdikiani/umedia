"""Choosing a content hash from what a provider reports for free."""

#: Strongest first. Anything else a provider reports (dropbox, quickxor,
#: xet, ...) is accepted after these, in alphabetical order for stability.
_PREFERRED = ("sha256", "sha512", "sha1", "md5")


def pick_native_hash(hashes: dict[str, str] | None) -> tuple[str, str] | None:
    """The (algorithm, hex) to index, or None when the provider has none."""
    usable = {
        algorithm.lower(): value.strip().lower()
        for algorithm, value in (hashes or {}).items()
        if isinstance(value, str) and value.strip()
    }
    for algorithm in _PREFERRED:
        if algorithm in usable:
            return algorithm, usable[algorithm]
    if usable:
        algorithm = sorted(usable)[0]
        return algorithm, usable[algorithm]
    return None
