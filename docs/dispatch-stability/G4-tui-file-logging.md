# G4 — TUI file-logging gap

**Status:** Shipped (commit `161d4e3` on dev → `main` 2026-05-20)
**Tests:** `test_g4_attach_file_handler_writes_to_path`,
`test_g4_attach_file_handler_is_idempotent`,
`test_g4_attach_file_handler_creates_missing_parent_dir`,
`test_g4_attach_file_handler_appends_existing_content`

## Beginner view

### What you'd see on the board

You ran Symphony via the TUI launcher and something went wrong an hour
in. You open `log/symphony.log` for the post-mortem. The file is almost
empty — or has only output from a previous headless run. The same
workload run via `symphony WORKFLOW.md` (headless) produces the full
structured log you'd expect.

### What's happening underneath

The headless `symphony` command launches the orchestrator as a
subprocess and uses shell redirection to route its stdout/stderr into
`log/symphony.log`. The TUI runs the orchestrator *in-process*, so
there's no subprocess and no shell redirection — the `StructuredLogger`
only writes to stderr (the terminal). Everything the orchestrator says
ends up scrolling past your TUI without ever hitting disk.

### The fix in one paragraph

A new idempotent `attach_file_handler(logger, path)` helper opens the
log file in append mode and registers it as a second sink on the
`StructuredLogger`. The TUI wrapper calls it on startup before
`App.run_async()`. Headless still gets the file via shell redirect;
TUI gets the same file via the in-process handler. Both modes are now
observable post-hoc.

### How to recognize it's working

After launching TUI, in another shell:

```bash
tail -f /path/to/workflow_dir/log/symphony.log
```

Lines should stream in real time while the TUI is up. If
`SYMPHONY_LOG_FILE=/tmp/custom.log` is set, that override path receives
them instead.

## Expert view

### Code path

- `src/symphony/logging.py` (new helper):
  ```python
  def attach_file_handler(logger: StructuredLogger, path: str | Path) -> None:
      resolved = str(Path(path))
      for existing in logger._streams:
          if getattr(existing, "name", None) == resolved:
              return  # idempotent
      Path(resolved).parent.mkdir(parents=True, exist_ok=True)
      fh = open(resolved, "a", encoding="utf-8")
      logger.add_stream(fh)
  ```

- `src/symphony/tui/app.py::KanbanTUI` (the async wrapper):
  ```python
  async def run(self) -> None:
      self._attach_file_log_sink()
      self._app = KanbanApp(self._orch, self._ws)
      await self._app.run_async()

  def _attach_file_log_sink(self) -> None:
      try:
          override = os.environ.get("SYMPHONY_LOG_FILE")
          if override:
              log_path = Path(override)
          else:
              cfg = self._ws.current()
              workflow_dir = cfg.workflow_path.parent if cfg else None
              if workflow_dir is None:
                  return
              log_path = workflow_dir / "log" / "symphony.log"
          attach_file_handler(get_logger(), log_path)
      except Exception:
          pass
  ```

### Invariants

1. **Idempotency by path:** calling `attach_file_handler(logger, "x")`
   N times leaves exactly one stream pointing at `"x"`. Streams are
   matched by Python's `file.name` attribute, which `open()` populates
   with the string path passed in.
2. **No startup hang on broken sink:** the wrapper swallows all
   exceptions. A read-only filesystem or a permission error must not
   prevent the TUI from coming up.

### Why a second stream, not a logging-module HandlerSet?

`StructuredLogger` is a hand-rolled key=value emitter, not a
`logging.Logger` from the stdlib. It already supports multiple `TextIO`
streams via `add_stream`. Adding a `FileHandler` would need a separate
adapter; reusing the existing stream list is the smaller change.

### Why open in append mode?

A long-running TUI session restarted mid-day would truncate the log if
we opened with `"w"`. `"a"` preserves prior diagnostic data — important
because the TUI is often launched immediately after a headless run for
investigation.

### Failure mode it replaces

TUI sessions were silent post-hoc. Operators couldn't reproduce TUI
bugs without re-running headless, losing the in-session state.

### Risk surface

Double-handler duplication if the helper is called twice with the same
path. Mitigated by the `getattr(existing, "name", None)` short-circuit.

### Related

- The headless service path in `src/symphony/service.py:_popen_detached`
  still does its own redirect — G4 only adds the in-process sink and
  doesn't change subprocess wiring.
- Environment override `SYMPHONY_LOG_FILE` works in both modes; the
  TUI helper honors it, and the headless path's `--log-file` argument
  (when added) would too.
