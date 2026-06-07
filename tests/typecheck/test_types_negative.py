"""Negative-test cases: these SHOULD fail type checking in both mypy and pyright.

Run with NO type: ignore markers to confirm errors fire.
"""

from __future__ import annotations

from dataclasses import dataclass

from freshcache import MemoryCache, TypedCache, TypedView, cached


@dataclass(frozen=True)
class User:
    name: str


backend = MemoryCache(maxsize=128)
users: TypedCache[str, User] = TypedView(backend, namespace="users")


# Should fail: passing int as V=User
users.set("alice", 42)


# Should fail: function returns float, cache expects User
@cached(cache=users, ttl=3600)
def get_score(user_id: str) -> float:
    return 0.0
