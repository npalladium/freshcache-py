"""Shared bench fixtures.

Bench files are pytest tests in name only (so pytest-benchmark can drive them).
They live outside the `tests` testpath so a default `pytest` run skips them;
invoke with `uv run pytest bench/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest


@pytest.fixture
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Single loop reused across benchmark iterations.

    asyncio.run() would create+destroy a loop per call and dominate the
    measurement; we want to time the coroutine, not loop setup.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()
