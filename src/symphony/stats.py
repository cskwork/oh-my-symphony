"""Append-only run-stats event store + on-read aggregation.

The orchestrator appends one JSON line per turn, per phase transition, and
per worker exit to `<workflow_dir>/.symphony/stats.jsonl`. The stats API and
the TUI stats screen aggregate on read.

Writes never block the caller: `record_*` enqueue onto a single-worker
executor thread (FIFO, so event order is preserved), which makes the hooks
safe to call from the orchestrator's event loop. `read_events` flushes the
queue first so a read always sees its own process's writes. Disk failures
are logged once and never raise.

Event shapes (all carry `ts` ISO-8601 UTC and `type`):
    turn        issue, state, agent, in, cache, out, total
    transition  issue, from, to
    run_end     issue, state, agent, outcome, turns, seconds
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logging import get_logger

log = get_logger()

_FLUSH_TIMEOUT_S = 2.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatsStore:
    """Non-blocking JSONL appender + aggregator for one board."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="symphony-stats"
        )
        self._write_failed_logged = False

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    def record_turn(
        self,
        *,
        issue: str,
        state: str,
        agent: str,
        input_tokens: int,
        cache_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> None:
        self._append(
            {
                "type": "turn",
                "issue": issue,
                "state": state,
                "agent": agent,
                "in": int(input_tokens),
                "cache": int(cache_tokens),
                "out": int(output_tokens),
                "total": int(total_tokens),
            }
        )

    def record_transition(self, *, issue: str, from_state: str, to_state: str) -> None:
        self._append(
            {"type": "transition", "issue": issue, "from": from_state, "to": to_state}
        )

    def record_run_end(
        self,
        *,
        issue: str,
        state: str,
        agent: str,
        outcome: str,
        turns: int,
        seconds: float,
    ) -> None:
        self._append(
            {
                "type": "run_end",
                "issue": issue,
                "state": state,
                "agent": agent,
                "outcome": outcome,
                "turns": int(turns),
                "seconds": round(float(seconds), 1),
            }
        )

    def _append(self, event: dict[str, Any]) -> None:
        line = json.dumps({"ts": _utc_now_iso(), **event}, ensure_ascii=False)
        try:
            self._writer.submit(self._write_line, line)
        except RuntimeError:
            # Executor shut down (interpreter exit) — drop the event.
            pass

    def _write_line(self, line: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            if not self._write_failed_logged:
                self._write_failed_logged = True
                log.warning("stats_write_failed", path=str(self.path), error=str(exc))

    def flush(self, timeout: float = _FLUSH_TIMEOUT_S) -> None:
        """Block until previously enqueued events are on disk (FIFO worker)."""
        try:
            self._writer.submit(lambda: None).result(timeout=timeout)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # aggregation
    # ------------------------------------------------------------------

    def read_events(self, days: int | None = None) -> list[dict[str, Any]]:
        """Return events, newest last; optionally only the last `days` days."""
        self.flush()
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        cutoff: datetime | None = None
        if days is not None and days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if cutoff is not None:
                ts = _parse_ts(event.get("ts"))
                if ts is None or ts < cutoff:
                    continue
            out.append(event)
        return out

    def aggregate(self, days: int | None = 30, done_states: set[str] | None = None) -> dict[str, Any]:
        """Aggregate events for the stats page / TUI stats screen."""
        acc = _Accumulator({s.lower() for s in (done_states or {"done"})})
        for event in self.read_events(days):
            acc.fold(event)
        return acc.result(days)


class _Accumulator:
    """Single-pass fold of stats events into the aggregate payload."""

    def __init__(self, done_states: set[str]) -> None:
        self.done_states = done_states
        self.totals = {
            "in": 0, "cache": 0, "out": 0, "total": 0, "turns": 0, "runs": 0, "done": 0,
        }
        self.by_day: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "turns": 0, "done": 0}
        )
        self.by_state: dict[str, dict[str, float]] = defaultdict(
            lambda: {"total": 0, "turns": 0, "runs": 0, "seconds": 0.0}
        )
        self.by_agent: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "turns": 0, "runs": 0}
        )
        self.first_seen: dict[str, datetime] = {}
        self.done_at: dict[str, datetime] = {}
        self.entered_state: dict[str, tuple[str, datetime]] = {}
        self.dwell: dict[str, list[float]] = defaultdict(list)

    def fold(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        ts = _parse_ts(event.get("ts"))
        day = ts.strftime("%Y-%m-%d") if ts else "unknown"
        if etype == "turn":
            self._fold_turn(event, day)
        elif etype == "run_end":
            self._fold_run_end(event)
        elif etype == "transition" and ts is not None:
            self._fold_transition(event, ts, day)

    def _fold_turn(self, event: dict[str, Any], day: str) -> None:
        total = _as_int(event.get("total"))
        self.totals["in"] += _as_int(event.get("in"))
        self.totals["cache"] += _as_int(event.get("cache"))
        self.totals["out"] += _as_int(event.get("out"))
        self.totals["total"] += total
        self.totals["turns"] += 1
        self.by_day[day]["total"] += total
        self.by_day[day]["turns"] += 1
        state = str(event.get("state") or "?")
        agent = str(event.get("agent") or "?")
        self.by_state[state]["total"] += total
        self.by_state[state]["turns"] += 1
        self.by_agent[agent]["total"] += total
        self.by_agent[agent]["turns"] += 1

    def _fold_run_end(self, event: dict[str, Any]) -> None:
        self.totals["runs"] += 1
        state = str(event.get("state") or "?")
        agent = str(event.get("agent") or "?")
        self.by_state[state]["runs"] += 1
        self.by_state[state]["seconds"] += float(event.get("seconds") or 0.0)
        self.by_agent[agent]["runs"] += 1

    def _fold_transition(self, event: dict[str, Any], ts: datetime, day: str) -> None:
        issue = str(event.get("issue") or "")
        if not issue:
            return
        if issue not in self.first_seen:
            self.first_seen[issue] = ts
        prev = self.entered_state.pop(issue, None)
        if prev is not None:
            prev_state, entered_at = prev
            self.dwell[prev_state].append((ts - entered_at).total_seconds())
        to_state = str(event.get("to") or "")
        self.entered_state[issue] = (to_state, ts)
        if to_state.lower() in self.done_states:
            self.totals["done"] += 1
            self.by_day[day]["done"] += 1
            self.done_at[issue] = ts

    def result(self, days: int | None) -> dict[str, Any]:
        cycle_seconds = [
            (self.done_at[i] - self.first_seen[i]).total_seconds()
            for i in self.done_at
            if i in self.first_seen and self.done_at[i] >= self.first_seen[i]
        ]
        return {
            "days": days,
            "totals": self.totals,
            "by_day": [
                {"date": d, **v}
                for d, v in sorted(self.by_day.items())
                if d != "unknown"
            ],
            "by_state": [
                {
                    "state": s,
                    "total_tokens": int(v["total"]),
                    "turns": int(v["turns"]),
                    "runs": int(v["runs"]),
                    "avg_run_seconds": round(v["seconds"] / v["runs"], 1) if v["runs"] else 0,
                    "avg_dwell_seconds": round(sum(self.dwell[s]) / len(self.dwell[s]), 1)
                    if self.dwell.get(s)
                    else 0,
                }
                for s, v in sorted(self.by_state.items())
            ],
            "by_agent": [
                {"agent": a, "total_tokens": v["total"], "turns": v["turns"], "runs": v["runs"]}
                for a, v in sorted(self.by_agent.items())
            ],
            "cycle": {
                "done_tickets": len(cycle_seconds),
                "avg_seconds": round(sum(cycle_seconds) / len(cycle_seconds), 1)
                if cycle_seconds
                else 0,
            },
        }


_STORES: dict[str, StatsStore] = {}
_STORES_LOCK = threading.Lock()


def stats_store_for(path: Path) -> StatsStore:
    """Per-path singleton so all in-process writers share one FIFO writer.

    Keyed on `normcase(abspath(...))` — stable whether or not the parent
    directory exists yet, and case-fold-stable on Windows.
    """
    key = os.path.normcase(os.path.abspath(str(path)))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = StatsStore(path)
            _STORES[key] = store
        return store


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
