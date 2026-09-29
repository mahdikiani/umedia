# UMedia

**A self-hosted media library for files spread across storage providers.**

UMedia brings local storage, S3-compatible object stores, and selected cloud and messaging providers into one library you can host yourself. It is open source under the MIT License.

**Status: early beta.** Provider support and verification differ. Check the [provider matrix](https://mahdikiani.github.io/umedia/providers/) before using UMedia with important data.

- [Documentation](https://mahdikiani.github.io/umedia/)
- [Source code](https://github.com/mahdikiani/umedia)
- [Report a problem](https://github.com/mahdikiani/umedia/issues)
- [Current hosted preview](https://umedia.uln.me) (maintainer preview; not a public signup service)

## What it does

- Browse a library that can reference content from more than one provider.
- Upload, download, organize, and transfer files between configured providers.
- Keep provider-specific integrations behind adapters, with the core managing the library and access rules.
- Run the application with Docker Compose, SQLite, and persistent local volumes.

Full-text search, an operating-system WebDAV mount, and AI organization are not available yet. See the [current limitations](https://mahdikiani.github.io/umedia/roadmap/).

## Run it

The checked-in `compose.yaml` is configured for an existing Traefik deployment. It expects an external Docker network named `traefik-net`, a running Traefik instance attached to that network, and host routing configured for `drive.uln.me`. This is not a standalone local Compose setup.

```bash
docker network create traefik-net
docker compose up --build -d
```

Before using another hostname, update the Traefik `Host(...)` rules in `compose.yaml` and configure the matching DNS and TLS routing. Provider OAuth credentials and Telegram application credentials must be passed into the API container; see [Getting Started](https://mahdikiani.github.io/umedia/getting-started/).

## Support and contributions

Use [GitHub Issues](https://github.com/mahdikiani/umedia/issues) for bugs and support requests. Include the UMedia version or commit, provider type, steps to reproduce, and relevant redacted logs. Never post passwords, tokens, session strings, or private files.

See [Contributing](https://mahdikiani.github.io/umedia/contributing/) before opening a pull request. For direct contact, email [mahdikiany@gmail.com](mailto:mahdikiany@gmail.com) or Telegram [@mahdikiani](https://t.me/mahdikiani).

## License

UMedia is licensed under the [MIT License](LICENSE).
