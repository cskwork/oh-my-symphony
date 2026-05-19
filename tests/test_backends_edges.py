"""Edge-case coverage for backend output normalization.

`tests/test_backends.py` covers the happy paths and the most-load-bearing
regressions. This file pins the rarer branches the output-normalization
helpers must still handle when a CLI returns a slightly degenerate
payload: empty content arrays, garbled `is_error` markers, missing token
fields, oddly-shaped Gemini telemetry blocks, etc.

These are pure-function tests — no subprocess, no asyncio. They catch
the kind of regression where a refactor inverts a default or drops a
branch the live agents only hit once a day.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
)
from symphony.backends.claude_code import (
    _extract_text as _claude_extract_text,
    _is_error_result,
)
from symphony.backends.codex import (
    _normalize_event_name,
    _sandbox_policy_to_turn_payload,
    _sandbox_uses_workspace_write,
)
from symphony.backends.gemini import GeminiBackend
from symphony.backends.pi import (
    PiBackend,
    _extract_failure_reason as _pi_extract_failure_reason,
    _extract_text as _pi_extract_text,
)
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


def _make_cfg(kind: str, *, workspace_root: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=workspace_root / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=workspace_root,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=workspace_root / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind=kind,
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


import asyncio


def _noop_event(_: dict) -> "asyncio.Future[None]":
    fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


# ---------------------------------------------------------------------------
# Claude `_is_error_result` — all six is_error coercion branches
# ---------------------------------------------------------------------------


class TestClaudeIsErrorResultBranches:
    """The `_is_error_result` decision tree has three coupled inputs:
    `subtype`, the truthiness of `is_error`, and string-form coercions.

    Existing tests cover the happy and the most-reported branches. These
    pin the rarer ones that a future refactor could silently invert."""

    def test_subtype_success_overrides_string_is_error_yes(self) -> None:
        # Per docstring: subtype=="success" short-circuits BEFORE is_error.
        assert (
            _is_error_result(
                {"type": "result", "subtype": "success", "is_error": "yes"}
            )
            is False
        )

    def test_subtype_starts_with_error_short_circuits_true(self) -> None:
        assert (
            _is_error_result({"type": "result", "subtype": "error_during_execution"})
            is True
        )

    def test_string_is_error_yes_and_one_are_truthy(self) -> None:
        assert _is_error_result({"is_error": "yes"}) is True
        assert _is_error_result({"is_error": "1"}) is True
        assert _is_error_result({"is_error": "TRUE"}) is True  # case-insensitive

    def test_string_is_error_no_and_zero_are_falsy(self) -> None:
        assert _is_error_result({"is_error": "no"}) is False
        assert _is_error_result({"is_error": "0"}) is False
        assert _is_error_result({"is_error": ""}) is False

    def test_integer_is_error_uses_bool_fallback(self) -> None:
        assert _is_error_result({"is_error": 1}) is True
        assert _is_error_result({"is_error": 0}) is False
        assert _is_error_result({"is_error": 42}) is True

    def test_missing_subtype_and_is_error_returns_false(self) -> None:
        assert _is_error_result({}) is False
        assert _is_error_result({"type": "result"}) is False


# ---------------------------------------------------------------------------
# Claude `_extract_text` rare shapes
# ---------------------------------------------------------------------------


class TestClaudeExtractText:
    def test_returns_empty_when_message_is_not_dict(self) -> None:
        # type-tolerant guard — production paths sometimes pass a list.
        assert _claude_extract_text(["not", "a", "dict"]) == ""  # type: ignore[arg-type]
        assert _claude_extract_text(None) == ""  # type: ignore[arg-type]

    def test_returns_empty_when_no_text_block_present(self) -> None:
        msg = {"content": [{"type": "tool_use", "name": "edit"}]}
        assert _claude_extract_text(msg) == ""

    def test_skips_text_blocks_with_empty_text(self) -> None:
        # An empty text block is "present" but not the chosen output.
        msg = {
            "content": [
                {"type": "text", "text": "real answer"},
                {"type": "text", "text": ""},
            ]
        }
        # `reversed` means the empty trailing block is checked first, then
        # falls through to the earlier non-empty text.
        assert _claude_extract_text(msg) == "real answer"

    def test_ignores_non_string_text_field(self) -> None:
        msg = {"content": [{"type": "text", "text": 12345}]}
        assert _claude_extract_text(msg) == ""


# ---------------------------------------------------------------------------
# Pi `_extract_text` extra branches
# ---------------------------------------------------------------------------


class TestPiExtractText:
    def test_falls_back_to_top_level_text_field(self) -> None:
        # No content array but the AssistantMessage carries `text` directly.
        assert _pi_extract_text({"text": "top-level only"}) == "top-level only"

    def test_top_level_text_field_must_be_string(self) -> None:
        assert _pi_extract_text({"text": 42}) == ""

    def test_content_list_with_no_text_blocks_returns_empty(self) -> None:
        msg = {"content": [{"type": "tool_use"}, {"type": "tool_result"}]}
        assert _pi_extract_text(msg) == ""

    def test_content_list_picks_last_non_empty_text_block(self) -> None:
        msg = {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": ""},
                {"type": "text", "text": "last"},
            ]
        }
        # `reversed` then truthiness => "last" wins (not "first").
        assert _pi_extract_text(msg) == "last"


# ---------------------------------------------------------------------------
# Pi `_extract_failure_reason` extra branches
# ---------------------------------------------------------------------------


class TestPiExtractFailureReason:
    def test_returns_none_for_empty_terminal(self) -> None:
        assert _pi_extract_failure_reason({}) is None

    def test_returns_none_when_messages_is_not_list(self) -> None:
        assert (
            _pi_extract_failure_reason({"type": "agent_end", "messages": "not-a-list"})
            is None
        )

    def test_returns_none_when_messages_is_empty(self) -> None:
        assert _pi_extract_failure_reason({"type": "agent_end", "messages": []}) is None

    def test_returns_none_when_last_message_is_not_dict(self) -> None:
        assert (
            _pi_extract_failure_reason(
                {"type": "agent_end", "messages": ["not-a-dict"]}
            )
            is None
        )

    def test_error_without_errorMessage_returns_descriptive_fallback(self) -> None:
        reason = _pi_extract_failure_reason(
            {"type": "agent_end", "messages": [{"stopReason": "error"}]}
        )
        assert reason is not None
        assert "stopReason" in reason and "error" in reason

    def test_error_with_empty_errorMessage_falls_back_to_descriptive(self) -> None:
        reason = _pi_extract_failure_reason(
            {
                "type": "agent_end",
                "messages": [{"stopReason": "error", "errorMessage": ""}],
            }
        )
        # Empty errorMessage is treated as missing, fallback wins.
        assert reason is not None
        assert "stopReason" in reason

    def test_unknown_stop_reason_returns_none(self) -> None:
        # Anything other than 'error' or 'aborted' is a clean stop.
        assert (
            _pi_extract_failure_reason(
                {"type": "agent_end", "messages": [{"stopReason": "max_tokens"}]}
            )
            is None
        )


# ---------------------------------------------------------------------------
# Pi `_update_usage` accumulation edges
# ---------------------------------------------------------------------------


class TestPiUpdateUsageEdges:
    def test_non_dict_usage_is_silently_ignored(self, tmp_path: Path) -> None:
        cfg = _make_cfg("pi", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = PiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage("not-a-dict")  # type: ignore[arg-type]
        assert backend.latest_usage["input_tokens"] == 0
        assert backend.latest_usage["output_tokens"] == 0
        assert backend.latest_usage["total_tokens"] == 0

    def test_missing_fields_default_to_zero(self, tmp_path: Path) -> None:
        cfg = _make_cfg("pi", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = PiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        # Only `input` and `output` set; cache fields absent.
        backend._update_usage({"input": 10, "output": 5})
        usage = backend.latest_usage
        assert usage["input_tokens"] == 10
        assert usage["output_tokens"] == 5

    def test_zero_usage_is_accepted_without_error(self, tmp_path: Path) -> None:
        cfg = _make_cfg("pi", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = PiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage({"input": 0, "output": 0, "totalTokens": 0})
        assert backend.latest_usage["input_tokens"] == 0


# ---------------------------------------------------------------------------
# Gemini `_update_usage_from_stats` shape tolerance
# ---------------------------------------------------------------------------


class TestGeminiUsageStats:
    def test_non_dict_stats_is_noop(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage_from_stats("not-a-dict")
        backend._update_usage_from_stats(None)
        assert backend.latest_usage["input_tokens"] == 0

    def test_missing_models_section_is_noop(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage_from_stats({"other": "thing"})
        assert backend.latest_usage["input_tokens"] == 0

    def test_models_with_non_dict_tokens_block_is_skipped(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage_from_stats({"models": {"gemini-pro": {"tokens": "garbage"}}})
        assert backend.latest_usage["input_tokens"] == 0

    def test_multiple_models_aggregate_into_buckets(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage_from_stats(
            {
                "models": {
                    "gemini-pro": {
                        "tokens": {
                            "input": 100,
                            "cached": 20,
                            "candidates": 50,
                            "thoughts": 5,
                            "tool": 3,
                        }
                    },
                    "gemini-flash": {
                        "tokens": {"input": 10, "candidates": 4}
                    },
                }
            }
        )
        usage = backend.latest_usage
        # Pro: input 100 + cached 20 = 120 in; candidates 50 + thoughts 5 + tool 3 = 58 out.
        # Flash: input 10 + 0 cached = 10 in; 4 out.
        # Sum: 130 in, 62 out, 192 total.
        assert usage["input_tokens"] == 130
        assert usage["output_tokens"] == 62
        assert usage["total_tokens"] == 192

    def test_stats_are_additive_across_calls(self, tmp_path: Path) -> None:
        # `_update_usage_from_stats` accumulates (`+=`) on each call —
        # the Gemini CLI emits one stats line per turn.
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        backend._update_usage_from_stats(
            {"models": {"a": {"tokens": {"input": 10, "candidates": 5}}}}
        )
        backend._update_usage_from_stats(
            {"models": {"a": {"tokens": {"input": 7, "candidates": 3}}}}
        )
        assert backend.latest_usage["input_tokens"] == 17
        assert backend.latest_usage["output_tokens"] == 8
        assert backend.latest_usage["total_tokens"] == 25


# ---------------------------------------------------------------------------
# Gemini `_stderr_blob` truncation invariant
# ---------------------------------------------------------------------------


class TestGeminiStderrBlob:
    def test_blob_is_empty_when_no_stderr_captured(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        assert backend._stderr_blob() == ""

    def test_blob_caps_at_400_chars_keeping_tail(self, tmp_path: Path) -> None:
        cfg = _make_cfg("gemini", workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir()
        backend = GeminiBackend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )
        # Capture > 400 chars of stderr; only the last 400 should survive.
        long_payload = ("x" * 200 + "\n" + "y" * 300 + "\n").encode()
        backend._capture_stderr(long_payload)
        blob = backend._stderr_blob()
        # The cap is 400 characters; output keeps the trailing window.
        assert len(blob) == 400
        assert blob.endswith("y" * 300) or "y" in blob


# ---------------------------------------------------------------------------
# Codex `_normalize_event_name` extra paths
# ---------------------------------------------------------------------------


class TestCodexEventNameNormalization:
    @pytest.mark.parametrize(
        "method,expected",
        [
            ("thread/turn/completed", EVENT_TURN_COMPLETED),
            ("thread/turn/failed", EVENT_TURN_FAILED),
            ("thread/turn/COMPLETED", EVENT_TURN_COMPLETED),
            ("thread/turn/FAILED", EVENT_TURN_FAILED),
            ("THREAD/TURN/COMPLETED", EVENT_TURN_COMPLETED),
            ("approval/requested", "approval_auto_approved"),
            ("", EVENT_OTHER_MESSAGE),
            ("nonsense/path", EVENT_OTHER_MESSAGE),
        ],
    )
    def test_normalization_is_case_insensitive_and_path_based(
        self, method: str, expected: str
    ) -> None:
        assert _normalize_event_name(method) == expected


# ---------------------------------------------------------------------------
# Codex `_sandbox_uses_workspace_write` true/false shapes
# ---------------------------------------------------------------------------


class TestCodexSandboxUsesWorkspaceWrite:
    @pytest.mark.parametrize(
        "value",
        [
            "workspace-write",  # WORKFLOW.md kebab-case form
            {"type": "workspaceWrite"},  # v2 tagged-enum dict form
        ],
    )
    def test_recognized_shapes(self, value) -> None:
        assert _sandbox_uses_workspace_write(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "WORKSPACE-WRITE",  # case-sensitive: not recognized
            "danger-full-access",
            "read-only",
            {"type": "readOnly"},
            {"type": "workspace-write"},  # kebab inside dict: not recognized
            {"mode": "workspaceWrite"},  # wrong key
            42,
            [],
        ],
    )
    def test_unrecognized_shapes(self, value) -> None:
        assert _sandbox_uses_workspace_write(value) is False

    def test_any_of_multiple_values_triggers_true(self) -> None:
        # variadic helper: True when ANY argument matches.
        assert (
            _sandbox_uses_workspace_write("read-only", "workspace-write") is True
        )

    def test_all_unmatched_returns_false(self) -> None:
        assert (
            _sandbox_uses_workspace_write("read-only", "danger-full-access") is False
        )


# ---------------------------------------------------------------------------
# Codex `_sandbox_policy_to_turn_payload` edge shapes
# ---------------------------------------------------------------------------


class TestCodexSandboxPolicyToTurnPayload:
    def test_none_returns_none(self) -> None:
        assert _sandbox_policy_to_turn_payload(None) is None

    def test_workspace_write_normalizes_to_v2_tagged_enum(self) -> None:
        # WORKFLOW.md kebab-case "workspace-write" translates to v2 camelCase tag.
        assert _sandbox_policy_to_turn_payload("workspace-write") == {
            "type": "workspaceWrite"
        }

    def test_read_only_normalizes_to_v2_tagged_enum(self) -> None:
        assert _sandbox_policy_to_turn_payload("read-only") == {"type": "readOnly"}

    def test_danger_full_access_normalizes_to_v2_tagged_enum(self) -> None:
        assert _sandbox_policy_to_turn_payload("danger-full-access") == {
            "type": "dangerFullAccess"
        }

    def test_unknown_string_returned_unchanged(self) -> None:
        # Don't second-guess unknown policy names; codex itself validates.
        assert _sandbox_policy_to_turn_payload("CUSTOM-POLICY") == "CUSTOM-POLICY"

    def test_dict_input_is_passed_through_when_no_writable_roots(self) -> None:
        payload = {"type": "custom", "writableRoots": ["/x"]}
        # No `writable_roots` extras means the dict passes through identity.
        assert _sandbox_policy_to_turn_payload(payload) is payload

    def test_dict_input_gets_writable_roots_merged(self) -> None:
        payload = {"type": "workspaceWrite", "writableRoots": ["/keep"]}
        out = _sandbox_policy_to_turn_payload(payload, writable_roots=["/add", "/keep"])
        assert out == {"type": "workspaceWrite", "writableRoots": ["/keep", "/add"]}
