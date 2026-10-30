"""Error-tracking filter: shutdown cancellation is not a fault."""

import asyncio
from typing import Any


def _is_cancellation(event: dict[str, Any], hint: dict[str, Any]) -> bool:
    exc_info = hint.get("exc_info")
    if exc_info and isinstance(exc_info[1], asyncio.CancelledError):
        return True
    values = (event.get("exception") or {}).get("values") or []
    return any(value.get("type") == "CancelledError" for value in values)


def drop_cancellation(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Sentry event processor: drop `asyncio.CancelledError` events.

    A restart cancels the lifespan receive and any in-flight scheduler job;
    the next poll tick picks the work up again, so reporting it is noise.
    """
    return None if _is_cancellation(event, hint) else event


def install() -> None:
    """Attach the filter when sentry-sdk is present and initialised."""
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.get_global_scope().add_event_processor(drop_cancellation)
