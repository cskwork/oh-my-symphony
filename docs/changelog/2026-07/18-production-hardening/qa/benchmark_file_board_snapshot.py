#!/usr/bin/env python3
"""Benchmark the exact TUI file-board fetch seam before and after hardening."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import symphony.trackers.file as file_tracker_module
from symphony.issue import normalize_state
from symphony.trackers.file import serialize_ticket
from symphony.workflow import TrackerConfig


ACTIVE_STATES = ("Todo", "In Progress")
TERMINAL_STATES = ("Done", "Cancelled")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES


def _write_board(root: Path, card_count: int, body_bytes: int) -> None:
    body = "x" * body_bytes
    for index in range(card_count):
        identifier = f"BENCH-{index:05d}"
        front = {
            "id": identifier,
            "identifier": identifier,
            "title": f"Benchmark card {index}",
            "state": ALL_STATES[index % len(ALL_STATES)],
            "priority": index % 5,
        }
        (root / f"{identifier}.md").write_text(
            serialize_ticket(front, body), encoding="utf-8"
        )


def _config(root: Path) -> Any:
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=ACTIVE_STATES,
        terminal_states=TERMINAL_STATES,
        board_root=root,
    )
    return SimpleNamespace(tracker=tracker)


def _fetch_seam() -> tuple[str, Callable[[Any], tuple[list[Any], list[Any]]]]:
    from symphony.tui import helpers

    snapshot = getattr(helpers, "_fetch_tracker_snapshot", None)
    if snapshot is not None:
        return "single_snapshot", snapshot

    def legacy(cfg: Any) -> tuple[list[Any], list[Any]]:
        return helpers._fetch_candidates(cfg), helpers._fetch_terminals(cfg)

    return "legacy_two_scan", legacy


def _expected_ids(card_count: int) -> tuple[list[str], list[str]]:
    candidates: list[str] = []
    terminals: list[str] = []
    terminal_keys = {normalize_state(state) for state in TERMINAL_STATES}
    for index in range(card_count):
        identifier = f"BENCH-{index:05d}"
        state_key = normalize_state(ALL_STATES[index % len(ALL_STATES)])
        if state_key in terminal_keys:
            terminals.append(identifier)
        else:
            candidates.append(identifier)
    return candidates, terminals


def _measure(
    fetch: Callable[[Any], tuple[list[Any], list[Any]]], cfg: Any
) -> tuple[float, int, list[str], list[str]]:
    parse_count = 0
    original = file_tracker_module.issue_from_file

    def counted(path: Path):
        nonlocal parse_count
        parse_count += 1
        return original(path)

    file_tracker_module.issue_from_file = counted
    try:
        started = time.perf_counter()
        candidates, terminals = fetch(cfg)
        elapsed_ms = (time.perf_counter() - started) * 1000
    finally:
        file_tracker_module.issue_from_file = original
    return (
        elapsed_ms,
        parse_count,
        [issue.identifier for issue in candidates],
        [issue.identifier for issue in terminals],
    )


def _order_digest(identifiers: list[str]) -> str:
    return hashlib.sha256("\0".join(identifiers).encode()).hexdigest()


def _run_size(
    card_count: int, *, samples: int, warmups: int, body_bytes: int
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="symphony-board-benchmark-") as raw:
        root = Path(raw)
        _write_board(root, card_count, body_bytes)
        cfg = _config(root)
        implementation, fetch = _fetch_seam()
        expected_candidates, expected_terminals = _expected_ids(card_count)
        for _ in range(warmups):
            _measure(fetch, cfg)
        timings: list[float] = []
        parse_counts: list[int] = []
        for _ in range(samples):
            elapsed, parses, candidates, terminals = _measure(fetch, cfg)
            if candidates != expected_candidates or terminals != expected_terminals:
                raise AssertionError("TUI fetch membership changed during benchmark")
            timings.append(elapsed)
            parse_counts.append(parses)
        expected_parses = card_count * (2 if implementation == "legacy_two_scan" else 1)
        if parse_counts != [expected_parses] * samples:
            raise AssertionError(
                f"expected {expected_parses} parses per sample, got {parse_counts}"
            )
        return {
            "cards": card_count,
            "implementation": implementation,
            "median_ms": round(statistics.median(timings), 3),
            "samples_ms": [round(value, 3) for value in timings],
            "parse_counts": parse_counts,
            "candidate_count": len(expected_candidates),
            "terminal_count": len(expected_terminals),
            "candidate_order_sha256": _order_digest(expected_candidates),
            "terminal_order_sha256": _order_digest(expected_terminals),
        }


def _enforce_baseline(
    results: list[dict[str, Any]], baseline_path: Path, max_ratio: float
) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    by_size = {row["cards"]: row for row in baseline["results"]}
    for result in results:
        before = by_size[result["cards"]]
        for key in ("candidate_order_sha256", "terminal_order_sha256"):
            if result[key] != before[key]:
                raise AssertionError(f"ordered membership changed for {key}")
        ratio = result["median_ms"] / before["median_ms"]
        result["baseline_median_ms"] = before["median_ms"]
        result["median_ratio"] = round(ratio, 4)
        if ratio > max_ratio:
            raise AssertionError(
                f"{result['cards']} cards: median ratio {ratio:.4f} exceeds {max_ratio}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", type=int, nargs="+", default=[1000, 5000])
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--body-bytes", type=int, default=1024)
    parser.add_argument("--baseline-json", type=Path)
    parser.add_argument("--max-ratio", type=float, default=0.65)
    parser.add_argument(
        "--expect-implementation",
        choices=("legacy_two_scan", "single_snapshot"),
    )
    args = parser.parse_args()
    if args.samples < 3 or args.warmups < 0 or min(args.cards) < 1:
        parser.error("cards must be positive, samples >= 3, and warmups >= 0")
    results = [
        _run_size(
            card_count,
            samples=args.samples,
            warmups=args.warmups,
            body_bytes=args.body_bytes,
        )
        for card_count in args.cards
    ]
    implementations = {result["implementation"] for result in results}
    if args.expect_implementation and implementations != {args.expect_implementation}:
        raise AssertionError(
            f"expected {args.expect_implementation}, got {sorted(implementations)}"
        )
    if args.baseline_json is not None:
        _enforce_baseline(results, args.baseline_json, args.max_ratio)
    print(
        json.dumps(
            {
                "samples": args.samples,
                "warmups": args.warmups,
                "body_bytes": args.body_bytes,
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
