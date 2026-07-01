"""StatsStore aggregation + skill discovery/injection."""

from __future__ import annotations

import json
from pathlib import Path

from symphony.prompt import build_first_turn_prompt
from symphony.issue import Issue
from symphony.skills import list_skills, normalize_skill_names, render_skill_block
from symphony.stats import StatsStore, stats_store_for


# ---------------------------------------------------------------------------
# StatsStore
# ---------------------------------------------------------------------------


def test_record_and_aggregate_roundtrip(tmp_path: Path) -> None:
    store = StatsStore(tmp_path / "stats.jsonl")
    store.record_transition(issue="T-1", from_state="", to_state="todo")
    store.record_turn(
        issue="T-1",
        state="doing",
        agent="claude",
        input_tokens=100,
        cache_tokens=10,
        output_tokens=50,
        total_tokens=160,
    )
    store.record_transition(issue="T-1", from_state="doing", to_state="done")
    store.record_run_end(
        issue="T-1", state="done", agent="claude", outcome="normal", turns=3, seconds=12.5
    )
    agg = store.aggregate(days=7, done_states={"done"})
    assert agg["totals"]["total"] == 160
    assert agg["totals"]["turns"] == 1
    assert agg["totals"]["runs"] == 1
    assert agg["totals"]["done"] == 1
    assert agg["cycle"]["done_tickets"] == 1
    by_agent = {row["agent"]: row for row in agg["by_agent"]}
    assert by_agent["claude"]["total_tokens"] == 160
    assert len(agg["by_day"]) == 1


def test_aggregate_skips_corrupt_lines_and_old_events(tmp_path: Path) -> None:
    path = tmp_path / "stats.jsonl"
    path.write_text(
        "\n".join(
            [
                "not json at all",
                json.dumps({"ts": "2001-01-01T00:00:00Z", "type": "turn", "total": 999}),
                json.dumps(
                    {
                        "ts": "2001-01-01T00:00:00Z",
                        "type": "transition",
                        "issue": "OLD-1",
                        "from": "",
                        "to": "done",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agg = StatsStore(path).aggregate(days=7, done_states={"done"})
    assert agg["totals"]["total"] == 0
    assert agg["totals"]["done"] == 0


def test_missing_file_aggregates_to_zero(tmp_path: Path) -> None:
    agg = StatsStore(tmp_path / "absent.jsonl").aggregate(days=7)
    assert agg["totals"] == {
        "in": 0, "cache": 0, "out": 0, "total": 0, "turns": 0, "runs": 0, "done": 0,
    }
    assert agg["by_day"] == []


def test_store_factory_returns_same_instance_per_path(tmp_path: Path) -> None:
    a = stats_store_for(tmp_path / "s.jsonl")
    b = stats_store_for(tmp_path / "s.jsonl")
    assert a is b


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------


def _make_skill(root: Path, name: str, description: str, body: str) -> None:
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_list_skills_reads_frontmatter(tmp_path: Path) -> None:
    _make_skill(tmp_path, "tdd", "test first", "Write tests before code.")
    _make_skill(tmp_path, "docs", "doc style", "Keep docs terse.")
    skills = list_skills(tmp_path)
    assert [s.name for s in skills] == ["docs", "tdd"]
    assert skills[1].description == "test first"


def test_list_skills_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_skills(tmp_path) == []


def test_normalize_skill_names_filters_and_dedupes() -> None:
    assert normalize_skill_names(["TDD", "tdd", "  ", "bad name!", 3, "docs"]) == (
        "tdd",
        "docs",
    )
    assert normalize_skill_names("not-a-list") == ()


def test_render_skill_block_includes_body_and_flags_missing(tmp_path: Path) -> None:
    _make_skill(tmp_path, "tdd", "test first", "Write tests before code.")
    block = render_skill_block(tmp_path, ("tdd", "ghost"))
    assert "## Attached skills" in block
    assert "Write tests before code." in block
    assert "ghost" in block and "not found" in block
    assert render_skill_block(tmp_path, ()) == ""


def test_orchestrator_turn_recorder_writes_deltas(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from symphony.orchestrator import Orchestrator
    from symphony.orchestrator.entries import RunningEntry
    from symphony.workflow import WorkflowState

    orch = Orchestrator(WorkflowState(tmp_path / "WORKFLOW.md"))
    orch._stats = StatsStore(tmp_path / "stats.jsonl")
    issue = Issue(
        id="T-1", identifier="T-1", title="t", description="", priority=None,
        state="Doing", agent_kind="claude",
    )
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path,
        agent_kind="claude",
    )
    entry.codex_input_tokens = 100
    entry.codex_output_tokens = 40
    entry.codex_total_tokens = 140
    orch._record_stats_turn(entry)
    entry.codex_input_tokens = 150
    entry.codex_output_tokens = 60
    entry.codex_total_tokens = 210
    orch._record_stats_turn(entry)
    orch._record_stats_transition("T-1", "Doing", "Done")

    events = orch._stats.read_events()
    turns = [e for e in events if e["type"] == "turn"]
    assert [t["total"] for t in turns] == [140, 70]
    assert turns[0]["state"] == "doing"
    assert turns[0]["agent"] == "claude"
    transitions = [e for e in events if e["type"] == "transition"]
    assert transitions == [
        {"ts": transitions[0]["ts"], "type": "transition", "issue": "T-1",
         "from": "doing", "to": "done"}
    ]


def test_skill_block_survives_strict_template_rendering(tmp_path: Path) -> None:
    # Skill bodies may contain literal {{ }} — they must bypass the strict
    # renderer via extra_context, not crash it.
    _make_skill(tmp_path, "tricky", "braces", "Use {{ curly braces }} literally.")
    issue = Issue(
        id="T-1",
        identifier="T-1",
        title="t",
        description="",
        priority=None,
        state="Todo",
        skills=("tricky",),
    )
    prompt, _ = build_first_turn_prompt(
        prompt_template="Work {{ issue.identifier }}.",
        issue=issue,
        attempt=None,
        language="en",
        max_turns=5,
        extra_context=render_skill_block(tmp_path, issue.skills),
    )
    assert "Work T-1." in prompt
    assert "Use {{ curly braces }} literally." in prompt
