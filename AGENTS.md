# Project Instructions

Read all documents in /docs before implementing.

This project is a self-hosted Universal Media Manager.

## Architecture

- Frontend and backend must be separated.
- Frontend: Next.js + TypeScript + shadcn/ui.
- Backend: FastAPI.
- Database: SQLite.
- ORM: SQLAlchemy.
- Use Repository + Service pattern.

## Core Concepts

MediaFile:
Logical user-facing file.

StorageObject:
Physical representation inside a storage provider.

ProviderConnection:
Storage provider configuration.


## Rules

- Do not put business logic in API routes.
- Do not put business logic in storage providers.
- Providers are adapters only.
- Follow TDD.
- Write tests before implementation.
- Follow REST API conventions.
- Use correct HTTP methods and status codes.
- Keep frontend and backend separated.
- Final product must run with docker compose.

## Development Process

Before coding:

1. Read docs.
2. Create implementation plan.
3. Confirm architecture.
4. Implement incrementally.

## Deployment

The product must work with:

docker compose up

