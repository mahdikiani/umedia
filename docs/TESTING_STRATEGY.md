# Testing Strategy

> Still current — see also
> [`08-implementation-plan.md`](./08-implementation-plan.md)'s
> "Development process: TDD, every phase" for how this applies to the
> plugin contract specifically.


## Methodology

Development must follow TDD.


Workflow:


1. Write failing test

2. Implement feature

3. Refactor

4. Run full test suite



## Backend Tests


Framework:

pytest



## Unit Tests


Test:

- Services
- Repositories
- Domain logic
- Provider logic



## Integration Tests


Test:

- REST API
- Database
- Provider interactions



## Provider Tests


Each provider must have isolated tests.


Example:


tests/

providers/

    test_local_storage.py

    test_s3_storage.py

    test_telegram_storage.py



## Coverage


Core business logic should maintain minimum 80% coverage.



