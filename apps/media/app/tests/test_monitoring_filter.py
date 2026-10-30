"""Shutdown cancellation must not reach error tracking."""

import asyncio

from server.monitoring import drop_cancellation


def test_cancelled_error_via_exc_info_is_dropped() -> None:
    hint = {"exc_info": (asyncio.CancelledError, asyncio.CancelledError(), None)}
    assert drop_cancellation({}, hint) is None


def test_cancelled_error_via_event_payload_is_dropped() -> None:
    event = {"exception": {"values": [{"type": "CancelledError"}]}}
    assert drop_cancellation(event, {}) is None


def test_other_errors_are_kept() -> None:
    event = {"exception": {"values": [{"type": "ValueError"}]}}
    hint = {"exc_info": (ValueError, ValueError("x"), None)}
    assert drop_cancellation(event, hint) is event
