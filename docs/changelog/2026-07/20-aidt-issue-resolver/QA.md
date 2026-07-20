# QA - AIDT Jira Issue Resolver

- Verdict: PARTIAL

## Before

- [x] `WORKFLOW.md` uses `tracker.kind: file`; no assigned Jira intake is configured.
- [x] Jira adapter JQL scopes project and states but not assignee, so using it directly could ingest other owners' tickets.
- [x] A20-1188 was read in the authenticated Jira UI: assigned to Chae Sung kuk, Medium, `백로그`, empty description.
- [x] A20-1186 contains the load-bearing requirements and identifies A20-1188 as the backend subtask.
- [x] `acli auth status` and `jira me` both report missing unattended API authentication.

## Results

- [x] Frontier 001 safe Jira inbox passed its nested literal commit gate after two Build/Verify iterations.
- [x] Frontiers 002/002a immutable AIDT routing and public output closure passed independent final verification.
- [ ] Worktrees, stage gates, QA, merge/deploy, managed surfaces, and integrated/live E2E remain.

Backward-trace: clean

## Commands

| Command | Source | Proves |
|---|---|---|
| `pytest` | frozen_repo | Symphony unit and integration behavior |
| `symphony doctor <workflow>` | frozen_repo | runtime configuration and port/CLI prerequisites |
| `python scripts/smoke_web_api.py --base-url <url>` | frozen_repo | dashboard/API runtime |
| browser and TUI E2E harnesses | frozen_repo | operator surfaces |

## QA

Tool: playwright-cli
UI-tier: Functional (reuse existing Symphony dashboard and TUI)
DB: SQLite runtime ledger only; no direct mutation during QA

## Reproduction Fidelity

- Fidelity level: not-reproduced
- Residual risk from data gap: unattended Atlassian API credentials are absent.
- Post-deploy confirmation plan: import A20-1188 through the configured read-only inbox, verify parent context,
  then use a non-production test card for the mutating delivery stages before processing a live issue.

## Residual Risk

- Not proven: unattended Jira API access, AIDT local QA boot, Jenkins aidt-dev deployment, final dev E2E.
- Follow-up: close the Frontier Map tickets and provide external credentials at their explicit gates.
