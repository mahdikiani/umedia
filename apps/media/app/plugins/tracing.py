"""Ids that tie one core-side job, its plugin calls and the plugin's own
log lines together.

The core sets `current_run_id` for the duration of a background job (a
sync walk, a hash pass); `PluginClient` sends it with the connection id and
a fresh request id on every call. The plugin SDK puts the three into
`request_context` for the request, and the plugin's log lines carry them,
so a plugin traceback can be matched to the core call and job that caused
it. Shared by both sides of the socket, like `contracts.py`.
"""

import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Umedia-Request-Id"
CONNECTION_ID_HEADER = "X-Umedia-Connection-Id"
RUN_ID_HEADER = "X-Umedia-Run-Id"

#: Core side: the background job (sync/hash run) this task works for.
current_run_id: ContextVar[str | None] = ContextVar("umedia_run_id", default=None)

#: Plugin side: the ids of the request being served.
request_context: ContextVar[dict[str, str]] = ContextVar(
    "umedia_plugin_request",
    default={},  # noqa: B039 -- never mutated, only replaced per request
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
