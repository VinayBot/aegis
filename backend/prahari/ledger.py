"""The usage ledger: one SQLite row per request, plus notable system events (breaker trips,
chaos actions).

Writes never touch the request path. Callers put rows on a queue; one background task drains it
in batches (up to 200 rows or every 250 ms) and runs each batch in a worker thread, so a burst of
requests never blocks the event loop on disk I/O.

Rows carry the key's public id, never the key. Every read a tenant can make is filtered by tenant
in SQL and served by an index (`tests/test_units.py` checks the query plans).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, tenant TEXT, key_id TEXT, model TEXT, backend TEXT,
  status TEXT, http_status INTEGER, cache TEXT, coalesced INTEGER, hedged INTEGER,
  failovers INTEGER, tokens_in INTEGER, tokens_out INTEGER, usage_estimated INTEGER,
  latency_ms REAL, ttft_ms REAL, cost_est_usd REAL, saved_est_usd REAL, trace_id TEXT,
  error TEXT, redactions TEXT
);
CREATE INDEX IF NOT EXISTS requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS requests_tenant ON requests(tenant, ts);
CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT, backend TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS system_events_kind ON system_events(kind);
CREATE TABLE IF NOT EXISTS feedback (
  request_id TEXT PRIMARY KEY, ts REAL NOT NULL, tenant TEXT, trace_id TEXT,
  value INTEGER NOT NULL, comment TEXT
);
CREATE INDEX IF NOT EXISTS feedback_tenant ON feedback(tenant, ts);
"""

COLUMNS = (
    "id",
    "ts",
    "tenant",
    "key_id",
    "model",
    "backend",
    "status",
    "http_status",
    "cache",
    "coalesced",
    "hedged",
    "failovers",
    "tokens_in",
    "tokens_out",
    "usage_estimated",
    "latency_ms",
    "ttft_ms",
    "cost_est_usd",
    "saved_est_usd",
    "trace_id",
    "error",
    "redactions",
)


class Ledger:
    def __init__(self, path: Path, batch_size: int = 200, flush_interval: float = 0.25) -> None:
        self.path = path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: asyncio.Queue[tuple[str, tuple[Any, ...]]] = asyncio.Queue(maxsize=20000)
        self._task: asyncio.Task[None] | None = None
        self._db_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()
        self._conn.executescript(SCHEMA)
        self.rows_written = 0
        self._in_hand = 0  # rows the background writer has taken but not yet written
        self.batches = 0
        self.dropped = 0
        self.last_batch_ms = 0.0

    def _migrate(self) -> None:
        """Ledgers written before key ids stored the raw key in `api_key`: rename the column.
        `remap_key_ids` then replaces those values with public ids."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(requests)")}
        if "api_key" in cols and "key_id" not in cols:
            self._conn.execute("ALTER TABLE requests RENAME COLUMN api_key TO key_id")

    def remap_key_ids(self, mapping: dict[str, str]) -> int:
        """Replace stored raw keys with their public ids (idempotent; a no-op once clean)."""
        changed = 0
        with self._db_lock:
            for raw, key_id in mapping.items():
                if raw != key_id:
                    cur = self._conn.execute(
                        "UPDATE requests SET key_id = ? WHERE key_id = ?", (key_id, raw)
                    )
                    changed += cur.rowcount
        return changed

    def explain(self, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
        """SQLite's query plan (EXPLAIN QUERY PLAN), for the index checks in the tests."""
        with self._db_lock:
            return [str(r[3]) for r in self._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]

    # --------------------------------------------------------------- writing

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._drain())

    async def stop(self) -> None:
        if self._task is not None:
            await self.flush()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def record_request(self, row: dict[str, Any]) -> None:
        values = tuple(json.dumps(row.get(c)) if c == "redactions" else row.get(c) for c in COLUMNS)
        self._enqueue("request", values)

    def record_event(self, kind: str, backend: str | None, detail: dict[str, Any]) -> None:
        self._enqueue("event", (time.time(), kind, backend, json.dumps(detail)))

    def _enqueue(self, kind: str, values: tuple[Any, ...]) -> None:
        try:
            self._queue.put_nowait((kind, values))
        except asyncio.QueueFull:
            self.dropped += 1

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def flush(self) -> None:
        """Write everything queued *and* wait for the batch the writer already holds, so a read
        right after a flush sees every row recorded before it."""
        batch: list[tuple[str, tuple[Any, ...]]] = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if batch:
            await asyncio.to_thread(self._write, batch)
        for _ in range(400):  # at most ~2 s: one flush interval plus a write
            if not self._in_hand:
                return
            await asyncio.sleep(0.005)

    async def _drain(self) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            self._in_hand = 1
            deadline = time.monotonic() + self.flush_interval
            while len(batch) < self.batch_size:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout))
                except TimeoutError:
                    break
            try:
                await asyncio.to_thread(self._write, batch)
            except Exception:  # never let a bad row kill the writer
                self.dropped += len(batch)
            finally:
                self._in_hand = 0

    def _write(self, batch: list[tuple[str, tuple[Any, ...]]]) -> None:
        start = time.perf_counter()
        requests = [v for k, v in batch if k == "request"]
        events = [v for k, v in batch if k == "event"]
        with self._db_lock:
            self._conn.execute("BEGIN")
            if requests:
                placeholders = ",".join("?" * len(COLUMNS))
                self._conn.executemany(
                    f"INSERT OR REPLACE INTO requests ({','.join(COLUMNS)}) VALUES ({placeholders})",
                    requests,
                )
            if events:
                self._conn.executemany(
                    "INSERT INTO system_events (ts, kind, backend, detail) VALUES (?,?,?,?)", events
                )
            self._conn.execute("COMMIT")
        self.rows_written += len(batch)
        self.batches += 1
        self.last_batch_ms = (time.perf_counter() - start) * 1000

    # --------------------------------------------------------------- reading

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._db_lock:
            cur = self._conn.execute(sql, params)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    async def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._query, sql, params)

    async def totals(self) -> dict[str, Any]:
        rows = await self.query(
            """SELECT COUNT(*) AS requests,
                      SUM(status = 'ok') AS served,
                      SUM(cache = 'exact') AS cache_exact,
                      SUM(cache = 'semantic') AS cache_semantic,
                      SUM(coalesced) AS coalesced,
                      SUM(hedged) AS hedged,
                      SUM(failovers > 0) AS failed_over,
                      SUM(http_status = 429) AS rate_limited,
                      COALESCE(SUM(tokens_in), 0) AS tokens_in,
                      COALESCE(SUM(tokens_out), 0) AS tokens_out,
                      COALESCE(SUM(cost_est_usd), 0) AS cost_est_usd,
                      COALESCE(SUM(saved_est_usd), 0) AS saved_est_usd
               FROM requests"""
        )
        trips = await self.query(
            "SELECT COUNT(*) AS n FROM system_events WHERE kind = 'breaker_open'"
        )
        out = {k: (v or 0) for k, v in rows[0].items()}
        out["breaker_trips"] = trips[0]["n"]
        return out

    async def tenants(self) -> list[dict[str, Any]]:
        month_start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
        rows = await self.query(
            """SELECT tenant, COUNT(*) AS requests,
                      COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens,
                      SUM(http_status = 429) AS rate_limited,
                      COALESCE(SUM(cost_est_usd), 0) AS cost_est_usd,
                      COALESCE(SUM(saved_est_usd), 0) AS saved_est_usd
               FROM requests WHERE ts >= ? GROUP BY tenant ORDER BY tokens DESC""",
            (month_start,),
        )
        votes = {
            r["tenant"]: r
            for r in await self.query(
                """SELECT tenant, SUM(value = 1) AS feedback_up, SUM(value = 0) AS feedback_down
                   FROM feedback WHERE ts >= ? GROUP BY tenant""",
                (month_start,),
            )
        }
        for r in rows:
            v = votes.get(r["tenant"], {})
            r["feedback_up"], r["feedback_down"] = (
                v.get("feedback_up") or 0,
                v.get("feedback_down") or 0,
            )
        return rows

    def _record_feedback(
        self, request_id: str, tenant: str, trace_id: str | None, value: int, comment: str | None
    ) -> None:
        with self._db_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback (request_id, ts, tenant, trace_id, value, comment)"
                " VALUES (?,?,?,?,?,?)",
                (request_id, time.time(), tenant, trace_id, value, comment),
            )

    async def record_feedback(
        self, request_id: str, tenant: str, trace_id: str | None, value: int, comment: str | None
    ) -> None:
        """A user's rating of one answer: written at once (rare, and the user is waiting)."""
        await asyncio.to_thread(self._record_feedback, request_id, tenant, trace_id, value, comment)

    RECENT_SQL = "SELECT * FROM requests ORDER BY ts DESC LIMIT ?"
    RECENT_TENANT_SQL = "SELECT * FROM requests WHERE tenant = ? ORDER BY ts DESC LIMIT ?"
    GET_SQL = "SELECT * FROM requests WHERE id = ?"
    GET_TENANT_SQL = "SELECT * FROM requests WHERE id = ? AND tenant = ?"

    async def recent(self, limit: int = 50, tenant: str | None = None) -> list[dict[str, Any]]:
        if tenant is None:
            return await self.query(self.RECENT_SQL, (limit,))
        return await self.query(self.RECENT_TENANT_SQL, (tenant, limit))

    async def get(self, request_id: str, tenant: str | None = None) -> dict[str, Any] | None:
        """With a tenant, a row of another tenant is simply not found (no IDOR by id)."""
        if tenant is None:
            rows = await self.query(self.GET_SQL, (request_id,))
        else:
            rows = await self.query(self.GET_TENANT_SQL, (request_id, tenant))
        return rows[0] if rows else None

    def ping(self) -> float:
        start = time.perf_counter()
        with self._db_lock:
            self._conn.execute("SELECT 1").fetchone()
        return (time.perf_counter() - start) * 1000

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()
