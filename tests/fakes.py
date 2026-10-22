"""Test doubles for the Supabase client.

The memory layer calls two different parts of the client:

* ``client.rpc(function, params).execute()`` for the atomic SQL functions
* ``client.table(name).select(...).eq(...).execute()`` for reads

The fakes below record both, so the tests can assert on the exact contract
without touching the database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class FakeResponse:
    """Minimal stand-in for ``postgrest.base_request_builder.APIResponse``."""

    def __init__(self, data: List[Dict[str, Any]], count: Optional[int] = None):
        self.data = data
        self.count = count


class FakeTableQuery:
    """Records the filter chain of a table query."""

    def __init__(self, client: "FakeClient", table: str):
        self.client = client
        self.table = table
        self.columns: Optional[str] = None
        self.select_kwargs: Dict[str, Any] = {}
        self.filters: List[tuple] = []
        self.order_by: Optional[tuple] = None
        self.limit_value: Optional[int] = None
        self.offset_value: Optional[int] = None

    # -- chainable builder methods -------------------------------------
    def select(self, columns: str = "*", **kwargs):
        self.columns = columns
        self.select_kwargs = kwargs
        return self

    def eq(self, column: str, value: Any):
        self.filters.append(("eq", column, value))
        return self

    def order(self, column: str, desc: bool = False):
        self.order_by = (column, desc)
        return self

    def limit(self, count: int):
        self.limit_value = count
        return self

    def offset(self, count: int):
        self.offset_value = count
        return self

    # -- terminal --------------------------------------------------------
    def execute(self) -> FakeResponse:
        if self.client.error is not None:
            raise self.client.error
        return FakeResponse(self.client.rows, self.client.count)

    # -- helpers for assertions ------------------------------------------
    def filter_values(self, operator: str, column: str) -> List[Any]:
        return [
            value
            for op, col, value in self.filters
            if op == operator and col == column
        ]


class FakeClient:
    """A Supabase client that records calls and returns canned data."""

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        count: Optional[int] = None,
        error: Optional[Exception] = None,
    ):
        self.rows = rows if rows is not None else []
        self.count = count
        self.error = error
        self.rpc_calls: List[Dict[str, Any]] = []
        self.queries: List[FakeTableQuery] = []

    # -- rpc --------------------------------------------------------------
    def rpc(self, function: str, params: Optional[Dict[str, Any]] = None, **kwargs):
        self.rpc_calls.append({"function": function, "params": params or {}})
        client = self

        class _Builder:
            def execute(self) -> FakeResponse:
                if client.error is not None:
                    raise client.error
                return FakeResponse(client.rows, client.count)

        return _Builder()

    # -- table ------------------------------------------------------------
    def table(self, name: str) -> FakeTableQuery:
        query = FakeTableQuery(self, name)
        self.queries.append(query)
        return query

    # -- helpers for assertions ------------------------------------------
    def last_rpc(self) -> Dict[str, Any]:
        assert self.rpc_calls, "no rpc call was made"
        return self.rpc_calls[-1]

    def last_query(self) -> FakeTableQuery:
        assert self.queries, "no table query was made"
        return self.queries[-1]
