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

## Quick start with Docker images

Download the sample environment and Compose file, then start the published images:

```bash
curl -fsSLO https://raw.githubusercontent.com/mahdikiani/umedia/main/.env.docker.example
mv .env.docker.example .env
curl -fsSLO https://raw.githubusercontent.com/mahdikiani/umedia/main/compose.release.yaml
curl -fsSLO https://raw.githubusercontent.com/mahdikiani/umedia/main/Caddyfile.release
docker compose -f compose.release.yaml pull
docker compose -f compose.release.yaml up -d
```

Open <http://localhost:8080>. The sample uses local storage and keeps the database and files in `./volumes/`. Change `UMEDIA_MASTER_KEY` in `.env` before storing real data, and keep a backup of both that key and the volume. Optional cloud-provider credentials and public-domain setup are covered in [Getting Started](https://mahdikiani.github.io/umedia/getting-started/). To use a different release, set `UMEDIA_VERSION` in `.env` to its version tag.

## Support and contributions

Use [GitHub Issues](https://github.com/mahdikiani/umedia/issues) for bugs and support requests. Include the UMedia version or commit, provider type, steps to reproduce, and relevant redacted logs. Never post passwords, tokens, session strings, or private files.

See [Contributing](https://mahdikiani.github.io/umedia/contributing/) before opening a pull request. For direct contact, email [mahdikiany@gmail.com](mailto:mahdikiany@gmail.com) or Telegram [@mahdikiani](https://t.me/mahdikiani).

## License

UMedia is licensed under the [MIT License](LICENSE).
