# Production pipeline

Symphony's default workflow now has four active agent lanes:

```text
Todo -> In Progress -> Verify -> Learn -> Human Review -> Done
          ^              |        |
          |              |        +-> operator may skip Learn to Human Review
          +--------------+
             Verify or Learn findings rewind here
```

`WORKFLOW.md` remains the orchestration manifest. Worker instructions live in
`docs/symphony-prompts/`, and the orchestrator assembles the shared base prompt
plus the current state's stage prompt for each fresh turn.

| State | Owner of this turn | Required output |
| --- | --- | --- |
| Todo | triage/router | `## Triage`, then route actionable work to `In Progress` or explain `Blocked` |
| In Progress | implementer | `## Plan`, `## Acceptance Tests`, `## Done Signals`, `## Implementation`, `## Self-Critique`, plus goal/before/after proof notes under `docs/<ID>/work/` |
| Verify | reviewer + QA + merge gate | `## Security Audit`, clean `## Review` or `## Review Findings`, `## QA Evidence`, `## AC Scorecard`, `## Merge Status`, plus not-covered and rerun guidance |
| Learn | distiller | `docs/llm-wiki/` updates, `## Wiki Updates`, `## Human Review`; may rewind real defects to `In Progress` |
| Human Review | operator | human approval before `Done` |
| Done | reporter | `## As-Is -> To-Be Report` with goal, evidence, residual risk, and how to re-run |
| Blocked | agent or operator | `## Blocker` describing the missing input or failed gate |

## Why four stages

The old eight-stage flow spread one delivery story across too many agent
turns. The new shape keeps the important gates while reducing context loss:

- Todo only decides whether a ticket is actionable.
- In Progress owns planning and implementation together, so the worker does
  not hand its own plan to a different fresh context before writing code.
- Verify keeps review, execution, acceptance scorecard, and merge proof in one
  compulsory lane.
- Learn remains a separate write-back lane because durable project knowledge is
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

If Verify or Learn rewinds a ticket to In Progress, the worker reads the most
recent `## Review Findings`, `## QA Failure`, or `## Learn Defect` first and
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
- `## Merge Status` - target branch, merge or PR proof, and final commit/ref.

Any CRITICAL/HIGH/MEDIUM review issue, failed command, failed AC, or failed
security row rewinds to `In Progress`. Verify should not hide failures by
retrying until the output looks clean.

## Learn and Human Review

Learn compares the ticket's plan, implementation, verification evidence, and
merge status against what future tickets need to know. It writes durable notes
to `${LLM_WIKI_PATH:-./docs/llm-wiki}/`, appends `## Wiki Updates`, then
appends `## Human Review` and moves the ticket to `Human Review`.

The operator can skip an idle Learn ticket through the TUI or web app. That
action appends `## Learn Skipped` and moves the card to `Human Review` without
spawning an agent. Agents must not simulate this skip themselves.

## Stage prompts

The example workflows use a stage-specific prompt manifest:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Verify: ./docs/symphony-prompts/file/stages/verify.md
    Learn: ./docs/symphony-prompts/file/stages/learn.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

Use `docs/symphony-prompts/file/` for the Markdown-file Kanban tracker and
`docs/symphony-prompts/linear/` for Linear. Customize those files directly
when a board needs different agent behavior.

## Runtime config

The supported production active states are:

```yaml
tracker:
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Blocked, Archive]
```

The orchestrator dispatches a worker for any ticket whose state is active.
Terminal states stop dispatch. `Human Review` is terminal because a human must
confirm before `Done`.

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
   `Learn`.
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

Learn is the only default lane that writes outside the ticket root; it updates
`${LLM_WIKI_PATH:-./docs/llm-wiki}/`.

## Reference ticket

A complete worked example lives at [`docs/PIPELINE-DEMO.md`](./PIPELINE-DEMO.md).
It includes every section a finished pipeline ticket should carry:
`## Plan`, `## Acceptance Tests`, `## Done Signals`, `## Implementation`,
`## Self-Critique`, `## Security Audit`, `## Review`, `## QA Evidence`,
`## AC Scorecard`, `## Merge Status`, `## Wiki Updates`, `## Human Review`,
and `## As-Is -> To-Be Report`.

Evidence-first stage rules adapt ideas from cskwork/backend-dev-skills (MIT).
