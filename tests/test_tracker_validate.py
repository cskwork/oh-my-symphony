"""Board dependency-DAG validation shared by CLI + web API (§trackers.validate)."""

from __future__ import annotations

import pytest

from symphony.errors import BoardDependencyError
from symphony.issue import BlockerRef, Issue
from symphony.trackers.validate import (
    dangling_blockers,
    find_cycle,
    topological_order,
    validate_ticket_dependencies,
)


def _issue(identifier: str, *blockers: str, state: str = "Todo") -> Issue:
    return Issue(
        id=identifier,
        identifier=identifier,
        title=identifier.lower(),
        description=None,
        priority=None,
        state=state,
        blocked_by=tuple(
            BlockerRef(id=b, identifier=b, state=None) for b in blockers
        ),
    )


def test_find_cycle_none_on_acyclic_graph():
    edges = {"A": ("B",), "B": ("C",), "C": ()}
    assert find_cycle(edges) is None


def test_find_cycle_returns_closed_path():
    edges = {"A": ("B",), "B": ("A",)}
    cycle = find_cycle(edges)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"A", "B"}


def test_find_cycle_ignores_dangling_targets():
    assert find_cycle({"A": ("GHOST",)}) is None


def test_topological_order_puts_blockers_first():
    edges = {"C": ("B",), "B": ("A",), "A": (), "D": ()}
    assert topological_order(edges) == ["A", "D", "B", "C"]


def test_dangling_blockers_reports_missing_targets():
    issues = [_issue("A"), _issue("B", "A", "GHOST")]
    assert dangling_blockers(issues) == {"B": ["GHOST"]}


def test_validate_rejects_duplicate_new_identifier():
    with pytest.raises(BoardDependencyError, match="already exists"):
        validate_ticket_dependencies(
            [_issue("A")], identifier="A", blocked_by=[], new_ticket=True
        )


def test_validate_rejects_unknown_blocker():
    with pytest.raises(BoardDependencyError, match="unknown blocked_by.*GHOST"):
        validate_ticket_dependencies(
            [_issue("A")], identifier="B", blocked_by=["GHOST"], new_ticket=True
        )


def test_validate_rejects_cycle_with_path_in_message():
    # A already (danglingly) points at B; creating B <- A closes the loop.
    issues = [_issue("A", "B")]
    with pytest.raises(BoardDependencyError, match="B -> A -> B"):
        validate_ticket_dependencies(
            issues, identifier="B", blocked_by=["A"], new_ticket=True
        )


def test_validate_rejects_self_blocking_update_as_cycle():
    with pytest.raises(BoardDependencyError, match="cycle"):
        validate_ticket_dependencies(
            [_issue("A")], identifier="A", blocked_by=["A"], new_ticket=False
        )


def test_validate_ignores_pre_existing_unrelated_cycle():
    # A hand-edited board may already contain X <-> Y; unrelated writes pass.
    issues = [_issue("X", "Y"), _issue("Y", "X"), _issue("A")]
    validate_ticket_dependencies(
        issues, identifier="B", blocked_by=["A"], new_ticket=True
    )


def test_validate_generated_identifier_only_checks_blockers_exist():
    validate_ticket_dependencies(
        [_issue("A")], identifier=None, blocked_by=["A"], new_ticket=True
    )
    with pytest.raises(BoardDependencyError, match="unknown blocked_by"):
        validate_ticket_dependencies(
            [_issue("A")], identifier=None, blocked_by=["NOPE"], new_ticket=True
        )


@pytest.mark.parametrize("cyclic", [False, True])
def test_find_cycle_handles_deep_dependency_chain(cyclic):
    edges = {f"T-{i:04}": (f"T-{i + 1:04}",) for i in range(2000)}
    edges["T-2000"] = ("T-0000",) if cyclic else ()
    cycle = find_cycle(edges)
    if cyclic:
        assert cycle == [*edges, "T-0000"]
    else:
        assert cycle is None


@pytest.mark.parametrize("cyclic", [False, True])
def test_validate_dependencies_handles_deep_chain(cyclic):
    issues = [_issue(f"T-{i:04}", f"T-{i + 1:04}")
              for i in range(2000)]
    issues.append(_issue("T-2000"))
    if cyclic:
        with pytest.raises(BoardDependencyError, match="dependency cycle"):
            validate_ticket_dependencies(issues, identifier="T-2000",
                                         blocked_by=["T-0000"], new_ticket=False)
    else:
        validate_ticket_dependencies(issues, identifier="NEW",
                                     blocked_by=["T-0000"], new_ticket=True)


def test_topological_order_preserves_waves_duplicates_and_cyclic_leftovers():
    edges = {"A": ("Z", "Z"), "B": (), "C": ("B",), "Z": (),
             "X": ("Y",), "Y": ("X",), "D": ("MISSING",)}
    assert topological_order(edges) == ["B", "D", "Z", "A", "C", "X", "Y"]
