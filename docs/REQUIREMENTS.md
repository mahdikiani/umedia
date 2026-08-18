# Functional Requirements

> **Superseded** by the `00`–`09` doc series (start at
> [`01-prd.md`](./01-prd.md)). Kept for reference — much of this is still
> accurate at the requirement level, just reframed around `Resource`
> instead of `MediaFile`/`StorageObject`.


# 1. Deployment


The application must support self-hosted single-node deployment.


Requirements:

- Docker based deployment
- Docker Compose support
- Persistent data volumes
- No external services required for basic operation


The default deployment represents one tenant.

No multi-user management is required in MVP.



# 2. Provider Management


The system must support adding multiple storage providers.


Supported providers:


Initial:

- Local filesystem
- S3 compatible storage
- Google Drive
- Telegram
- FTP
- SFTP



A provider type can have multiple connections.


Example:


S3:

- AWS account
- Backblaze account
- MinIO instance



Each provider connection must support:


- Create
- Read
- Update
- Delete
- Connection test
- Capacity information



Provider credentials must be stored encrypted.



# 3. Storage Abstraction


All providers must implement a common storage interface.


Required operations:


- List files
- Get metadata
- Read file
- Write file
- Delete file
- Move file
- Copy file
- Get capacity



Business logic must work independently of provider implementation.



# 4. File Management


Users must be able to:


- Browse files
- View metadata
- Upload files
- Download files
- Delete files
- Rename files
- Move files
- Copy files



The system must separate:


Logical file:

MediaFile


Physical storage:

StorageObject



A single MediaFile may have multiple StorageObjects.



# 5. Public Sharing


The system must support public file sharing.


Requirements:


- Generate public URL
- Optional expiration time
- Optional password protection
- Download tracking


Public URLs must reference MediaFile.


Changing provider connection must not invalidate existing share links.



# 6. Search


The system must provide global search.


Search must work across all providers.


Initial search fields:


- Filename
- Path
- MIME type
- Metadata



Architecture:


Storage

↓

Indexer

↓

Search Index

↓

Search API



The search index is not the source of truth.



# 7. Background Jobs


The system requires background workers.


Jobs include:


- Provider synchronization
- Metadata indexing
- Search indexing
- Long running file operations



Jobs must have:


- Status
- Progress
- Error information
- Retry capability



# 8. API Requirements


All functionality must be available through REST API.


API requirements:


- Versioned endpoints
- Proper HTTP methods
- Correct status codes
- Pagination
- Filtering
- Sorting
- Consistent error format



Example:


GET /api/v1/media-files


GET /api/v1/providers


POST /api/v1/providers



# 9. Frontend Requirements


Frontend must provide:


## Dashboard

Display:

- Connected providers
- Used capacity
- Available capacity
- Recent activity



## Provider Management

Users can:

- Add provider
- Configure provider
- Test connection
- Remove provider



## File Explorer

Support:

- Folder navigation
- File operations
- Upload
- Download



## Search UI

Support:

- Search query
- Results list
- File preview



# 10. S3 Gateway (Future)


The architecture should allow exposing the virtual filesystem through S3 compatible API.


Possible operations:


- List buckets
- List objects
- Upload objects
- Download objects



# 11. Future Cloud Version


Architecture should allow future migration to:


- Multi tenant
- External authentication
- SSO
- Billing
- User management



The MVP must not implement these features.



