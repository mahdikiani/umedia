# Product overview

UMedia is a self-hosted media manager built around a library that can reference content held by different storage providers. The library owns the user-facing organization and permissions; provider adapters perform storage-specific operations.

## What is available

- A web interface for connecting providers and browsing a shared library.
- Upload, download, rename, copy, move, soft-delete, and restore flows, subject to provider capabilities.
- Provider synchronization for importing existing remote objects into the library.
- An HTTP API and an S3-compatible API surface.
- A Docker Compose deployment backed by SQLite.

## What is not available yet

- Full-text or semantic search across providers.
- A WebDAV server for mounting the library in desktop file browsers.
- AI-powered organization or an AI-agent access surface.
- Production-level verification of every provider and operation.

UMedia is not a cloud service with public signup. You run and administer your own instance.
