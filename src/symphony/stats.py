"""Append-only run-stats event store + on-read aggregation.

The orchestrator appends one JSON line per turn, per phase transition, and
per worker exit to `<workflow_dir>/.symphony/stats.jsonl`. The stats API and
the TUI stats screen aggregate on read. Writes are failure-tolerant: a
broken disk must never take the orchestrator down.

Event shapes (all carry `ts` ISO-8601 UTC and `type`):
    turn        issue, state, agent, in, cache, out, total
    transition  issue, from, to
    run_end     issue, state, agent, outcome, turns, seconds
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logging import get_logger

log = get_logger()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatsStore:
    """Thread-safe JSONL appender + aggregator for one board."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
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
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as exc:
            if not self._write_failed_logged:
                self._write_failed_logged = True
                log.warning("stats_write_failed", path=str(self.path), error=str(exc))

    # ------------------------------------------------------------------
    # aggregation
    # ------------------------------------------------------------------

    def read_events(self, days: int | None = None) -> list[dict[str, Any]]:
        """Return events, newest last; optionally only the last `days` days."""
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
        events = self.read_events(days)
        done_lower = {s.lower() for s in (done_states or {"done"})}

        totals = {"in": 0, "cache": 0, "out": 0, "total": 0, "turns": 0, "runs": 0, "done": 0}
        by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "turns": 0, "done": 0})
        by_state: dict[str, dict[str, float]] = defaultdict(
            lambda: {"total": 0, "turns": 0, "runs": 0, "seconds": 0.0}
        )
        by_agent: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "turns": 0, "runs": 0})
        first_seen: dict[str, datetime] = {}
        done_at: dict[str, datetime] = {}
        entered_state: dict[str, tuple[str, datetime]] = {}
        dwell: dict[str, list[float]] = defaultdict(list)

        for event in events:
            etype = event.get("type")
            ts = _parse_ts(event.get("ts"))
            day = ts.strftime("%Y-%m-%d") if ts else "unknown"
            issue = str(event.get("issue") or "")
            if etype == "turn":
                total = _as_int(event.get("total"))
                totals["in"] += _as_int(event.get("in"))
                totals["cache"] += _as_int(event.get("cache"))
                totals["out"] += _as_int(event.get("out"))
                totals["total"] += total
                totals["turns"] += 1
                by_day[day]["total"] += total
                by_day[day]["turns"] += 1
                state = str(event.get("state") or "?")
                agent = str(event.get("agent") or "?")
                by_state[state]["total"] += total
                by_state[state]["turns"] += 1
                by_agent[agent]["total"] += total
                by_agent[agent]["turns"] += 1
            elif etype == "run_end":
                totals["runs"] += 1
                state = str(event.get("state") or "?")
                agent = str(event.get("agent") or "?")
                by_state[state]["runs"] += 1
                by_state[state]["seconds"] += float(event.get("seconds") or 0.0)
                by_agent[agent]["runs"] += 1
            elif etype == "transition" and ts is not None and issue:
                if issue not in first_seen:
                    first_seen[issue] = ts
                prev = entered_state.pop(issue, None)
                if prev is not None:
                    prev_state, entered_at = prev
                    dwell[prev_state].append((ts - entered_at).total_seconds())
                to_state = str(event.get("to") or "")
                entered_state[issue] = (to_state, ts)
                if to_state.lower() in done_lower:
                    totals["done"] += 1
                    by_day[day]["done"] += 1
                    done_at[issue] = ts

        cycle_seconds = [
            (done_at[i] - first_seen[i]).total_seconds()
            for i in done_at
            if i in first_seen and done_at[i] >= first_seen[i]
        ]
        return {
            "days": days,
            "totals": totals,
            "by_day": [
                {"date": d, **v} for d, v in sorted(by_day.items()) if d != "unknown"
            ],
            "by_state": [
                {
                    "state": s,
                    "total_tokens": int(v["total"]),
                    "turns": int(v["turns"]),
                    "runs": int(v["runs"]),
                    "avg_run_seconds": round(v["seconds"] / v["runs"], 1) if v["runs"] else 0,
                    "avg_dwell_seconds": round(sum(dwell[s]) / len(dwell[s]), 1) if dwell.get(s) else 0,
                }
                for s, v in sorted(by_state.items())
            ],
            "by_agent": [
                {"agent": a, "total_tokens": v["total"], "turns": v["turns"], "runs": v["runs"]}
                for a, v in sorted(by_agent.items())
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
    """Per-path singleton so all in-process writers share one append lock."""
    key = str(path.resolve()) if path.parent.exists() else str(path)
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
