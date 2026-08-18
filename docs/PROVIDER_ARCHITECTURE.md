# Provider Architecture

> **Superseded** by [`03-provider-system.md`](./03-provider-system.md),
> which has the concrete plugin process/REST contract. Kept for reference.

Storage providers are adapters only.

They must not contain business logic.


## Interface


Every provider must implement:


list(path)

stat(path)

read(path)

write(path)

delete(path)

move(source, target)

copy(source, target)

capacity()



## Implementations


LocalStorageProvider

S3StorageProvider

GoogleDriveProvider

TelegramProvider

FTPProvider

SFTPProvider



## Adding New Provider


Adding a new provider should only require:

1. New provider implementation
2. Provider registration
3. Provider tests


Core business logic must not change.


