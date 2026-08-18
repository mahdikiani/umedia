# Contributing to UMedia

## Before you start

1. Read [`docs/00-product-vision.md`](docs/00-product-vision.md) through
   [`docs/09-tasks.md`](docs/09-tasks.md), in order. This project has a
   deliberate architecture (resource abstraction, not filesystem; provider
   isolation via subprocess + REST) — familiarize yourself before proposing
   changes to it.
2. Check [`docs/09-tasks.md`](docs/09-tasks.md) for the current, resumable
   task list before starting new work, so you don't duplicate in-flight
   effort.
3. If you're an AI coding agent, [`docs/07-agent-instructions.md`](docs/07-agent-instructions.md)
   and `AGENTS.md` are binding, not optional.

## Development rules

These apply to every change, human or agent-authored (from
`docs/07-agent-instructions.md` / `AGENTS.md`):

- Don't tightly couple the core to a specific provider. Every external
  service is a provider plugin behind the REST contract in
  [`docs/03-provider-system.md`](docs/03-provider-system.md).
- Business logic lives in the Service layer — not in API routes, not in
  database models, not in provider plugins (which are transport adapters
  only). Route → Service → Repository → Model/Plugin-client.
- **TDD**: write the failing test first, then the implementation, then
  refactor. See [`docs/08-implementation-plan.md`](docs/08-implementation-plan.md)'s
  "Development process" section and [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md).
- Keep provider-plugin interfaces stable; changes to the shared contract
  need the contract test suite updated first, then every plugin brought
  back to green.
- Before a large architectural change: explain the problem, the
  alternatives considered, the decision, and the tradeoffs — in the PR
  description at minimum, in a `docs/` update if it changes a standing
  decision.

## Making a change

```bash
git checkout -b your-branch-name
# apps/media: uv sync && uv run pytest
# apps/web:   bun install && bun test
```

- Add/adjust tests for anything you touch. Coverage thresholds are enforced
  in CI (`pyproject.toml`'s `[tool.coverage.report]`).
- Run the linter (`uv run ruff check .` for Python, `bun run lint` for the
  frontend) before opening a PR.
- Update the relevant `docs/0X-*.md` file if your change affects the
  architecture, data model, or API surface described there — and tick the
  matching box in [`docs/09-tasks.md`](docs/09-tasks.md) if it closes a
  tracked task.

## Reporting issues

Open a GitHub issue with steps to reproduce, expected vs actual behavior,
and — for provider bugs — which provider/plugin is involved.
