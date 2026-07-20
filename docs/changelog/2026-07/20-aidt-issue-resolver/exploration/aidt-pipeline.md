# AIDT issue execution pipeline

Date: 2026-07-20
Mode: read-only exploration; no Jira, Git remote, Jenkins, database, deployment, or product-code mutation performed.

## Decision

Use Jira only as a read-only inbox and source-context feed. A local Symphony file card owns the delivery state. Each routed AIDT service change starts from `origin/aidt-prd`, is developed in a service-local ticket worktree, is verified locally, and is promoted to `aidt-dev` through a temporary merge branch. Jenkins deploy and dev QA are separate serialized gates. Any missing, failed, stale, or ambiguous evidence blocks the card.

The current target `WORKFLOW.md` cannot safely run this pipeline yet:

- It has only `Todo -> In Progress -> Verify -> Learn`, creates a worktree of the Symphony repository itself, has blank `qa.boot` fields, and enables generic host-repository auto-merge.
- Symphony supports custom lanes and per-state prompts, but ordering and `blocked_by` are only partially enforced. Prompts alone are not an authorization boundary.
- The AIDT root has experimental `WORKFLOW.<service>.md` files, but they are untracked, share one board, and are one-service profiles. Running several against the shared board risks duplicate dispatch. They are evidence, not a deployable source of truth.

## Evidence and precedence

| Evidence | Finding | Pipeline use |
|---|---|---|
| AIDT `CLAUDE.md` | Do not occupy local `aidt-dev`/`aidt-stg`; compare remote refs; merge from a temporary `merge/A20-...-aidt-{dev|stg}` branch and push `HEAD:<target>` | Overrides legacy direct checkout/merge instructions |
| `aidt-service-promotion/SKILL.md` | Verify before merge, create temporary branch from `origin/<target>`, re-test merged tree, choose Jenkins context explicitly, and prove runtime health/smoke after deploy | Current promotion authority |
| `aidt-service-branch-commit/SKILL.md` | Service-local Git only; feature branch from `origin/aidt-prd`; backend `{feat|fix}/A20-*`, frontend `csk-{feat|fix}/A20-*` | Source branch contract |
| AIDT `CONTEXT-MAP.md`, service `CONTEXT.md`, and `aidt-project-map/SKILL.md` | AIDT is a polyrepo-style service set with cross-service domain dependencies | Routing must be evidence-based and may be multi-service |
| `jira-resolve/SKILL.md` and current phase references | TDD, review, local QA, dev merge, deploy, and dev E2E intent; 90% confidence model | Reuse the evidence model, not unsafe legacy fall-throughs |
| Target `WORKFLOW.md`, `docs/symphony-prompts/file/**`, and `scripts/symphony-setup-worktree.sh` | Durable per-turn evidence, fresh stage prompts, max three rewinds, review/security/QA records, merge proof | Reuse for local card/evidence lifecycle |
| Symphony monorepo template and customization guide | One service per template; arbitrary lanes and per-state concurrency are supported | A dynamic AIDT router is missing and must be built |
| `src/symphony/trackers/jira.py` | Candidate JQL filters project/status only; requested fields omit assignee, parent, components, and comments; adapter also exposes Jira writes | Direct Jira tracker mode is unsafe for assigned-only intake |
| AIDT `qa-aidt/` and `aidt-qa-playwright/SKILL.md` | Serial Chromium, 120-second timeout, popup entry, console/page/API diagnostics, retained failure artifacts | Dev web QA baseline |

Read-only current-state observations strengthen the isolation requirement: the AIDT root is heavily dirty, many service worktrees already exist, and the main `aidt-lms-api` directory currently occupies `aidt-stg`. Automation must preserve that user state and must not depend on checking out a protected branch in a main service directory.

## Service routing contract

Routing is a gate, not a keyword guess.

1. Import Jira key, type, summary, body, status, priority, labels, component, assignee, links, parent, and parent requirements/comments needed to understand the subtask.
2. Prefer explicit component/service metadata. Validate it against the checked-out AIDT service catalog and directory existence.
3. Use `CONTEXT-MAP.md` and the selected service's `CONTEXT.md` to verify domain ownership and cross-service consumers.
4. Use keywords only as supporting evidence: LMS/assignment/class generally maps to `aidt-lms-api`; viewer/textbook/render/AI-learning content to `aidt-viewer-api`; author/content management to `aidt-lcms-api`; operator/admin to `aidt-bo-api`; UI names to the matching `*-web`; batch, SSO, SSE, and WebSocket to their named services.
5. Inspect current code/API ownership before freezing the route. Record `service`, `service_dir`, `route_evidence`, `issue_type`, `branch_prefix`, and `confidence` on the local card.
6. If two services own changes, create one coordinator card plus one independently verifiable service child per repository. Do not place unrelated repositories in one worktree.
7. Missing or conflicting route evidence, an absent service checkout, or confidence below 90% moves the card to an approval/blocking state. Never default to `lms-api`.

The A20-1188 sample cannot be routed from its empty body alone. Its parent context is mandatory. Existing local A20-1186 evidence points toward viewer work, but that is not yet a general routing rule.

## Required stage sequence

| Stage | Required exit evidence | Failure route |
|---|---|---|
| Intake | Exact assignee and actionable-status query; returned-assignee post-check; parent-aware immutable source snapshot; no Jira write | Retryable Intake Failure |
| Route | One validated service or explicit child-card split; domain/code anchors; branch type; confidence | Human Review / Blocked |
| Plan | Small surgical plan, affected files, side effects, one proof per acceptance criterion, independent review | Re-plan; after three low-confidence passes, Human Review |
| Plan Approval | Durable approval bound to issue revision and plan hash | Wait; infrastructure approval does not approve future issue plans |
| Worktree | Feature worktree from recorded `origin/aidt-prd` SHA; no protected branch newly occupied; clean scope manifest | Blocked |
| Build | Reproduction or RED test, minimal implementation, focused GREEN, service build/typecheck | Build rewind |
| Review | Fresh-context diff review; security, compatibility, data/API side effects; no unresolved medium-or-higher finding | Build rewind |
| Local QA | Exact functional behavior, full relevant tests, runtime/API/browser proof, logs and side-effect checks | Build rewind or Environment Block |
| Commit | Intended files only; service-local commit(s); source SHA and clean status | Blocked |
| Dev Merge | Fresh `origin/aidt-dev`; temporary merge branch; `--no-ff`; post-merge tests; non-force remote push; remote SHA proof | Merge Blocked |
| Dev Deploy | Exact discovered job/parameters; one trigger; correlated run ID; Jenkins success; deployed SHA proof | Deploy Failed / Unknown, never retry blindly |
| Dev QA | Service-appropriate health, workload, API/UI smoke, issue-specific E2E, console/network/log review, side-effect evidence | Dev QA Failed |
| Complete | Acceptance scorecard all pass; evidence freshness matches deployed SHA; sanitized report; optional approved Jira write-back | Human Review / Blocked |

Recommended Symphony lanes are `Intake`, `Route`, `Plan`, `Plan Approval`, `Build`, `Review`, `Local QA`, `Merge`, `Deploy`, `Dev QA`, and `Learn`, with terminal `Done`, `Human Review`, `Blocked`, and `Cancelled`. Set merge/deploy/dev-QA concurrency to one per service/environment. Add code-level transition validation; per-stage prompts remain the worker instructions, not the gate implementation. Disable the target's generic `auto_merge_on_done` for this AIDT profile.

## Safe branch, merge, and cleanup sequence

All Git commands run inside the routed service repository or an attached worktree. The commands below are a specification, not commands run during exploration.

1. Fetch and freeze refs: `git -C <service> fetch origin aidt-prd aidt-dev`; record both SHAs.
2. Reject a protected source name and reject an already-occupied ticket branch. On resume, require the stored branch/base SHA and do not reset, recreate, or rebase automatically.
3. Create the source worktree from `origin/aidt-prd` with no upstream tracking:
   `git -C <service> worktree add -b <feat-or-fix/A20-key> <ticket-worktree> --no-track origin/aidt-prd`.
4. TDD, review, local QA, and commit in the ticket worktree. Confirm the diff is scoped and the worktree is clean. Preserve the exact source SHA.
5. Serialize promotion. Re-fetch `origin/aidt-dev`; if its SHA changed since preflight, re-run merge preflight against the new SHA.
6. Create a separate merge worktree/branch from the remote target, for example `merge/A20-1234-<service>-aidt-dev`. Never checkout local `aidt-dev` in the service directory.
7. In the merge worktree, merge the named source branch with `--no-ff`, save conflict evidence, and re-run relevant tests/build. A conflict or test failure blocks promotion.
8. Push with a normal non-force update: `git -C <merge-worktree> push origin HEAD:aidt-dev`. A concurrent update must reject the push; fetch/rebuild/retest rather than force.
9. Verify the remote target SHA and bind it to the merge evidence. Deployment may start only from that SHA.
10. After final dev QA, remove only the worktrees created by this ticket and prune only their known registrations. Do not delete branches or clean unrelated/prunable worktrees automatically. Re-run `worktree list --porcelain` for `aidt-lms-api` and `aidt-viewer-api` and prove no new `aidt-dev`/`aidt-stg` occupancy remains.

## Jenkins discovery and deployment contract

The checked-in guidance conflicts: the current `jk` skill and the legacy Phase 10 table disagree for at least `datactl-api`, and the legacy table extrapolates jobs not confirmed by the current `jk` mapping. Job names must be discovered, not generated from a pattern.

Read-only discovery sources, in order:

1. `jk --version`, `jk context ls`, and `jk auth status` without printing credential material.
2. `jk -c dev search --job-glob '*<service-short>*' --json`.
3. `jk -c dev job view <job>` and `jk -c dev run params <job>`.
4. Recent successful runs via `jk -c dev run ls <job> --limit 5 --json`, checking branch/environment/namespace parameters and, when available, deployed commit metadata.
5. Service-owned Jenkinsfile/deployment documentation if present. Store the discovered mapping as reviewed configuration, not an agent guess.

Mutation gate:

- Explicitly select/verify the dev context and capture the current queue/latest run before triggering.
- Trigger exactly once with the reviewed parameters. Never use `--follow` or `--wait` in this environment.
- A slow response or timeout is an unknown result, not permission to retry. Correlate a new queued/running build by job, parameters, trigger time, and run number.
- Poll with `jk run ls`/`jk run view`; capture a bounded log snapshot with secrets redacted. Jenkins `SUCCESS` is necessary but insufficient: prove the deployed merge SHA, workload readiness, health, and smoke behavior.

## Local and dev QA requirements

Local QA is service-specific and must test the changed behavior, not merely compilation, HTTP 200, or expected 401/403:

- Backend API: focused unit/integration tests, full relevant Gradle tests, build, local/audit boot when a supported recipe exists, authenticated request, response-body/data assertions, and error/log review.
- MyBatis: read-only query/`EXPLAIN` and returned-data validation before commit; no DML against shared environments. Unavailable DB proof requires approval and remains `Not proven`.
- Web: typecheck/unit/build plus Playwright on the declared local path; console, page errors, AIDT 4xx/5xx, network, and persistence/side-effect checks.
- Batch/SSE/WebSocket: service-specific job/protocol verification; “browser not applicable” must lead to an alternate proof, not a skipped gate.

Dev QA runs only after Jenkins success and deployed-SHA confirmation:

- Use the maintained `qa-aidt` fixtures for LMS/viewer web flows: serial Chromium, 120-second test timeout, popup capture on entry, stable role/locator helpers, and retained trace/screenshot/video on failure.
- Run the issue-specific scenario plus the smallest relevant smoke/regression set. Assert screen behavior against the underlying API/data contract where applicable.
- Capture console errors, uncaught page errors, AIDT API failures, server/deploy logs, workload/health state, URLs, run ID, and deployed SHA.
- Use environment-provided QA identities. Do not record names, IDs, tokens, JWTs, cookies, or credentials in cards, logs, screenshots, commands, or git.
- Prefer mutation-safe observation. If the acceptance criterion requires writes, use a pre-approved disposable fixture and documented cleanup; otherwise block rather than mutate shared dev data.
- A timeout, inaccessible environment, missing browser dependency, missing account, skipped test, or unexplained warning cannot advance to Done.

## Approval and confidence gates

- The 2026-07-20 infrastructure plan is pre-approved. It does not approve future Jira ticket plans, Git pushes, Jenkins runs, dev-data mutation, or Jira write-back.
- Effective safe policy until the operator chooses otherwise: every live issue requires plan approval before product-code edits. Any plan/root-cause/route below 90% is re-checked with fresh context up to three times, then routed to Human Review.
- Never use the legacy `FORCE_PROCEED` behavior. Critical cases always require human approval: ten-or-more-file refactors, schema/data migrations, contradictory requirements, security/auth changes, cross-service contract changes, or unavailable functional proof.
- Merge is authorized only by fresh local evidence. Deploy is authorized only by the exact remote merge SHA. Completion is authorized only by dev QA bound to the Jenkins run and SHA.

## Exact blockers and unknowns

1. Unattended Atlassian authentication and the canonical operator account ID are absent; live assigned-only intake is not proven.
2. The exact A20 actionable-status allowlist and parent/comment permissions are not verified through the API.
3. Current Jira adapter data does not include assignee, parent, component, or comments and offers write methods; the read-only parent-aware mirror is not implemented.
4. Future issue-plan approval policy is unresolved. Mandatory approval is the safe default.
5. A single-dashboard dynamic service-worktree provisioner does not exist. The generic target script provisions the Symphony repo; the untracked AIDT profiles are one-service/shared-board experiments.
6. AIDT root/service worktree state is crowded and dirty; `aidt-lms-api` already occupies a protected branch. Automation must preserve it and cannot rely on protected local checkouts.
7. Service-local boot/health/QA recipes are incomplete. Target `WORKFLOW.md` has blank boot fields; the experimental LMS profile explicitly substitutes unit/build checks for runtime QA.
8. Jenkins guidance contains a committed DEV credential value in local skill/reference files. The value is intentionally omitted here. It must be revoked/rotated and removed before unattended deployment is enabled.
9. Jenkins job mappings conflict across local sources; exact job, parameter schema, permissions, queue semantics, and deployed-SHA signal are unverified live.
10. Kubernetes namespace/workload checks and health/smoke endpoints are not mapped per service.
11. Dev QA is well documented for LMS/viewer browser flows but not for BO, LCMS, datactl, batch, SSO, SSE, or WebSocket issue types.
12. Safe dev-write fixtures and cleanup rules are unspecified; read-only QA cannot prove acceptance criteria that require mutation.
13. Jira completion policy is unresolved: no write-back, sanitized comment only, or comment plus status transition.
14. Rollback/recovery policy after a successful deploy followed by failed dev QA is unspecified.
15. Existing untracked AIDT workflow files are not durable configuration and include backend/model assumptions that require `symphony doctor` validation before reuse.

## Suggested frontier ticket boundaries

Keep each ticket to one contract and one independently runnable proof:

1. `001-safe-jira-inbox` — keep the existing assigned-only, parent-aware, read-only, idempotent mirror ticket.
2. `002-aidt-routing-contract` — service catalog, component/domain/code evidence, multi-service child-card split, fail-closed confidence.
3. `003-aidt-worktree-provisioner` — service-local `origin/aidt-prd` worktrees, resume identity, scope manifest, protected-branch invariant tests.
4. `004-delivery-stage-enforcement` — custom lanes, evidence schema/freshness, transition validator, approval/confidence gates, per-stage serialization.
5. `005-local-qa-adapters` — first LMS/viewer backend+web recipes; add one child ticket per additional service family rather than a generic mega-adapter.
6. `006-dev-merge-promotion` — temporary `origin/aidt-dev` merge branch, `--no-ff`, post-merge tests, non-force push race and recovery tests.
7. `007-jenkins-dev-deploy-gate` — secret-free reviewed job map, single-trigger idempotency, timeout correlation, run/SHA evidence.
8. `008-dev-qa-and-completion` — service-specific runtime/health/E2E proof, side-effect policy, failure/rollback state, optional sanitized Jira write-back.
9. `009-managed-resolver-surfaces` — dedicated-port service plus dashboard/TUI parity for the same durable local card state.
10. `010-integrated-fake-e2e` — fake Jira + temporary Git services + fake Jenkins/browser harness through Dev QA, including restart and duplicate prevention.
11. `011-live-a20-dev-e2e` — credentialed read-only intake and one explicitly approved non-production A20 scenario through real `aidt-dev`; blocked by credentials, job discovery, and QA fixture approval.

Do not combine tickets 003, 006, and 007: worktree creation, protected-branch promotion, and external deployment have different failure and authorization boundaries.
