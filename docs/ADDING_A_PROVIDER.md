# Adding a Storage Provider

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; providers are now plugin processes with a REST contract, see
> [`03-provider-system.md`](./03-provider-system.md).

UMedia providers are async adapters. They contain transport-specific behavior,
not MediaFile, sharing, indexing, or reconciliation business logic.

## Contract

Implement `providers.base.StorageProvider`:

- connection test
- list and stat
- streaming read and write
- delete, move, and copy

The adapter returns normalized `ProviderEntry` values. It must never access API
requests or write logical MediaFile records.

## Registration

Add a `ProviderDefinition` to `providers/catalog.py` with:

- a stable lowercase identifier
- human-readable name and description
- adapter family (native async client)
- honest lifecycle status
- explicit capabilities
- configuration fields, including which values are secrets

All submitted configuration is allowlisted against these fields and encrypted
before SQLite persistence. API responses never contain encrypted or plaintext
configuration.

## Lifecycle status

- `available`: operation contract and integration tests pass.
- `beta`: connection configuration is supported but the adapter still needs
  broader provider-specific compatibility testing.
- `planned`: visible roadmap entry; configuration may be staged, but UMedia
  must not advertise file operations as ready.

Do not mark a provider available until its contract suite covers connection
failure, listing, streaming upload/download, move, copy, delete, path safety,
rate limits, and reconciliation behavior.

## Tests

Every provider must run the shared contract suite plus provider-specific tests.
External services should use isolated containers or official sandbox APIs; unit
tests should use protocol fakes rather than network calls.
