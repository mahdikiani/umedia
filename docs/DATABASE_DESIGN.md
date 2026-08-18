# Database Design

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; the concrete schema now lives in
> [`04-data-model.md`](./04-data-model.md).


## Database

SQLite


## ORM

SQLAlchemy


## Pattern

Repository + Service Layer


## Tables


## media_files

Logical files visible to users.


Fields:

id

name

mime_type

size

hash

created_at

updated_at



## storage_objects

Physical objects stored in providers.


Fields:

id

media_file_id

provider_connection_id

remote_path

remote_id

etag

status

created_at



## provider_connections

Provider configurations.


Fields:

id

type

name

encrypted_config

status

created_at



## share_links


Fields:

id

media_file_id

token

expires_at

password_hash

download_limit

created_at



## search_documents


Search index metadata.


Fields:

id

media_file_id

content

metadata_json

updated_at



## jobs


Background jobs.


Fields:

id

type

status

payload

created_at

updated_at

