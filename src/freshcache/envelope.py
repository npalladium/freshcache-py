from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class Envelope(Generic[V]):
    value: V
    created_at: float
    soft_ttl: float

    def is_stale(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.created_at >= self.soft_ttl
