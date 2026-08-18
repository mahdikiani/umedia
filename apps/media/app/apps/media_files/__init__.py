"""MediaFile: the user library layer (docs/11-dual-layer-library.md).

What users browse at `/files`: the folder tree lives *only* here; content
is reached through a link to a `StorageObject` (apps/storage_objects).
Replaces the single-`resources` coupling (apps/resources, now unmounted).
"""
