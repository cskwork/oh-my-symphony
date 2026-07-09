# Builder evidence - AF-05 and AF-09 backends

## Scope

- PLAN step 3 only: per-turn completion content and persistent Codex stream corruption.
- Source: `plain_cli.py`, `gemini.py`, `claude_code.py`, `per_turn.py`, and `codex.py`.
- Tests: backend contract and backend unit modules only.

## Root cause and decisions

- Plain/Gemini productive completion events placed text under `result`/`response`, while the orchestrator preview contract reads canonical `message`.
- Claude completion events had no string `message`; use the terminal result, falling back to the already-extracted assistant text-block preview.
- A zero exit code was accepted even after stdout normalized to an empty string; emit the existing `turn_failed` event and raise `TurnFailed` before adapter completion.
- Codex stopped reading after the malformed-line limit but remained open with a live subprocess; mark it closed, fail existing waiters, record `codex_stream_corrupt`, and call the shared process-tree reaper directly. Calling `stop()` from the reader was rejected because it cancels and awaits the current reader task.

## RED

Command:

```text
.venv/bin/pytest -q tests/test_backend_contract.py tests/test_backends.py -k 'productive_completion_exposes_canonical_message or zero_exit_whitespace_stdout_is_a_failed_turn or codex_corrupt_stream_closes_reaps_and_later_turn_fails_fast or codex_valid_json_resets_malformed_line_streak'
```

Result: exit 1; `9 failed, 3 passed, 2 skipped, 131 deselected in 0.28s`.

Expected failures:

- four productive Plain/Gemini/Claude payloads lacked `message`;
- Gemini, Agy, Kiro, and OpenCode accepted whitespace-only stdout as completed;
- Codex remained open after `MALFORMED_LINE_LIMIT` malformed lines.

The valid-JSON streak-reset characterization passed before implementation.

## GREEN

Same focused RED command after the minimal source change:

```text
.venv/bin/pytest -q tests/test_backend_contract.py tests/test_backends.py -k 'productive_completion_exposes_canonical_message or zero_exit_whitespace_stdout_is_a_failed_turn or codex_corrupt_stream_closes_reaps_and_later_turn_fails_fast or codex_valid_json_resets_malformed_line_streak'
```

Result: exit 0; `12 passed, 2 skipped, 131 deselected in 0.12s`. The two skips are OpenCode and Pi in the AF-05 canonical-preview test; neither is part of that payload defect.

Backend regression suite:

```text
.venv/bin/pytest -q tests/test_backend_contract.py tests/test_backends.py
```

Result: exit 0; `143 passed, 2 skipped in 0.49s`.

Static checks:

```text
.venv/bin/ruff check src/symphony/backends/plain_cli.py src/symphony/backends/gemini.py src/symphony/backends/claude_code.py src/symphony/backends/per_turn.py src/symphony/backends/codex.py tests/test_backends.py tests/test_backend_contract.py
```

Result: exit 0; `All checks passed!`.

```text
.venv/bin/pyright src/symphony/backends/plain_cli.py src/symphony/backends/gemini.py src/symphony/backends/claude_code.py src/symphony/backends/per_turn.py src/symphony/backends/codex.py
```

Result: exit 0; `0 errors, 0 warnings, 0 informations`.

## Verification boundary

- Direct orchestrator G2 counter/reset coverage is outside this builder's backend-only test ownership. The canonical `message` contract is proven here; the conductor must rely on the orchestrator slice/full-suite verification for the three-turn productive-versus-empty behavior.
- `uv run --extra dev ...` could not initialize `/Users/danny/.cache/uv` in this restricted sandbox (`Operation not permitted`). The existing isolated worktree `.venv` was used for all recorded commands.
