"""`symphony board ...` — minimal helper to manage a file-based Kanban.

Subcommands:
    init  <root>                       create the board directory + sample
    ls    [--state STATE]              list tickets (optionally filtered)
    new   <id> <title> [--state ...]   create a validated ticket
    mv    <id> <new-state>             change a ticket's state
    update <id> [--state] [--blocked-by ID] [--add-blocked-by ID] [--request]
                                       update an existing ticket
    show  <id>                         print a ticket's contents
    graph [--request REQ]              print the dependency DAG

`new` validates before writing: unique id, legal state, existing
`--blocked-by` targets, and an acyclic board dependency graph.

These commands operate directly on the configured `tracker.board_root` from
the current `WORKFLOW.md` (or an explicit one passed with --workflow).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..errors import SymphonyError
from ..issue import Issue, normalize_state
from ..trackers.file import FileBoardTracker, parse_ticket_file
from ..trackers.validate import (
    blocker_ids,
    board_edges,
    dangling_blockers,
    find_cycle,
    topological_order,
    validate_identifier,
    validate_ticket_dependencies,
)
from ..workflow import (
    DEFAULT_BOARD_ROOT_NAME,
    SUPPORTED_AGENT_KINDS,
    TrackerConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


def _operator_message(exc: BaseException) -> str:
    """Operator-facing error text without the internal error code.

    `SymphonyError.__str__` prefixes the machine code
    (`board_dependency_error: ...`) which the JSON API needs and a human (or
    an agent reading CLI output) does not. `WorkflowMutationError` already
    strips it on operator surfaces; the CLI now agrees.
    """
    if isinstance(exc, SymphonyError):
        message = exc.message
        if exc.context:
            details = " ".join(f"{k}={v!r}" for k, v in exc.context.items())
            return f"{message} ({details})"
        return message
    return str(exc)


def _resolve_tracker(args: argparse.Namespace) -> TrackerConfig | None:
    """Return TrackerConfig from WORKFLOW.md or None to fall back to --root."""
    workflow_path = resolve_workflow_path(args.workflow)
    if workflow_path.exists():
        try:
            cfg = build_service_config(load_workflow(workflow_path))
        except SymphonyError as exc:
            print(f"warn: workflow load failed ({exc}); using --root", file=sys.stderr)
            return None
        if cfg.tracker.kind != "file":
            print(
                f"warn: tracker.kind is {cfg.tracker.kind!r}, not 'file'; using --root",
                file=sys.stderr,
            )
            return None
        return cfg.tracker
    return None


def _tracker_from_root(root: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Cancelled"),
        board_root=root.resolve(),
    )


def _get_tracker(args: argparse.Namespace) -> TrackerConfig:
    if args.root is not None:
        return _tracker_from_root(Path(args.root))
    cfg = _resolve_tracker(args)
    if cfg is not None:
        return cfg
    # Default: ./board next to the workflow.
    wf_path = resolve_workflow_path(args.workflow)
    return _tracker_from_root(wf_path.parent / DEFAULT_BOARD_ROOT_NAME)


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    tracker = _tracker_from_root(root)
    fbt = FileBoardTracker(tracker)
    sample_id = "DEMO-001"
    if (root / f"{sample_id}.md").exists():
        print(f"board already initialized at {root}")
        return 0
    fbt.create(
        identifier=sample_id,
        title="Symphony demo ticket",
        state="Todo",
        priority=2,
        labels=["demo"],
        description=(
            "This is a sample ticket. Replace the body with your real task. "
            "Symphony will pick it up on the next poll tick if its state is in "
            "tracker.active_states."
        ),
    )
    print(f"initialized board at {root}, sample ticket {sample_id}.md")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    issues = fbt.scan_all()
    if args.state:
        target = args.state.lower()
        issues = [i for i in issues if i.state.lower() == target]
    if not issues:
        print("(no tickets)")
        return 0
    width_id = max(len(i.identifier) for i in issues)
    width_state = max(len(i.state) for i in issues)
    for i in issues:
        prio = "" if i.priority is None else f" P{i.priority}"
        labels = f" [{', '.join(i.labels)}]" if i.labels else ""
        print(
            f"{i.identifier:<{width_id}}  {i.state:<{width_state}}  {i.title}{prio}{labels}"
        )
    return 0


def _read_description(args: argparse.Namespace) -> str:
    if args.description_file is not None:
        if args.description is not None:
            raise SymphonyError("use --description or --description-file, not both")
        if args.description_file == "-":
            return sys.stdin.read()
        return Path(args.description_file).read_text(encoding="utf-8")
    return args.description or ""


def _collect_labels(args: argparse.Namespace) -> list[str] | None:
    labels = [item.strip() for item in (args.labels or "").split(",") if item.strip()]
    labels.extend(args.label or [])
    return labels or None


def cmd_new(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    try:
        identifier = validate_identifier(args.id)
    except SymphonyError as exc:
        print(f"error: {_operator_message(exc)}", file=sys.stderr)
        return 1
    legal_states = {
        s.lower(): s for s in (*tracker.active_states, *tracker.terminal_states)
    }
    state = legal_states.get(args.state.lower())
    if state is None:
        print(
            f"error: unknown state {args.state!r}; "
            f"expected one of {sorted(legal_states.values())}",
            file=sys.stderr,
        )
        return 1
    blocked_by = list(dict.fromkeys(args.blocked_by or []))
    try:
        description = _read_description(args)

        def _validate(issues: list[Issue], resolved: str) -> None:
            validate_ticket_dependencies(
                issues,
                identifier=resolved,
                blocked_by=blocked_by,
                new_ticket=True,
            )

        # Validate + write under one board lock: two concurrent `board new`
        # calls must not each see an acyclic board and jointly create a cycle.
        _, path = fbt.create_validated(
            identifier=identifier,
            validate=_validate,
            title=args.title,
            state=state,
            priority=args.priority,
            labels=_collect_labels(args),
            description=description,
            agent_kind=args.agent_kind,
            blocked_by=blocked_by or None,
            request=args.request,
        )
    except (OSError, SymphonyError) as exc:
        print(f"error: {_operator_message(exc)}", file=sys.stderr)
        return 1
    print(f"created {path}")
    return 0


def cmd_mv(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    try:
        path = fbt.transition(args.id, args.state)
    except SymphonyError as exc:
        print(f"error: {_operator_message(exc)}", file=sys.stderr)
        return 1
    print(f"{args.id} -> {args.state} ({path})")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Partial ticket update — the write path `verify.md` / `deep/*.md` need.

    The prompts tell agents to add bug-ticket ids to an existing ticket's
    `blocked_by` and to push another ticket back to an earlier lane, while
    the chat preamble forbids hand-writing ticket markdown. Without this verb
    the only path was hand-editing frontmatter, which bypasses cycle
    validation entirely.
    """
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    try:
        identifier = validate_identifier(args.id)
    except SymphonyError as exc:
        print(f"error: {_operator_message(exc)}", file=sys.stderr)
        return 1

    state: str | None = None
    if args.state is not None:
        legal_states = {
            s.lower(): s for s in (*tracker.active_states, *tracker.terminal_states)
        }
        state = legal_states.get(args.state.lower())
        if state is None:
            print(
                f"error: unknown state {args.state!r}; "
                f"expected one of {sorted(legal_states.values())}",
                file=sys.stderr,
            )
            return 1

    try:
        issues = fbt.scan_all()
        current = next((i for i in issues if i.identifier == identifier), None)
        if current is None:
            print(f"error: ticket {identifier} not found", file=sys.stderr)
            return 1

        blocked_by: list[str] | None = None
        if args.blocked_by is not None:
            blocked_by = list(dict.fromkeys(args.blocked_by))
        if args.add_blocked_by:
            existing = blocked_by if blocked_by is not None else blocker_ids(current)
            blocked_by = list(dict.fromkeys([*existing, *args.add_blocked_by]))

        if blocked_by is not None:
            validate_ticket_dependencies(
                issues,
                identifier=identifier,
                blocked_by=blocked_by,
                new_ticket=False,
            )

        path = fbt.update_fields(
            identifier,
            state=state,
            blocked_by=blocked_by,
            request=args.request,
        )
    except (OSError, SymphonyError) as exc:
        print(f"error: {_operator_message(exc)}", file=sys.stderr)
        return 1

    changes = []
    if state is not None:
        changes.append(f"state={state}")
    if blocked_by is not None:
        changes.append(f"blocked_by={','.join(blocked_by) or '(cleared)'}")
    if args.request is not None:
        changes.append(f"request={args.request or '(cleared)'}")
    print(f"updated {identifier} ({', '.join(changes) or 'no changes'}) -> {path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    path = fbt.find_path(args.id)
    if path is None:
        print(f"error: ticket {args.id} not found", file=sys.stderr)
        return 1
    front, body = parse_ticket_file(path)
    print(f"# {front.get('identifier', args.id)} ({front.get('state', '?')})")
    print(f"title: {front.get('title', '')}")
    if front.get("priority") is not None:
        print(f"priority: {front['priority']}")
    if front.get("labels"):
        print(f"labels: {', '.join(front['labels'])}")
    if front.get("request"):
        print(f"request: {front['request']}")
    agent = front.get("agent")
    if isinstance(agent, dict) and agent.get("kind"):
        print(f"agent: {agent['kind']}")
    elif front.get("agent_kind"):
        print(f"agent: {front['agent_kind']}")
    if body:
        print()
        print(body)
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    all_issues = fbt.scan_all()
    issues = all_issues
    if args.request:
        issues = [i for i in all_issues if i.request == args.request]
    if not issues:
        print("(no tickets)")
        return 0

    edges = board_edges(issues)
    cycle = find_cycle(edges)
    if cycle is not None:
        print(f"error: dependency cycle: {' -> '.join(cycle)}", file=sys.stderr)
        return 1

    by_id: dict[str, Issue] = {i.identifier: i for i in issues}
    known_on_board = {i.identifier for i in all_issues}
    order = topological_order(edges)
    depth: dict[str, int] = {}
    for identifier in order:
        parents = [b for b in edges[identifier] if b in depth]
        depth[identifier] = 1 + max((depth[b] for b in parents), default=-1)
        issue = by_id[identifier]
        line = f"{'  ' * depth[identifier]}{identifier} {issue.state} {issue.title}"
        blockers = blocker_ids(issue)
        if blockers:
            line += f" <- {', '.join(blockers)}"
        print(line)

    for identifier, missing in sorted(dangling_blockers(all_issues).items()):
        if identifier not in by_id:
            continue
        for target in missing:
            print(f"WARN {identifier}: blocked_by {target} not on board")
    for issue in issues:
        for blocker in issue.blocked_by:
            target = blocker.identifier or blocker.id or ""
            if (
                target in known_on_board
                and normalize_state(blocker.state) == "cancelled"
            ):
                print(f"WARN {issue.identifier}: blocker {target} is Cancelled")
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony board",
        description="Manage a file-based Kanban tracker.",
    )

    def add_workflow_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workflow", default=None, help="path to WORKFLOW.md")
        p.add_argument("--root", default=None, help="board root (overrides WORKFLOW.md)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize a new board directory")
    p_init.add_argument("root", help="board directory")
    p_init.set_defaults(func=cmd_init)

    p_ls = sub.add_parser("ls", help="list tickets")
    add_workflow_args(p_ls)
    p_ls.add_argument("--state", default=None, help="filter by state (case-insensitive)")
    p_ls.set_defaults(func=cmd_ls)

    p_new = sub.add_parser("new", help="create a ticket")
    add_workflow_args(p_new)
    p_new.add_argument("id", help="ticket identifier (e.g. DEV-001)")
    p_new.add_argument("title", help="ticket title")
    p_new.add_argument("--state", default="Todo")
    p_new.add_argument("--priority", type=int, default=None)
    p_new.add_argument("--labels", default=None, help="comma-separated labels")
    p_new.add_argument(
        "--label",
        action="append",
        default=None,
        help="add one label (repeatable)",
    )
    p_new.add_argument("--description", default=None)
    p_new.add_argument(
        "--description-file",
        default=None,
        help="read the description from PATH ('-' for stdin)",
    )
    p_new.add_argument(
        "--blocked-by",
        action="append",
        default=None,
        metavar="ID",
        help="existing ticket this one depends on (repeatable)",
    )
    p_new.add_argument(
        "--request",
        default=None,
        metavar="REQ",
        help="request grouping id (e.g. REQ-1)",
    )
    p_new.add_argument(
        "--agent-kind",
        "--agent",
        dest="agent_kind",
        choices=sorted(SUPPORTED_AGENT_KINDS),
        default=None,
        help="override backend for this ticket (default: WORKFLOW.md agent.kind)",
    )
    p_new.set_defaults(func=cmd_new)

    p_mv = sub.add_parser("mv", help="change a ticket state")
    add_workflow_args(p_mv)
    p_mv.add_argument("id", help="ticket identifier")
    p_mv.add_argument("state", help="new state")
    p_mv.set_defaults(func=cmd_mv)

    p_update = sub.add_parser("update", help="update an existing ticket")
    add_workflow_args(p_update)
    p_update.add_argument("id", help="ticket identifier")
    p_update.add_argument("--state", default=None, help="new state")
    p_update.add_argument(
        "--blocked-by",
        action="append",
        default=None,
        metavar="ID",
        help="replace the blocker list (repeatable; pass none to clear)",
    )
    p_update.add_argument(
        "--add-blocked-by",
        action="append",
        default=None,
        metavar="ID",
        help="add one blocker, keeping the existing ones (repeatable)",
    )
    p_update.add_argument(
        "--request",
        default=None,
        metavar="REQ",
        help="request grouping id ('' clears it)",
    )
    p_update.set_defaults(func=cmd_update)

    p_show = sub.add_parser("show", help="print a ticket's contents")
    add_workflow_args(p_show)
    p_show.add_argument("id", help="ticket identifier")
    p_show.set_defaults(func=cmd_show)

    p_graph = sub.add_parser("graph", help="print the dependency DAG")
    add_workflow_args(p_graph)
    p_graph.add_argument(
        "--request",
        default=None,
        metavar="REQ",
        help="only tickets in this request group",
    )
    p_graph.set_defaults(func=cmd_graph)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
