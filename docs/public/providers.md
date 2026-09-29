# Providers and verification status

Provider availability describes what the current code advertises. It is not a promise that every provider and operation has been tested against every real service.

| Provider | Current status | Notes |
|---|---|---|
| Local filesystem | Available | Uses a directory mounted into the API container. The provider is restricted to its configured root. |
| S3-compatible storage | Available, with a live-write caveat | Includes AWS S3, Cloudflare R2, MinIO, Backblaze B2, and compatible services. Listing and reads have been exercised against a test server; writes still need verification against real AWS or MinIO. |
| Google Drive | Beta | Uses the rclone adapter and OAuth credentials. Live account authorization and remote CRUD need more verification. |
| Microsoft OneDrive | Beta | Uses the rclone adapter and OAuth credentials. Live account authorization and remote CRUD need more verification. |
| Dropbox | Beta | Uses the rclone adapter and OAuth credentials. Live account authorization and remote CRUD need more verification. |
| Telegram | Beta | Stores channel documents. Real Telegram use has not been verified; interactive phone/code/2FA login is not finished. Channels are flat, and renaming may re-upload the content. |

The rclone engine knows about additional backends, but WebDAV, Nextcloud, FTP, and SFTP are not advertised as supported UMedia providers and have not been validated end to end.

## Provider setup notes

- S3-compatible services need a bucket and access credentials. For a custom endpoint, enter the endpoint URL and provider-specific region/profile values.
- Google Drive, OneDrive, and Dropbox need OAuth application credentials configured for the API container. Follow each provider's current configuration docs before enabling the connection.
- Telegram needs Telegram application credentials and an authorized account session. Do not paste session strings into public issues or logs.
- Local storage is inside the host directory mounted into the container; it is not arbitrary host-wide filesystem access.

If a provider matters to your workflow, test the exact operations and account type you intend to use before trusting it with primary data.
