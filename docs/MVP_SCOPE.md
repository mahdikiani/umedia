# MVP Scope

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference — its "single tenant, no multi-user in MVP" and
> "Index != Storage" calls still hold and are carried forward there.


## Goal

Build the first working self-hosted Universal Media Manager.


## Must Have


### Project Setup

- Monorepo
- Next.js frontend
- FastAPI backend
- Docker Compose
- SQLite


### Core Domain

Implement:

- MediaFile
- StorageObject
- ProviderConnection


### Provider System

Implement:

- Provider interface
- Provider registry


First provider:

Local filesystem


Second provider:

S3 compatible storage


### Frontend


Implement:

- Dashboard
- Provider management
- File explorer
- Upload
- Download


### API


Implement:

- Provider CRUD
- File CRUD
- File browsing
- Upload/download


### Testing


Implement:

- Unit tests
- API integration tests
- Provider tests



## NOT MVP


Do not implement:


- Multi-user authentication
- Billing
- Team management
- AI features
- OCR
- Semantic search
- S3 Gateway
- Cloud deployment



## Quality Goal


A clean foundation that allows future:

- More providers
- Search engine
- Cloud version
- AI features

