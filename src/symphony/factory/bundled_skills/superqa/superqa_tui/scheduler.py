"""Simple interval scheduler. Schedules persist in ~/.superqa/schedules/schedules.yaml.

Two consumers:
- TUI arms schedules as asyncio tasks while it is open.
- `superqa schedule daemon` runs them in a blocking loop (nohup/launchd friendly).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .scenario import superqa_home


@dataclass
class ScheduleEntry:
    scenario: str          # scenario name or path
    every_minutes: int
    enabled: bool = True
    last_run: float = 0.0

    def to_dict(self) -> dict:
        return {"scenario": self.scenario, "every_minutes": self.every_minutes,
                "enabled": self.enabled, "last_run": self.last_run}


def _schedules_path() -> Path:
    return superqa_home() / "schedules" / "schedules.yaml"


def load_schedules() -> list[ScheduleEntry]:
    p = _schedules_path()
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    out = []
    for d in data:
        try:
            out.append(ScheduleEntry(
                scenario=str(d["scenario"]),
                every_minutes=max(1, int(d["every_minutes"])),
                enabled=bool(d.get("enabled", True)),
                last_run=float(d.get("last_run", 0.0)),
            ))
        except Exception:
            continue
    return out


def save_schedules(entries: list[ScheduleEntry]) -> None:
    p = _schedules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump([e.to_dict() for e in entries], allow_unicode=True,
                                sort_keys=False), encoding="utf-8")


def add_schedule(scenario: str, every_minutes: int) -> ScheduleEntry:
    entries = [e for e in load_schedules() if e.scenario != scenario]
    entry = ScheduleEntry(scenario=scenario, every_minutes=max(1, every_minutes))
    entries.append(entry)
    save_schedules(entries)
    return entry


def remove_schedule(scenario: str) -> bool:
    entries = load_schedules()
    kept = [e for e in entries if e.scenario != scenario]
    save_schedules(kept)
    return len(kept) < len(entries)


async def run_loop(runner: Callable[[str], Awaitable[None]],
                   stop: asyncio.Event | None = None,
                   tick_seconds: int = 20) -> None:
    """Fire due schedules until stop is set. `runner` runs one scenario by name."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        entries = load_schedules()
        changed = False
        now = time.time()
        for e in entries:
            if not e.enabled:
                continue
            if now - e.last_run >= e.every_minutes * 60:
                e.last_run = now
                changed = True
                try:
                    await runner(e.scenario)
                except Exception:
                    pass  # one failing run must not kill the scheduler
        if changed:
            save_schedules(entries)
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
        except asyncio.TimeoutError:
            continue
