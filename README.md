# UMedia

**A self-hosted Universal Data Layer.**

UMedia connects your storage and messaging providers — local disks, S3,
Google Drive, Telegram, and more — behind one unified, provider-agnostic API.
It is a **resource abstraction layer, not a filesystem**: a Telegram message
is a valid resource just like an S3 object or a local file, folders are
optional, and the core never assumes everything is hierarchical.

- **Provider agnostic** — every external service is an isolated plugin;
  connect as many instances of the same provider type as you want (e.g. two
  S3 accounts, a personal and a work Google Drive).
- **Plugin based** — providers run as supervised subprocesses, reachable
  from the core only over a local REST contract. A provider can misbehave
  without taking the core down or reading its database.
- **Security first** — credentials are encrypted at rest, decrypted only in
  the trusted core, and never touch a plugin's disk.
- **API first** — every capability is a REST endpoint (see
  [`docs/05-api-design.md`](docs/05-api-design.md)); the frontend is just
  another client.
- **Self-hostable** — one `docker compose up`, one SQLite database, no
  external services required.

## Documentation

Start at [`docs/00-product-vision.md`](docs/00-product-vision.md) and read
through the numbered series (`00`–`09`) in order:

| Doc | Covers |
|---|---|
| [`00-product-vision.md`](docs/00-product-vision.md) | Why this exists |
| [`01-prd.md`](docs/01-prd.md) | Target users, main features |
| [`02-architecture.md`](docs/02-architecture.md) | High-level architecture, deployment shape, isolation model |
| [`03-provider-system.md`](docs/03-provider-system.md) | Provider plugin contract |
| [`04-data-model.md`](docs/04-data-model.md) | `Resource` / `ProviderConnection` schema |
| [`05-api-design.md`](docs/05-api-design.md) | Concrete route list |
| [`06-roadmap.md`](docs/06-roadmap.md) | Phased roadmap |
| [`07-agent-instructions.md`](docs/07-agent-instructions.md) | Rules for anyone (human or AI agent) implementing this |
| [`08-implementation-plan.md`](docs/08-implementation-plan.md) | The current rebuild's design decisions and why |
| [`09-tasks.md`](docs/09-tasks.md) | Resumable, checkbox-tracked task list |

Everything else under `docs/` (ALLCAPS filenames) is earlier design work,
marked superseded but kept for reference — later ideas sometimes only exist
there.

## Project status

UMedia is being rebuilt around `apps/media` as the single backend (see
[`08-implementation-plan.md`](docs/08-implementation-plan.md) for why).
Track progress in [`09-tasks.md`](docs/09-tasks.md).

## Running it

```bash
docker network create traefik-net  # once, if it doesn't already exist
docker compose up --build
```

One backend container (SQLite, no external database), one frontend
container. See `compose.yaml` and `.env.example`.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE)
