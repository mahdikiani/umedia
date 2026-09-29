# Security

UMedia is intended to run on infrastructure controlled by its operator. The operator is responsible for keeping the host, Docker installation, DNS, TLS termination, and backups secure.

The application stores provider connection credentials encrypted in its SQLite database. Provider integrations run through adapter processes; credentials are provided to the adapter for operations and are not intended to be persisted by the adapter. This design does not replace a security audit of the application or its dependencies.

## Safe operation

- Use HTTPS through a correctly configured reverse proxy when exposing an instance beyond localhost.
- Keep the SQLite database, encryption key, OAuth credentials, Telegram sessions, and backups private.
- Do not expose the API or S3-compatible endpoint directly to the public internet without understanding its authentication and access-control behavior.
- Keep independent backups of provider data and UMedia's `volumes/data` and `volumes/storage` directories.
- Never share passwords, access tokens, OAuth client secrets, session strings, or unredacted logs in GitHub Issues.

## Report a vulnerability

Do not publish an unpatched security issue in the public issue tracker. Email [mahdikiany@gmail.com](mailto:mahdikiany@gmail.com) with a concise description and safe reproduction details. Do not include live credentials or other people's data.
