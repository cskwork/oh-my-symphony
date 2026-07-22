# GOAL - AIDT Jira Issue Resolver

Single source of "done" for the broad GREENFIELD objective. Delivery is split into
vertical frontier runs linked from `wayfinder/map.md`.

## Original Request

> setup always running /Users/chaeseong-gug/Documents/PARA/Project/Git/symphony-multi-agent dashboard html tui. that will take jira tickets that come in my name for example https://dong-a.atlassian.net/browse/A20-1188 check its contents (주기적으로) and if needed implementation utilize supergoal method to implement but using oh-my-symphony process of codex agent will do work in worktree finish implement verify review then qa locally and if all works without side effects merge and deploy to aidt-dev jenkins then once deployed do final qa. in dev. stop condition is when this is fully implemented and a e2e test is done accept the plan made. this is a goal . a dedicated port used to host the dashboard for symphony which will the main orchestrator and issue resolver

## Spec

Build an always-running, local-first Symphony profile that periodically imports actionable A20 Jira
issues assigned to the operator, preserves the Jira issue and parent context, and drives each imported
issue through an evidence-gated Codex workflow in isolated AIDT service worktrees. The internal board
owns the detailed Plan, Build, Verify, Local QA, Merge, Deploy, and Dev QA stages because A20 Jira's
workflow states do not represent those stages. The HTML dashboard and TUI expose the same local state on
a dedicated port. A merge or Jenkins deployment is permitted only after exact tests, review, local QA,
and side-effect checks pass. Secrets remain in operator-provided environment variables.

Current Jira sample evidence (2026-07-20): A20-1188 is a Medium-priority backend subtask assigned to
Chae Sung kuk in `백로그`; its own description is empty, so import must include parent A20-1186. The
parent requires completion-state data for each AI-learning module and first-incomplete-module routing.

User-directed supervised setting: ticket 009 must give the supervised resolver worker environment the exact
non-secret value `EXAMBANK_TWIN_IMAGE_JUDGE_ENABLED=true`. Ticket 009 owns activation and health proof; the current
Frontier 003 worktree frontier must not activate this setting or start the managed resolver.

## Success Criteria

- [ ] Assigned A20 Jira issues are imported periodically without importing another assignee's work.
- [ ] Imported cards contain the issue body, parent requirements, links, status, priority, and routing data.
- [ ] Codex workers use isolated AIDT service worktrees and the supergoal evidence contract.
- [ ] The board enforces Plan, Build, Verify/Review, Local QA, Merge, Jenkins Deploy, and Dev QA gates.
- [ ] Merge and deployment stop on failed, missing, stale, or side-effect evidence.
- [ ] A managed background service hosts the HTML dashboard on a dedicated verified port and supports TUI use.
- [ ] The ticket 009 supervised worker runs with `EXAMBANK_TWIN_IMAGE_JUDGE_ENABLED=true`, and health/startup proof
  records only its enabled state without dumping the surrounding environment.
- [ ] A real end-to-end scenario proves Jira intake through final dev QA, with secrets excluded from git.

## QA Cases (web apps only)

- [ ] Dashboard shows a mirrored assigned Jira card and its current workflow stage.
- [ ] Dashboard health, refresh, issue detail, pause/resume, and failure state remain functional.
- [ ] TUI renders the same card and runtime state as the HTML dashboard.

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | A20 Jira stages do not match the required internal delivery stages. | Mirror assigned Jira issues into the file board; keep delivery stages local. | Integration E2E |
| d2 | ask-user | open | No Atlassian CLI/API token is configured for unattended polling. | Operator must authenticate `acli` or provide `JIRA_EMAIL` and `JIRA_API_TOKEN` outside git. | Live Jira preflight |
| d3 | ask-user | open | Future Jira ticket plans are not known at setup time. | The present request pre-approves this infrastructure plan; Jira issue plans still obey confidence and repository safety gates. | First live issue |
