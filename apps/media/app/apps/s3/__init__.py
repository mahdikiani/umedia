"""S3-compatible gateway over the MediaFile library.

A thin protocol adapter (docs/02-architecture.md): SigV4 requests signed
with a per-user access key (`apps/user_access_keys`) are resolved to that
key's owner, and every object operation is delegated to the same
`MediaFileService` the REST routes use. Object URLs look like
`/api/v1/s3/{bucket}/{library-path}`.
"""
