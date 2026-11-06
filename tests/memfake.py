"""A tiny in-memory stand-in for the Supabase client.

Supports the query-builder calls the memory layer uses on tables (select,
eq, neq, in_, gt, like, contains, order, limit, insert, update, delete), so
logic that chains several queries can be tested without a database.
"""

from __future__ import annotations

import copy
import itertools
import uuid
from typing import Any, Dict, List, Optional


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _like(value: Any, pattern: str) -> bool:
    if value is None:
        return False
    text = str(value)
    if pattern.startswith("%") and pattern.endswith("%"):
        return pattern.strip("%") in text
    if pattern.endswith("%"):
        return text.startswith(pattern[:-1])
    if pattern.startswith("%"):
        return text.endswith(pattern[1:])
    return text == pattern


class _Query:
    def __init__(self, db: "MemFake", table: str):
        self.db, self.table = db, table
        self.kind = "select"
        self.payload: Any = None
        self.filters: List[tuple] = []
        self._order: Optional[tuple] = None
        self._limit: Optional[int] = None
        self.columns = "*"

    def select(self, columns="*", **_kw):
        self.columns = columns
        return self

    def insert(self, payload):
        self.kind, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.kind, self.payload = "update", payload
        return self

    def delete(self):
        self.kind = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def gt(self, col, val):
        self.filters.append(("gt", col, val))
        return self

    def like(self, col, pattern):
        self.filters.append(("like", col, pattern))
        return self

    def contains(self, col, vals):
        self.filters.append(("contains", col, list(vals)))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row) -> bool:
        for op, col, val in self.filters:
            cur = row.get(col)
            if op == "eq" and cur != val:
                return False
            if op == "neq" and cur == val:
                return False
            if op == "in" and cur not in val:
                return False
            if op == "gt" and not (cur is not None and cur > val):
                return False
            if op == "like" and not _like(cur, val):
                return False
            if op == "contains" and not set(val) <= set(cur or []):
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.kind == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for item in items:
                row = {"id": str(uuid.uuid4()), "created_at": next(self.db.clock), **copy.deepcopy(item)}
                rows.append(row)
                out.append(copy.deepcopy(row))
            return _Resp(out)
        matched = [r for r in rows if self._match(r)]
        if self.kind == "update":
            for r in matched:
                r.update(copy.deepcopy(self.payload))
            return _Resp(copy.deepcopy(matched))
        if self.kind == "delete":
            self.db.tables[self.table] = [r for r in rows if r not in matched]
            return _Resp(copy.deepcopy(matched))
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return _Resp(copy.deepcopy(matched), count=len(matched))


class MemFake:
    def __init__(self, **tables: List[Dict[str, Any]]):
        self.tables: Dict[str, List[Dict[str, Any]]] = {k: copy.deepcopy(v) for k, v in tables.items()}
        self.clock = (f"2026-01-01T00:00:{n:02d}+00:00" for n in itertools.count())

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def add(self, table: str, **row) -> Dict[str, Any]:
        row.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, []).append(row)
        return row
