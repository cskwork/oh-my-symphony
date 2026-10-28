"""`symphony release check` — host-side application release validation (via `symphony.cli.release`)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from pathlib import PurePosixPath

from symphony.errors import SymphonyError
from symphony.release.contracts import validate_release_contract
from symphony.workflow import (
    DEFAULT_BOARD_ROOT_NAME,
    build_service_config,
    expand_path_value,
    load_workflow,
    resolve_var_indirection,
    resolve_workflow_path,
)
from symphony.workflow.parser import WorkflowDefinition
from symphony.utils.git_sandbox import resolve_git_common_dir


def _configured_board_mount(workflow: WorkflowDefinition) -> PurePosixPath | None:
    """Apply the config builder's board-path defaults without resolving its base."""
    tracker = workflow.config.get("tracker") or {}
    if not isinstance(tracker, dict):
        tracker = {}
    kind = tracker.get("kind")
    tracker_is_file = isinstance(kind, str) and kind.strip() == "file"
    raw = tracker.get("board_root")
    if not isinstance(raw, str) or not raw:
        return (
            PurePosixPath(DEFAULT_BOARD_ROOT_NAME)
            if tracker_is_file
            else None
        )
    resolved = resolve_var_indirection(raw) if raw.startswith("$") else raw
    if not isinstance(resolved, str) or not resolved:
        return None
    mount = PurePosixPath(expand_path_value(resolved))
    return None if mount.is_absolute() else mount


def _canonical_repository_root(workspace_root: Path) -> Path | None:
    """Return the checkout owning a workspace's shared Git common directory."""
    common_dir = resolve_git_common_dir(workspace_root)
    if common_dir is None:
        return None
    try:
        common_dir = common_dir.resolve(strict=True)
        repository_root = common_dir.parent.resolve(strict=True)
        repository_git_dir = (repository_root / ".git").resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if not common_dir.is_dir() or repository_git_dir != common_dir:
        return None
    return repository_root


def _canonical_board_root(
    *,
    configured_board_root: Path | None,
    board_mount: PurePosixPath | None,
    repository_root: Path,
) -> Path | None:
    """Anchor relative board configuration to the host checkout, not a copy."""
    if board_mount is None:
        return configured_board_root
    return repository_root.joinpath(*board_mount.parts)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony release",
        description="Validate machine-bound application release evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check",
        help="validate release-contract.yaml and one verifier's evidence",
    )
    check.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    check.add_argument("--ticket", required=True, help="current verifier ticket ID")
    check.add_argument(
        "--workspace",
        required=True,
        help="ticket workspace containing release-contract.yaml and docs/<ticket>/qa",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="emit the immutable validation result as JSON",
    )
    return parser


def _check(args: argparse.Namespace) -> int:
    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.is_file():
        print(f"symphony release: workflow file not found: {workflow_path}", file=sys.stderr)
        return 2
    try:
        workflow = load_workflow(workflow_path)
        cfg = build_service_config(workflow)
    except SymphonyError as exc:
        print(f"symphony release: workflow load failed: {exc}", file=sys.stderr)
        return 2

    workspace_root = Path(args.workspace).expanduser()
    repository_root = _canonical_repository_root(workspace_root)
    if repository_root is None:
        print(
            "symphony release: workspace Git host repository could not be resolved",
            file=sys.stderr,
        )
        return 2
    board_mount = _configured_board_mount(workflow)
    board_root = _canonical_board_root(
        configured_board_root=cfg.tracker.board_root,
        board_mount=board_mount,
        repository_root=repository_root,
    )
    result = validate_release_contract(
        workspace_root=workspace_root,
        repository_root=repository_root,
        verifier_ticket=args.ticket,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
        board_root=board_root,
        board_mount=board_mount,
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status}  app.release-contract")
        print(result.note_text)
        if result.target_sha:
            print(f"Target: {result.target_branch}@{result.target_sha}")
        if result.contract_sha256:
            print(f"Contract SHA-256: {result.contract_sha256}")
        print(f"Fingerprint: {result.fingerprint}")
    return 0 if result.passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
