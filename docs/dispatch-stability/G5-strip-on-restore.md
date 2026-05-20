# G5 — Strip `## Conflict` / `## Budget Exceeded` on restore

**Status:** Shipped (commit `161d4e3` on dev → `main` 2026-05-20)
**Tests:** `test_g5_strip_conflict_and_budget_sections_on_active_restore`,
`test_g5_strip_does_not_fire_on_transition_into_terminal_state`,
`test_g5_strip_preserves_operator_authored_content_between_warnings`,
`test_g5_strip_handles_warning_at_end_of_body`,
`test_g5_strip_is_idempotent`

## Beginner view

### What you'd see on the board

A ticket was blocked on a conflict. Symphony wrote a `## Conflict` note
into the ticket body explaining why. You fixed the cause and moved the
ticket back into Todo. But the ticket body still shows the
`## Conflict` heading. Your operators read the board and assume the
ticket is still conflict-blocked. Nobody picks it up.

### What's happening underneath

When Symphony blocks a ticket for conflict, it calls
`_tracker_call_append_note(..., "Conflict", body)` which appends a
`## Conflict\n\n<body>` section to the ticket markdown. Same for
`Budget Exceeded`. The append path was symmetric: it added on block,
but nothing removed it on restore.

### The fix in one paragraph

The file tracker's `update_state(issue, target_state)` now checks
whether `target_state` is in the configured `active_states` set. If
so — before doing the actual state transition — it strips any
`## Conflict` / `## Budget Exceeded` sections from the body. Operator
authored content with those headings sits between other paragraphs and
is preserved; only the orchestrator-authored block (heading + content
up to the next `##`) is removed. Transitions into non-active states
(Done, Cancelled, Blocked) leave the body alone — the warnings still
matter when the ticket is at rest in a terminal state.

### How to recognize it's working

Move a ticket from Blocked → Todo. The `## Conflict` section in the
body disappears. The operator-authored preamble and any subsequent
`##` sections remain.

## Expert view

### Code path

- `src/symphony/trackers/file.py` (new module-level helpers):
  ```python
  _WARNING_HEADING_RE = re.compile(
      r"^##\s+(?:Conflict|Budget\s+Exceeded)\s*$",
      re.IGNORECASE | re.MULTILINE,
  )

  def _strip_warning_blocks(body: str) -> str:
      matches = list(_WARNING_HEADING_RE.finditer(body))
      if not matches:
          return body
      out = body
      for match in reversed(matches):
          start = match.start()
          next_heading = re.search(r"^##\s+\S", out[match.end():], re.MULTILINE)
          end = match.end() + next_heading.start() if next_heading else len(out)
          out = out[:start] + out[end:]
      return out.rstrip() + ("\n" if body.endswith("\n") else "")
  ```

- `FileBoardTracker.update_state`:
  ```python
  def update_state(self, issue: Issue, target_state: str) -> None:
      if target_state.lower() in self._active:
          self._strip_orchestrator_warning_sections(issue.identifier)
      self.transition(issue.identifier, target_state)
  ```

### Invariants

1. **Direction-gated:** strip fires only on transitions *into* an active
   state. Transitions into terminal states (Done, Cancelled, Blocked)
   keep the warning intact — that's where it's most useful for audit.
2. **Section-scoped:** the strip removes a `## Conflict` (or `## Budget
   Exceeded`) heading and everything up to the next `## ` heading or
   end-of-body. Operator-authored sections between warning sections
   survive intact.
3. **Idempotent:** if the body has no warning sections, the body is
   returned unchanged (early return). If the body already had the
   sections stripped on a previous restore, the second restore is a
   no-op for the body content.

### Why only the file tracker?

Plan from `docs/improvements/dispatch-stability-2026-05-20.md`:
> Non-file trackers (Jira / Linear) get a no-op for now — those bodies
> are owned by the remote tracker and we will not rewrite them silently.

A Jira description belongs to the Jira workspace; an autonomous strip
on every restore would surprise (and probably anger) ops. The fix
stays local to the file tracker where Symphony owns the body file.

### Why walk matches in reverse?

Deleting forwards would invalidate later offsets. Reverse-walk lets
each `body[:start] + body[end:]` mutation work on offsets that haven't
been touched yet.

### Failure mode it replaces

Board UIs (TUI, web viewer) showed stale `## Conflict` / `## Budget
Exceeded` text long after the conflict cleared. Operators triaging
the board got false signals.

### Risk surface

Stripping legitimate operator-authored content that happens to use the
same headings. Mitigated by:
1. The regex matches only `## Conflict` and `## Budget Exceeded` with
   trailing whitespace — not arbitrary `## Conflict-related` headings.
2. The strip only fires on transition into an active state. An
   operator who wants to keep a `## Conflict` note around can move the
   ticket to Done (or any non-active state) and the section persists.

### Related

- The append side (`_tracker_call_append_note`) is unchanged. G5 is
  purely additive on the restore path.
- `_active` is populated from `cfg.tracker.active_states` at
  `FileBoardTracker.__init__`, so the strip uses the same source of
  truth as the rest of the eligibility logic.
