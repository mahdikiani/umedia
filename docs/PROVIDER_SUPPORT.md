# Provider Support Matrix

> **Superseded** by [`09-tasks.md`](./09-tasks.md) (Phase 3 + Backlog),
> which reflects the actual providers being shipped this pass
> (local/S3/Google Drive via `rclone`/Telegram) versus deferred. Kept for
> reference.

| Provider | Adapter | Status | Notes |
|---|---|---:|---|
| Local filesystem | Native async | Available | Mounted paths inside `/storage` |
| S3 compatible | Native async | Planned | AWS, MinIO, Backblaze and compatible |
| Telegram | Native MTProto | Planned | Channel-backed object storage |
| Google Drive | Native async | Planned | OAuth connection flow |
| Microsoft OneDrive | Native async | Planned | Microsoft Graph |
| Dropbox | Native async | Planned | Dropbox API v2 |
| WebDAV | Native async | Planned | Generic WebDAV |
| Nextcloud | Native WebDAV | Planned | App-password authentication |
| FTP/FTPS | Native async | Planned | Explicit capability limitations apply |
| SFTP | Native async | Planned | Password and key authentication |

“Beta” does not mean every file operation is production-ready. A provider only
moves to Available after the shared storage contract suite and reconciliation
tests pass.
