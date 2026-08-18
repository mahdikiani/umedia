# Agent Development Rules


## Before Coding

Before implementing any feature:

1. Read all documentation files.
2. Understand existing architecture.
3. Create an implementation plan.
4. Identify affected modules.
5. Start coding only after the plan is clear.


## Architecture Rules


Do not change the architecture without explanation.


Frontend:

- Next.js
- TypeScript
- shadcn/ui


Backend:

- FastAPI
- SQLAlchemy
- SQLite


Use:

API Layer

↓

Service Layer

↓

Repository Layer

↓

Provider Layer



## Business Logic


Business logic MUST NOT live in:

- API routes
- Database models
- React components
- Storage providers


## Provider Rules


Storage providers are adapters only.


A provider should only know how to:

- Read
- Write
- Delete
- List
- Stat
- Move
- Copy


Do not put MediaFile or Sharing logic inside providers.


## Database Rules


Do not access database directly from API routes.


Use:

Repository

Service

Model


## API Rules


All APIs must:

- Follow REST conventions
- Use proper HTTP methods
- Use proper status codes
- Have consistent error responses


## Testing Rules


Follow TDD.


For every feature:

1. Write tests first.
2. Implement.
3. Refactor.
4. Run tests.


No feature is complete without tests.


## Dependencies


Do not add dependencies without justification.


Prefer existing libraries.


## Code Quality


Write:

- Clean
- Typed
- Documented
- Maintainable code


Avoid:

- Duplicate logic
- Magic values
- Tight coupling


## Communication


Before large architectural decisions:

Explain:

- Problem
- Alternatives
- Decision
- Tradeoffs

