# Production pipeline

Symphony's default workflow now has four active agent lanes:

```text
Todo -> In Progress -> Verify -> Document -> Done
          ^              |        |
          |              |        +-> critical/manual intervention -> Human Review
          +--------------+
             Verify or Document findings rewind here
```

`WORKFLOW.md` remains the orchestration manifest. Worker instructions live in
`docs/symphony-prompts/`, and the orchestrator assembles the shared base prompt
plus the current state's stage prompt for each fresh turn.

| State | Owner of this turn | Required output |
| --- | --- | --- |
| Todo | triage/router | `## Triage`, then route actionable work to `In Progress` or explain `Blocked` |
| In Progress | implementer | `## Plan`, `## Acceptance Tests`, `## Done Signals`, `## Implementation`, `## Self-Critique`, plus goal/before/after proof notes under `docs/<ID>/work/` |
| Verify | reviewer + QA + merge preflight | `## Security Audit`, clean `## Review` or `## Review Findings`, `## QA Evidence`, `## AC Scorecard`, `## Merge Status`, plus not-covered and rerun guidance |
| Document | distiller | `docs/llm-wiki/` updates, `## Wiki Updates`, `## As-Is -> To-Be Report`; may rewind real defects to `In Progress` |
| Human Review | operator | manual intervention or explicit review before `Done` |
| Done | reporter | `## As-Is -> To-Be Report` with goal, evidence, residual risk, and how to re-run |
| Blocked | agent or operator | `## Blocker` describing the missing input or failed gate |

## The intent gate

A request may enter the board from the web chat. The chat agent never files
the request itself: it proposes an intent card (Problem, Evidence, Success
criteria, Out of scope, Constraints, Open questions, Track), the operator
approves it once, and the server files the request ticket and records
`.sdlc/work/<slug>/intent.md`. On this default board the ticket lands in
`Todo`; on the deep preset it lands in `Intake`, whose prompt consumes the
intent instead of re-asking (`docs/symphony-prompts/file/deep/intake.md`).
The ticket description carries the whole intent, so every lane treats it as
the scope of record and never reopens the ask with the operator; `Human
Review` stays reserved for a real blocker.

Every proposal is scanned with the sdlc-kit trip-wire heuristics
(`tools/tripwire.sh`: migrations, data deletion, public API, security paths,
infra/config). Hits are shown on the card and recorded as `## Trip-wires` on
the ticket and in `intent.md`; they never block the operator, but a hit
downgrades a `micro` track to `full`, and the deep Review lane treats each
hit as a mandatory objection candidate.

## Human decisions and automatic work

Chat intent approval is the normal human entry gate. Direct ticket creation is already an instruction to work. After that, workers implement, verify and document; the orchestrator checks dependencies, evidence and budgets. There is no mandatory Human Review stop on the normal Document-to-Done path. Operators decide explicit review holds, missing inputs or credentials, and budget extensions. Mechanical evidence checks do not replace substantive code review or execution evidence.

The maintained source workflow enables local merge and target push to `dev`, with empty `fallback_kinds`. These are workflow settings, not a universal permission to publish: project operators choose the target and push policy before dispatch. Release tags and public deployments remain separate actions. The protected source checkout itself is not a worker project.

## Taking over a blocked recovery manually

A FIX ticket is a real dependency of its source. Moving the source back to an active lane does not resolve that dependency. Prefer completing the FIX with current `## Fix Resolution` evidence on both the FIX and its source; the recovery checks then decide whether the source may reopen.

For a deliberate manual takeover, first set `agent.auto_recover_blocked: false` to stop opening new automatic FIX tickets. This setting does not remove existing `blocked_by` edges and does not disable the evidence checks on completed FIX tickets. Cancel or archive an unused FIX, review and remove only its dependency edge from the source while preserving other blockers, then move the source to the intended active lane. Do not mark an unresolved FIX Done merely to release its source. A restart alone does not resolve dependencies.

Open a ticket's **Execution status** section to inspect the last scheduler decision and follow links to unresolved dependencies. Completed dependencies are omitted from that list; a missing card is identified without a broken navigation button. The existing Request view uses the same scheduler projection. A ticket edited after evaluation is marked stale, and missing scheduler data never becomes a ready decision. **Reload execution status** only rereads saved information; it does not trigger dispatch. Wait for the next scheduler pass when a fresh evaluation is needed.

**Execution limits** distinguishes a disabled cap, unknown usage, and a known remaining count. Turns include the current turn, tokens use the current lane's guard, and the initial Done completion is excluded from reopen usage. A current reopened run consumes one reopen. Zero remaining retries or rewinds means there is no further allowance under that cap; it is not a command to stop the current worker. Reopen history is unknown when unavailable or truncated. These rows are a read-only explanation of guard counters, not a second scheduler or a way to approve completion. Review the latest failure and existing run controls before resuming work.


## Deep preset: request and dependent tickets

```text
Request: Intake -> Research -> Plan -> Review -> Done
                                             | PASS unlocks
Separate tickets: Build slices -> QA -> Verify -> Document
```

Plan creates the dependent task graph. The request ends after Review PASS; it does not continue through Build, QA, Verify and Document as the same ticket. A micro request may skip Research. QA or Verify can reopen a completed Build slice, subject to reopen limits. Each task owns its lane evidence. An application release verifier/finalizer adds the separate host-owned release contract when explicitly configured.

## Why four stages

The old eight-stage flow spread one delivery story across too many agent
turns. The new shape keeps the important gates while reducing context loss:

- Todo only decides whether a ticket is actionable.
- In Progress owns planning and implementation together, so the worker does
  not hand its own plan to a different fresh context before writing code.
- Verify keeps review, execution, acceptance scorecard, and merge preflight in
  one compulsory lane; the merge itself happens once, at Done.
- Document remains a separate write-back lane because durable project knowledge is
  different from verification evidence.

## In Progress contract

In Progress must leave enough evidence for a fresh verifier to audit the work:

- `## Plan` - user goal, before state, after target, rejected alternatives, and ordered implementation steps.
- `## Acceptance Tests` - one observable proof per acceptance criterion.
- `## Done Signals` - exact state the verifier should see, including anything still `Not proven`.
- `## Implementation` - changed files, behavior, and why this approach was chosen.
- `## Self-Critique` - known limits, not-covered areas, and suspicious paths for Verify.
- `docs/<ID>/work/` - at least one durable work artefact when a docs root is
  available.

If Verify or Document rewinds a ticket to In Progress, the worker reads the most
recent `## Review Findings`, `## QA Failure`, or `## Document Defect` first and
fixes that scope before opening new work.

## Verify contract

Verify is never skipped. For trivial non-runtime changes the QA section may be
short, but the lane still records what was checked and why runtime coverage was
not needed.

Verify must produce:

- `## Security Audit` - pass/fail rows for auth, input validation, data
  exposure, destructive actions, and secrets.
- `## Review` for a clean diff, or `## Review Findings` with severity and
  cited paths for blocking issues.
- `## QA Evidence` - commands, exit codes, and evidence paths.
- `## QA Evidence` also names what worked, what failed, what is not covered,
  and how to re-run the proof.
- `## AC Scorecard` - acceptance criteria with signal, source, pass/fail
  status, and evidence path.
- `## Merge Status` - target branch, feature branch, and the
  `git merge-tree --write-tree` preflight result.

## One merge per ticket

There is exactly one merge, and the orchestrator makes it:

> **Verify proves, Document documents, the orchestrator merges.** Verify runs a
> `git merge-tree --write-tree` preflight and records the result; when the
> ticket reaches `Done`, `agent.auto_merge_on_done` creates the single
> `--no-ff` merge commit on the target branch. With the default
> `agent.auto_merge_push_target: true`, it then pushes and verifies the
> target's configured upstream. Set that option to `false` for a local-only
> run: all local safety checks and the `--no-ff` merge remain active, while
> neither `git push` nor `git ls-remote` is called (including no-op retries).
> Normal ticket final-history snapshotting follows the same setting, so a
> local-only ticket cannot publish its feature branch before the target merge;
> release-evidence snapshots remain local audit records.

This ordering is also the wiki write-back rule. The wiki is a host-repo,
tracked path (`<workflow-dir>/docs/llm-wiki/`, configurable via `wiki.root`).
Workers write it **inside their worktree** at the same relative path; the
per-turn wip commit carries it and the Done merge delivers it. The wiki is
never symlinked into the workspace and never written directly to the host
tree. If you do symlink it (to share it across tickets), add that path to
`agent.auto_merge_capture_untracked` so the merge commit still carries it.

A Verify lane that hand-merged produced two merge commits per ticket and landed
code on the target branch *before* Document ran — which is why wiki write-back
used to arrive inconsistently.

Any CRITICAL/HIGH/MEDIUM review issue, failed command, failed AC, or failed
security row rewinds to `In Progress`. Verify should not hide failures by
retrying until the output looks clean.

## Document and Human Review

Document compares the ticket's plan, implementation, verification evidence, and
merge status against what future tickets need to know. It writes durable notes
to `${LLM_WIKI_PATH:-./docs/llm-wiki}/`, appends `## Wiki Updates`, appends
`## As-Is -> To-Be Report`, and moves normal successful work to `Done`.

The operator can skip an idle Document ticket through the TUI or web app. That
action appends `## Document Skipped` and moves the card to `Human Review` without
spawning an agent. Agents must not simulate this skip themselves, and should
use `Human Review` only when a recorded critical/manual intervention remains.

## Stage prompts

The example workflows use a stage-specific prompt manifest:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Verify: ./docs/symphony-prompts/file/stages/verify.md
    Document: ./docs/symphony-prompts/file/stages/document.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

Use `docs/symphony-prompts/file/` for the Markdown-file Kanban tracker and
`docs/symphony-prompts/linear/` for Linear. Customize those files directly
when a board needs different agent behavior.

## Runtime config

The supported production active states are:

```yaml
tracker:
  active_states: [Todo, "In Progress", Verify, Document]
  terminal_states: ["Human Review", Done, Blocked, Archive]
```

The orchestrator dispatches a worker for any ticket whose state is active.
Terminal states stop dispatch. `Human Review` is terminal because a human must
resolve or confirm an explicit intervention before `Done`.

`agent.stage_contracts` (default `auto`) is the mechanical evidence floor
behind the prompts. On the default lanes it checks the sections above at
every forward transition, including the move into `Done`; on the deep preset
it checks each lane's vault file and verdict line. A miss appends
`## Contract Failure` and rewinds the ticket to the producing lane. Blocked,
Cancelled, and Human Review are never contract-gated.

Two budgets bound the loops. `agent.max_attempts` caps rewinds inside one
run (Verify or Document back to In Progress). `agent.max_reopens` caps how
often a ticket that already reached Done is dispatched again — the deep
preset's Verify RED / QA BLOCKED reopen a merged Build slice as a fresh run,
which `max_attempts` never sees. Past the cap the ticket gets a
`## Reopen Budget` note and parks in Blocked; an operator appends
`## Reopen Approved` to buy one more cycle.

The learning loop runs on the same counters. Every contract failure,
reopen hold, and backend fallback is a `gate` event in `.symphony/stats.jsonl`;
rewinds are derived from the lane order. The Stats page and TUI show them per
lane with the recurring contract misses, and the Document lane's prompt
receives the same summary as `{{ board_health }}` so repeat failures turn
into `## Learnings` and wiki entries.

The web board opens on active agent lanes. `Human Review`, `Done`, `Blocked`,
and `Archive` stay visible in the compact **Review and parked** group until
you switch to `All`.

`max_concurrent_agents_by_state` can throttle expensive lanes, for example one
Verify worker at a time when browser QA or integration tests contend for shared
ports.

## Adopting the pipeline

1. Copy `WORKFLOW.file.example.md` (file tracker) or `WORKFLOW.example.md`
   (Linear) to `WORKFLOW.md` and customize.
2. Confirm `tracker.active_states` contains exactly the active lanes you want.
   For the default production flow, keep `Todo`, `In Progress`, `Verify`, and
   `Document`.
3. Confirm the `prompts:` block points at the matching prompt flavor.
4. Confirm hooks land each agent in a workspace where tests, APIs, and browser
   checks can actually run.
5. Decide whether `docs/llm-wiki/` lives in this repo or a sibling docs repo.
6. Run `symphony doctor ./WORKFLOW.md` before launch.

## Per-ticket artefact root

Every durable artefact for a ticket should live under:

```text
docs/<TICKET-ID>/
  reproduce/   bug reproductions, when relevant
  work/        implementation notes, generated docs, screenshots, fixtures
  verify/      review notes, diff evidence, merge proof
  qa/          command output, traces, HAR files, screenshots
```

Document is the only default lane that writes outside the ticket root; it updates
`${LLM_WIKI_PATH:-./docs/llm-wiki}/`.

## Reference ticket

A complete worked example lives at [`docs/PIPELINE-DEMO.md`](./PIPELINE-DEMO.md).
It includes every section a finished pipeline ticket should carry:
`## Plan`, `## Acceptance Tests`, `## Done Signals`, `## Implementation`,
`## Self-Critique`, `## Security Audit`, `## Review`, `## QA Evidence`,
`## AC Scorecard`, `## Merge Status`, `## Wiki Updates`, and
`## As-Is -> To-Be Report`.

Evidence-first stage rules adapt ideas from cskwork/backend-dev-skills (MIT).
