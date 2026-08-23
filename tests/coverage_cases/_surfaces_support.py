# ruff: noqa: F401
"""Behavioral coverage for operator-facing boundary and lifecycle surfaces.

These tests deliberately exercise observable validation, HTTP, filesystem, Git,
preview-lifecycle, chat-projection, and TUI-rendering contracts.  External
processes and services are replaced only at their documented boundaries.
"""

from __future__ import annotations

import asyncio

import json

import os

import socket

import subprocess

import sys

from dataclasses import replace

from datetime import datetime, timedelta, timezone

from pathlib import Path

from types import SimpleNamespace

from typing import Any, cast

import pytest

from aiohttp import WSCloseCode, WSMsgType, web

from aiohttp.test_utils import TestClient, TestServer

from rich.text import Text

from textual.app import App

from textual.containers import VerticalScroll

from textual.widgets import Input, Select, Static, TextArea

import symphony.chat as chat

import symphony.hub as hub

import symphony.product_preview as product_preview

import symphony.projects as projects

import symphony.webapi as webapi

import symphony.workspace as workspace

import symphony.tui.app as tui_app

import symphony.stats as stats_module

import symphony.trackers.file as file_tracker_module

from symphony.issue import BlockerRef, Issue

from symphony.product_preview import ProductPreviewError, ProductPreviewManager

from symphony.orchestrator.scheduler import RequestGroupKey

from symphony.projects import Project, ProjectError, ProjectRegistry

from symphony.tui.app import KanbanApp, KanbanTUI

from symphony.tui.helpers import (
    _CardStatus,
    _append_attention_meta,
    _append_token_meta,
    _attention_label,
    _compact_rate_limits,
    _fetch_candidates,
    _fetch_terminals,
    _first_meaningful_line,
    _stage_position,
)

from symphony.tui.screens import (
    EditIssueScreen,
    NewIssueScreen,
    StatsScreen,
    TicketDetailScreen,
    _RefreshNow,
    _fmt_seconds,
)

from symphony.tui.widgets import DetailPane, IssueCard, Lane, StatsBar

from symphony.workflow.mutate import WorkflowMutationError


def _issue(**overrides: Any) -> Issue:
    values: dict[str, Any] = {
        "id": "issue-1",
        "identifier": "ISSUE-1",
        "title": "Ship visible behavior",
        "description": "# Internal heading\n\nUser-visible summary",
        "priority": 2,
        "state": "Todo",
        "labels": ("release", "ui"),
    }
    values.update(overrides)
    return Issue(**values)


def _project(tmp_path: Path, **overrides: Any) -> Project:
    values: dict[str, Any] = {
        "id": "demo",
        "name": "Demo",
        "git_repo": str(tmp_path),
        "workflow": str(tmp_path / "WORKFLOW.md"),
        "host": "127.0.0.1",
        "port": 9999,
    }
    values.update(overrides)
    return Project(**values)


class _HubRegistry:
    def __init__(self, record: Project) -> None:
        self.record = record
        self.running = False
        self.result = 0

    def list(self) -> list[Project]:
        return [self.record]

    def status(self, project_id: str) -> dict[str, Any]:
        if project_id != self.record.id:
            raise KeyError(project_id)
        return {"running": self.running}

    def start(self, project_id: str) -> int:
        if project_id != self.record.id:
            raise KeyError(project_id)
        if self.result == 0:
            self.running = True
        return self.result

    def stop(self, project_id: str) -> int:
        if project_id != self.record.id:
            raise KeyError(project_id)
        return self.result


class _WebSocketBoundaryManager:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.queues: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self.closed = asyncio.Event()
        self.focused = asyncio.Event()
        self.unsubscribed = asyncio.Event()
        self.focus: str | None = "not-set"

    def subscribe(
        self, _focus: str | None = None
    ) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self.queues.discard(queue)
        self.unsubscribed.set()

    def set_focus(
        self, _queue: asyncio.Queue[dict[str, Any] | None], session_id: str | None
    ) -> None:
        self.focus = session_id
        self.focused.set()

    def snapshot(self, *_args: Any) -> dict[str, Any]:
        return {"active": False}

    def list_sessions(self) -> dict[str, Any]:
        return {"sessions": []}

    async def close(self) -> None:
        self.closed.set()
        for queue in tuple(self.queues):
            queue.put_nowait(None)

    def emit(self, row: dict[str, Any]) -> None:
        for queue in tuple(self.queues):
            queue.put_nowait(row)


def _websocket_boundary_app(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[web.Application, _WebSocketBoundaryManager]:
    monkeypatch.setattr(webapi, "ChatManager", _WebSocketBoundaryManager)
    app = web.Application()
    app[webapi.BIND_HOST_KEY] = "127.0.0.1"
    webapi._register_chat_routes(
        app,
        cast(Any, SimpleNamespace(config=lambda: None)),
        cast(Any, SimpleNamespace(request_refresh=lambda: None)),
    )
    return app, cast(_WebSocketBoundaryManager, app[webapi.CHAT_MANAGER_KEY])


def _minimal_project_source(path: Path, *, git: bool) -> Path:
    path.mkdir()
    for name in projects._BUNDLE_FILES:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
            "workspace:\n  root: ./workspaces\n---\nbody\n"
            if name == "WORKFLOW.file.example.md"
            else "bundle\n"
        )
        target.write_text(content, encoding="utf-8")
    for name in projects._BUNDLE_DIRS:
        (path / name).mkdir(parents=True)
    if git:
        _git_text(path, "init", "-b", "main")
        _git_text(path, "config", "user.name", "Coverage Test")
        _git_text(path, "config", "user.email", "coverage@example.com")
        _git_text(path, "add", ".")
        _git_text(path, "commit", "-m", "bundle")
    return path


def _chat_session(**overrides: Any) -> chat.ChatSession:
    values: dict[str, Any] = {
        "session_id": "20260824-120000-abcdef",
        "mode": "qa",
        "agent_kind": "codex",
        "mode_enforced": True,
        "created_at": "2026-08-24T12:00:00Z",
    }
    values.update(overrides)
    return chat.ChatSession(**values)


class _ScreenHost(App[None]):
    def __init__(self, screen: Any) -> None:
        super().__init__()
        self.modal = screen
        self.result: Any = "pending"

    def on_mount(self) -> None:
        self.push_screen(self.modal, lambda result: setattr(self, "result", result))


def _git_text(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


__all__ = [name for name in globals() if not name.startswith("__")]
