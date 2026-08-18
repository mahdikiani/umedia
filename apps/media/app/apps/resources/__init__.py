"""The `Resource` domain -- docs/04-data-model.md.

Replaces `apps/files`'s Beanie-based `MediaFile`/`ObjectMetaData` (Phase 4,
docs/09-tasks.md). A `Resource` is provider-neutral: filesystem is only one
possible provider (docs/00-product-vision.md) -- `type` is an open string,
`parent_id` is optional.
"""
