# GOAL - Frontier 001 Safe Jira Inbox

Deliver a default-off, read-only secondary Jira intake that mirrors only actionable A20 issues assigned
to the authenticated operator into Symphony's local file board. Jira owns a bounded source block; the
file board owns workflow state and delivery evidence.

## Success Criteria

- [x] An absent or disabled `jira_intake` block preserves current configuration, polling, and dispatch behavior.
- [x] Intake JQL contains an escaped project, a non-empty configured status allowlist, and
  `assignee = currentUser()`; `/rest/api/3/myself` identity is checked against every result.
- [x] Missing, inactive, unassigned, malformed, or foreign-assignee results fail closed before any board write.
- [x] Search pagination is provably complete and bounded: missing/repeated tokens, missing `isLast`, duplicate keys,
  project-key mismatches, page caps, or issue caps produce zero board writes.
- [x] An assigned subtask with an empty body includes its parent summary and description; unavailable or empty
  required parent context fails closed.
- [x] Two identical polls create one card and leave its bytes, mtime, and `updated_at` unchanged on poll two.
- [x] Refresh replaces only the managed Jira source block and preserves local state, routing, text, and delivery
  evidence; unmanaged identifier collisions fail closed.
- [x] Hostile Jira text cannot create Symphony-interpreted headings, markers, frontmatter, fences, dependencies,
  touched-file claims, acceptance criteria, or evidence; oversize/deep input is rejected before rendering.
- [x] Jira transport, pagination, or authorization failure preserves existing cards, records retryable intake
  health, and does not prevent existing local candidates from dispatching.
- [x] The intake transport performs GET requests only and exposes no Jira mutation path, token, email, account ID,
  authorization header, or unsanitized response body in logs or health.
- [x] Canonical/noncanonical duplicate IDs, case collisions, duplicate markers, symlinks, path escapes, late-batch
  collisions, and exhausted CAS retries fail without overwriting a local edit.
- [x] Targeted tests and the relevant Jira, file tracker, orchestrator health, service, and web API regressions pass.

## QA Cases

No browser case belongs to this frontier. Failure visibility is proven through `/api/v1/health`; the visible SPA
banner and shared dashboard/TUI runtime are owned by the managed-surfaces frontier.

## Scope Boundary

No `WORKFLOW.md` activation, credentials, Jira write-back, AIDT worktree creation, merge, Jenkins deployment,
dashboard JavaScript, or TUI changes.
