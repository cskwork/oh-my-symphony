# ruff: noqa: F401
"""Behavior-focused coverage for runtime utilities and command entry points.

These tests exercise platform and protocol boundaries that the normal Windows
suite cannot reach without privileged symlinks or another operating system.
"""

from __future__ import annotations

import asyncio

import argparse

import contextlib

import dataclasses

import errno

import importlib

import io

import json

import runpy

import signal

import sqlite3

import subprocess

import sys

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone

from pathlib import Path, PurePosixPath

from types import SimpleNamespace

from typing import Any

import pytest

import httpx

import yaml

from aiohttp.test_utils import TestClient, TestServer

from symphony import (
    _shell,
    artifacts,
    continuous_improvement as ci,
    mock_codex,
    progress_md,
    prompt,
    service,
)

from symphony.backends import redact_session_id

from symphony.backends import claude_code as claude_backend

from symphony.backends import codex as codex_backend

from symphony.backends.gemini import GeminiBackend

from symphony.backends.kiro import _insert_before_prompt_arg

from symphony.backends import pi as pi_backend

from symphony.backends import opencode as opencode_backend

from symphony.backends import per_turn as per_turn_backend

from symphony.backends.prime_agent import PrimeAgentBackend

from symphony.cli import release

from symphony.cli import doctor

from symphony.cli import board as board_cli

from symphony.cli import project as project_cli

from symphony.errors import ConfigValidationError, ResponseError, SymphonyError

from symphony.orchestrator import parsing as orchestrator_parsing

from symphony.orchestrator import release_contracts

from symphony.orchestrator import run_registry as run_registry_module

from symphony.orchestrator.release_contracts import ReleaseValidationResult

from symphony.trackers import _retry

from symphony.trackers import file as tracker_file

from symphony.trackers import jira as jira_tracker

from symphony.trackers import linear as linear_tracker

from symphony import trackers as tracker_factory

from symphony.trackers.validate import _find_cycle_through, topological_order

from symphony.utils import (
    auto_merge,
    git_inspect,
    git_ops,
    git_sandbox,
    keep_awake,
    wiki_sweep,
)

from symphony import logging as symphony_logging

from symphony import runtime_safety, server as server_module, skills, stats

from symphony import issue as issue_module

from symphony import prompt_context, pyright as pyright_module

from symphony.notifications import config as notification_config

from symphony.notifications import dispatcher as notification_dispatcher

from symphony.notifications import slack as notification_slack

from symphony.notifications.events import NotificationEvent

from symphony.workflow import coercion as workflow_coercion

from symphony.workflow import builder as workflow_builder

from symphony.workflow import config as workflow_config

from symphony.workflow import parser as workflow_parser

from symphony.workflow import state as workflow_state

from symphony.workflow import mutate as workflow_mutate

from symphony.workflow.parser import WorkflowDefinition

from symphony.workflow.config import TrackerConfig


class _ProtocolIO:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.lines = list(lines or [])
        self.messages: list[dict[str, Any]] = []
        self.started = False
        self.drains = 0

    async def start(self) -> None:
        self.started = True

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def write_json(self, obj: dict[str, Any]) -> None:
        self.messages.append(obj)

    async def drain(self) -> None:
        self.drains += 1


def _rpc(method: str, message_id: int | None, **params: Any) -> bytes:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if message_id is not None:
        payload["id"] = message_id
    return (json.dumps(payload) + "\n").encode()


class _TerminableProcess:
    def __init__(self, pid: int | None, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminated = 0
        self.killed = 0
        self.raise_terminate = False
        self.raise_kill = False

    def terminate(self) -> None:
        self.terminated += 1
        if self.raise_terminate:
            raise ProcessLookupError

    def kill(self) -> None:
        self.killed += 1
        if self.raise_kill:
            raise ProcessLookupError


class _WakeProcess:
    pid = 99

    def __init__(
        self,
        *,
        alive: bool = True,
        terminate_error: bool = False,
        kill_error: bool = False,
    ) -> None:
        self.alive = alive
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.killed = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        if self.terminate_error:
            raise OSError("cannot terminate")
        self.alive = False

    def wait(self, *, timeout: float) -> int:
        del timeout
        raise subprocess.TimeoutExpired("caffeinate", 2)

    def kill(self) -> None:
        self.killed = True
        if self.kill_error:
            raise OSError("cannot kill")


def _service_record(tmp_path: Path, **overrides: Any) -> service.ServiceRecord:
    values: dict[str, Any] = {
        "workflow_path": tmp_path / "WORKFLOW.md",
        "workflow_dir": tmp_path,
        "host": "127.0.0.1",
        "port": 9999,
        "orchestrator_pid": 42,
        "log_path": tmp_path / "symphony.log",
        "started_at": "2026-08-24T00:00:00Z",
        "orchestrator_command": ["python", "-m", "symphony.cli"],
        "service_instance_id": "a" * 43,
    }
    values.update(overrides)
    return service.ServiceRecord(**values)


def _ci_execution(
    argv: tuple[str, ...],
    *,
    returncode: int | None = 0,
    output: str = "",
    timed_out: bool = False,
    missing: bool = False,
) -> ci.CommandExecution:
    return ci.CommandExecution(argv, returncode, output, timed_out, missing)


def _ci_runner(
    responses: dict[tuple[str, ...], ci.CommandExecution],
) -> Any:
    async def run(
        argv: tuple[str, ...], _cwd: Path, *, timeout_s: float
    ) -> ci.CommandExecution:
        del timeout_s
        return responses[argv]

    return run


def _completed(
    returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["tool"], returncode, stdout, stderr)


def _doctor_cfg(kind: str, command: str = "tool") -> Any:
    commands = {
        name.replace("-", "_"): SimpleNamespace(command=command)
        for name in (
            "agy",
            "claude",
            "codex",
            "gemini",
            "kiro",
            "opencode",
            "pi",
            "prime-agent",
        )
    }
    return SimpleNamespace(agent=SimpleNamespace(kind=kind), **commands)


class _AsyncLineStream:
    def __init__(self, *items: bytes | Exception) -> None:
        self.items = list(items)

    async def readline(self) -> bytes:
        if not self.items:
            return b""
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _WritableInput:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.closed = False

    def write(self, _value: bytes) -> None:
        if self.broken:
            raise BrokenPipeError("closed")

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _pi_runtime_backend() -> Any:
    backend = object.__new__(pi_backend.PiBackend)
    backend._closed = False
    backend._last_message = ""
    backend._stderr_tail = []
    backend._pi = SimpleNamespace(
        command="pi --mode json", turn_timeout_ms=1000, resume_across_turns=True
    )
    backend._session_id = None
    backend._resume_on_next_turn = False
    backend._resume_flag = "--session"
    backend._cwd = Path.cwd()
    backend._on_process_started = None
    backend._active_proc = None
    backend._agent_name = "pi"
    backend._stream_corrupt = None
    backend._expected_resume_session_id = None
    backend._resume_session_confirmed = False
    return backend


def _claude_runtime_backend() -> Any:
    backend = object.__new__(claude_backend.ClaudeCodeBackend)
    backend._closed = False
    backend._last_message = ""
    backend._stderr_tail = []
    backend._claude = SimpleNamespace(
        command="claude", turn_timeout_ms=1, resume_across_turns=True
    )
    backend._git_roots = []
    backend._session_id = None
    backend._resume_on_next_turn = False
    backend._cwd = Path.cwd()
    backend._on_process_started = None
    backend._active_proc = None
    backend._stream_corrupt = None
    backend._expected_resume_session_id = None
    backend._resume_session_confirmed = False
    return backend


def _file_tracker_config(root: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo", "Doing"),
        terminal_states=("Done",),
        board_root=root.resolve(),
    )


def _workflow_definition(tmp_path: Path, config: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition(
        config=config, prompt_template="", source_path=tmp_path / "WORKFLOW.md"
    )


def _project(identifier: str, *, port: int = 9999) -> Any:
    return project_cli.Project(
        id=identifier,
        name=identifier.upper(),
        git_repo=f"C:/repos/{identifier}",
        workflow=f"C:/repos/{identifier}/WORKFLOW.md",
        host="127.0.0.1",
        port=port,
    )


def _cli_runtime_args(workflow: Path, **overrides: Any) -> argparse.Namespace:
    values = {
        "workflow": str(workflow),
        "log_level": None,
        "tui": False,
        "keep_awake": False,
        "progress_md": False,
        "progress_md_path": None,
        "port": None,
        "host": "127.0.0.1",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _cli_runtime_config(*, server_port: int | None = None) -> Any:
    return SimpleNamespace(
        system=SimpleNamespace(keep_awake=False),
        progress=SimpleNamespace(enabled=True, path=None, max_transitions=7),
        server=SimpleNamespace(port=server_port),
    )


def _write_workflow_front(tmp_path: Path, front: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(f"---\n{front}\n---\nBody\n", encoding="utf-8")
    return path


__all__ = [name for name in globals() if not name.startswith("__")]
