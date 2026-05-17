# SMA-26 — codex 0.130 `app-server` silent hang on invalid model

## As-Is

Symphony's codex backend (`src/symphony/backends/codex.py`) dispatched
a codex worker that died within ~1s, reporting
`error: port_exit: subprocess stdout closed`. Symptom was identical
across 6 retries — the worker workspace was created cleanly, the
`before_run` hook ran fine, and then the worker exited as soon as the
backend tried to talk to the subprocess.

## Root cause

`~/.codex/config.toml` pinned `model = "gpt-5.5"`, which is not a
valid model identifier in codex CLI `0.130.0`. When Symphony sent
`thread/start` over the JSON-RPC stdio transport, codex accepted the
request but **never sent a response** — no error, no notification,
no exit. `_request()` in the codex backend waits indefinitely on a
future, so Symphony surfaced the symptom only later when the
subprocess eventually terminated (for unrelated reasons), at which
point the pending future was rejected with
`PortExit("subprocess stdout closed")`.

This is two compounding bugs:

1. **codex CLI 0.130** silently hangs on an invalid model at
   `thread/start` instead of returning a JSON-RPC error.
2. **Symphony's `_request`** has no per-call timeout, so the silent
   hang is only surfaced as a confusing `port_exit` long after the
   real problem.

## Reproduction

Pipe an `initialize` + `thread/start` to `codex app-server`. With
the default user config (`model = "gpt-5.5"`):

```bash
( printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"symphony","version":"0.2.0"}}}\n'
  printf '{"jsonrpc":"2.0","id":2,"method":"thread/start","params":{"cwd":"/tmp"}}\n'
  sleep 5
) | timeout 7 codex app-server
```

Observed: only the `initialize` response and a
`remoteControl/status/changed` notification come back. `thread/start`
(id=2) produces zero bytes, then `timeout` kills the process at 7s.

Re-run with `-c model=gpt-5-codex` appended and the `thread/start`
response (`result.thread.id`) lands immediately.

## To-Be (fix applied)

`WORKFLOW.md`:

```yaml
codex:
  command: codex app-server -c model=gpt-5-codex
```

The `-c key=value` form is codex's own config override and applies
**only** to spawns from Symphony — the user's `~/.codex/config.toml`
stays untouched (other tools that share the same home may rely on it).

Verified with a manual handshake: `thread/start` returned
`result.thread.id = 019e34f0-c760-7540-b18e-d9fbfedd65bd` plus the
expected `thread/started` notification and MCP-server startup events.

## Follow-ups worth filing separately

- **Upstream codex**: invalid `model` should produce a JSON-RPC
  `error` on `thread/start` instead of hanging. The current behaviour
  is indistinguishable from a deadlocked server.
- **Symphony backend**: add a per-call timeout (or at least to the
  `thread/start` handshake) so silent hangs surface as a clear
  `RequestTimeout` rather than a stale `port_exit` minutes later.

## References

- `src/symphony/backends/codex.py:268` — `start()` spawn.
- `src/symphony/backends/codex.py:379` — `initialize()` handshake.
- `src/symphony/backends/codex.py:388` — `start_session()` calls
  `thread/start` and awaits `result.thread.id`.
- `src/symphony/backends/codex.py:610` — where `PortExit` is raised
  when the subprocess closes stdout while a request is still pending.
- `codex-cli 0.130.0` — JSON schema at
  `codex app-server generate-json-schema --out <dir>`; `v1` and `v2`
  ClientRequest definitions confirm `initialize` + `thread/start`
  shapes that Symphony uses.
