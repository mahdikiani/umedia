# Universal Media Manager

> **Superseded** by the `00`–`09` doc series (start at
> [`00-product-vision.md`](./00-product-vision.md)), which reframes this as
> a Universal Data Layer (resource abstraction, not filesystem abstraction).
> Kept for reference — its HATEOAS/security detail and the fuller Persian
> PRD pasted during planning (see
> [`08-implementation-plan.md`](./08-implementation-plan.md)) are still
> worth mining.

## Vision

Build a self-hosted universal storage aggregation platform.

Users can connect multiple storage providers and manage all files from one unified interface.

## Deployment

Primary target:

Self-hosted single-node Docker deployment.

One installation represents one tenant.

No user management in MVP.

## Providers

Initial providers:

- Local filesystem
- S3 compatible storage
- Google Drive
- Telegram
- FTP
- SFTP


Future:

- Dropbox
- OneDrive
- WebDAV
- Nextcloud


## Core Concepts

### MediaFile

Logical representation of a file.

Visible to users.

### StorageObject

Physical representation stored inside a provider.

A MediaFile can have multiple StorageObjects.


Example:

photo.jpg

    |
    +-- AWS S3 object
    +-- Google Drive object
    +-- Local backup


### ProviderConnection

Configuration of a provider connection.

Multiple connections of the same provider are supported.


Example:

S3:

- AWS Personal
- Backblaze Backup


## Features

### Provider Management

- Add provider
- Remove provider
- Test connection
- Show capacity


### File Management

- Browse files
- Upload
- Download
- Delete
- Rename
- Move
- Copy


### Public Sharing

Files can generate public links.

Links belong to MediaFile, not StorageObject.

Changing storage provider must not break links.


### Search

Search is an independent indexing layer.

Storage is the source of truth.

Architecture inspired by Quickwit:

Index != Storage


## Future

- AI semantic search
- OCR
- Thumbnail generation
- S3 gateway
- Cloud SaaS version

