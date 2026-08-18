from selectors import EpollSelector
from typing import Any

_original_select = EpollSelector.select


def _polling_select(
    self: EpollSelector,
    timeout: float | None = None,
) -> list[tuple[Any, int]]:
    bounded_timeout = 0.01 if timeout is None else min(timeout, 0.01)
    return _original_select(self, bounded_timeout)


EpollSelector.select = _polling_select
