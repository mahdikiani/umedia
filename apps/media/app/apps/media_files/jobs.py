"""One trace, one id and one durable record per background job run.

Before this, every background job in a process shared the lifespan's
Sentry trace (all UMEDIA-2/4/6 events since a restart had one trace_id),
plugin calls carried no connection id, and a 40-minute sync walk that
failed left a single ERROR line. `job_run` wraps one sync walk or hash
pass:

- a fresh Sentry isolation scope and transaction: a trace of its own,
  tagged `job.kind`, `job.run_id`, `job.trigger`, `connection_id`;
- `plugins.tracing.current_run_id`, so each plugin call (and the plugin's
  log lines about it) carries the run id;
- a `job_runs` row: started/finished, status, counters, error;
- a start and a finish log line with the run id and duration, both
  inside the scope; a failed run's finish line is its GlitchTip event.

Bookkeeping is best effort: a failure to write the record never changes
the job's own outcome.
"""

import dataclasses
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import sentry_sdk
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugins.tracing import current_run_id

from .models import JobRun

logger = logging.getLogger(__name__)

#: Runs older than this are pruned when a new run of the same kind starts.
JOB_RUN_RETENTION = timedelta(days=90)


@dataclasses.dataclass(frozen=True)
class JobRunRecord:
    uid: str
    kind: str
    provider_connection_id: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    result: dict[str, Any]
    error: str | None


def _record(row: JobRun) -> JobRunRecord:
    return JobRunRecord(
        uid=row.uid,
        kind=row.kind,
        provider_connection_id=row.provider_connection_id,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        result=dict(row.result or {}),
        error=row.error,
    )


class JobRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start(self, data: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            session.add(JobRun(status="running", result={}, **data))
            await session.commit()

    async def finish(self, uid: str, changes: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            row = await session.get(JobRun, uid)
            if row is None:
                return
            for key, value in changes.items():
                setattr(row, key, value)
            await session.commit()

    async def list_for_connection(
        self,
        provider_connection_id: str,
        *,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[JobRunRecord]:
        async with self._session_factory() as session:
            query = select(JobRun).where(
                JobRun.provider_connection_id == provider_connection_id,
                JobRun.is_deleted.is_(False),
            )
            if kind is not None:
                query = query.where(JobRun.kind == kind)
            rows = await session.scalars(
                query.order_by(JobRun.started_at.desc()).limit(limit),
            )
            return [_record(row) for row in rows.all()]

    async def prune(self, *, kind: str, older_than: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(JobRun).where(
                    JobRun.kind == kind,
                    JobRun.started_at < older_than,
                ),
            )
            await session.commit()


@dataclasses.dataclass
class JobRunHandle:
    """What the job body reports back: its counters, and `partial` when it
    finished but left something out (a sync with unlisted folders)."""

    run_id: str
    result: dict[str, Any] = dataclasses.field(default_factory=dict)
    partial: bool = False


def _repository(state: Any) -> JobRunRepository | None:  # noqa: ANN401
    session_factory = getattr(state, "session_factory", None)
    return None if session_factory is None else JobRunRepository(session_factory)


async def _best_effort(action: str, run_id: str, call: Any) -> None:  # noqa: ANN401
    try:
        await call
    except Exception:
        logger.exception("Could not %s job run %s", action, run_id)


@asynccontextmanager
async def job_run(
    state: Any,  # noqa: ANN401 -- app.state or anything with session_factory
    *,
    kind: str,
    connection_id: str,
    trigger: str,
    run_id: str | None = None,
) -> AsyncIterator[JobRunHandle]:
    """Run one background job under its own trace, ids and record.

    Everything the run logs happens inside its isolation scope and
    transaction, the finish line included. A failed run's finish line is
    an ERROR with the traceback, so its GlitchTip event carries the job
    tags and the job's trace. The caller's own `logger.exception` for the
    same exception after it propagates is then dropped by the SDK's dedupe
    integration (same exception object), instead of being a second,
    untagged event on the process-wide trace.
    """
    handle = JobRunHandle(run_id=run_id or uuid4().hex)
    repository = _repository(state)
    started_at = datetime.now()
    started = time.monotonic()
    token = current_run_id.set(handle.run_id)
    try:
        with sentry_sdk.isolation_scope() as scope:
            # A trace of its own: events from this run are not lumped in
            # with every other background job since the process started.
            scope.set_new_propagation_context()
            scope.set_tag("job.kind", kind)
            scope.set_tag("job.run_id", handle.run_id)
            scope.set_tag("job.trigger", trigger)
            scope.set_tag("connection_id", connection_id)
            with sentry_sdk.start_transaction(
                op=f"job.{kind}",
                name=f"{kind} {connection_id}",
            ):
                if repository is not None:
                    await _best_effort(
                        "record the start of",
                        handle.run_id,
                        repository.start({
                            "uid": handle.run_id,
                            "kind": kind,
                            "provider_connection_id": connection_id,
                            "trigger": trigger,
                            "started_at": started_at,
                        }),
                    )
                logger.info(
                    "Job %s started: connection=%s trigger=%s run_id=%s",
                    kind,
                    connection_id,
                    trigger,
                    handle.run_id,
                )
                failure: BaseException | None = None
                try:
                    yield handle
                except BaseException as error:
                    failure = error
                    raise
                finally:
                    await _finish(
                        repository,
                        handle,
                        kind=kind,
                        connection_id=connection_id,
                        started_at=started_at,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        failure=failure,
                    )
    finally:
        current_run_id.reset(token)


async def _finish(
    repository: JobRunRepository | None,
    handle: JobRunHandle,
    *,
    kind: str,
    connection_id: str,
    started_at: datetime,
    duration_ms: int,
    failure: BaseException | None,
) -> None:
    """Record the end of a run, then log its finish line. Called inside the
    run's scope; the line comes last so that, for a failed run, it is the
    last exception reported before the caller sees it (dedupe compares
    against the last one only)."""
    if failure is None:
        status = "partial" if handle.partial else "ok"
        error_text = None
    else:
        status = "failed"
        error_text = f"{type(failure).__name__}: {failure}"[:2000]
    if repository is not None:
        await _best_effort(
            "record the end of",
            handle.run_id,
            repository.finish(
                handle.run_id,
                {
                    "status": status,
                    "finished_at": datetime.now(),
                    "duration_ms": duration_ms,
                    "result": handle.result,
                    "error": error_text,
                },
            ),
        )
        await _best_effort(
            "prune runs before",
            handle.run_id,
            repository.prune(kind=kind, older_than=started_at - JOB_RUN_RETENTION),
        )
    if isinstance(failure, Exception):
        log, exc_info = logger.error, failure
    else:
        # A partial run, or one cancelled at shutdown: not an error event.
        log, exc_info = (logger.info if status == "ok" else logger.warning), None
    log(
        "Job %s %s in %.1fs: connection=%s run_id=%s%s",
        kind,
        status,
        duration_ms / 1000,
        connection_id,
        handle.run_id,
        f" error={error_text}" if error_text else "",
        exc_info=exc_info,
    )
