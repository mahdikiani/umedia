# Domain Model

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; `MediaFile`/`StorageObject` below became a single `Resource`
> entity — filesystem is only one possible provider, see
> [`04-data-model.md`](./04-data-model.md).


## MediaFile

Logical file representation.


Fields:

id

name

mime_type

size

hash

created_at

updated_at



## StorageObject

Physical representation in a provider.


Fields:

id

media_file_id

provider_connection_id

remote_path

remote_id

etag

status



## ProviderConnection

Storage provider configuration.


Fields:

id

type

name

encrypted_config

status



## ShareLink


Fields:

id

media_file_id

token

expires_at

password_hash

download_limit



## Relationship


MediaFile

    |

    +---- StorageObject

              |

              +---- ProviderConnection


