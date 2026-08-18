# Non Functional Requirements

> **Superseded** by the `00`–`09` doc series (start at
> [`08-implementation-plan.md`](./08-implementation-plan.md)). Kept for
> reference; still broadly accurate, cross-check against
> [`02-architecture.md`](./02-architecture.md) for the concrete
> single-container/SQLite constraints.


## Architecture Quality


Follow clean architecture principles.


Business logic must not live in:

- API routes
- Database models
- Provider adapters


Layers:


API Layer

Service Layer

Repository Layer

Provider Layer



## REST API


HTTP APIs should follow REST maturity principles as much as practical.


Requirements:


- Use resources instead of RPC style endpoints
- Correct HTTP verbs
- Correct HTTP status codes
- Stateless requests
- Pagination for collections
- Filtering support
- Sorting support
- Consistent error responses


Examples:


Good:

GET /api/v1/media-files/123


Bad:

GET /api/getFile?id=123



## Security


- Encrypt provider credentials
- Never store plaintext secrets
- Validate uploaded files
- Prevent path traversal
- Do not expose provider credentials
- Public shares must use secure tokens



## Performance


- Avoid loading large files into memory
- Stream uploads/downloads
- Use background jobs for long operations
- Avoid unnecessary provider API calls



## Maintainability


Adding a new provider should not require modifying core business logic.


## Deployment


The application must run using:


docker compose up


No manual configuration steps.



