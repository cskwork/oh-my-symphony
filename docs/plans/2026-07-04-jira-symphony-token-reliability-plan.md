# Jira Symphony Token Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` for parallelizable tasks or `superpowers:executing-plans` for sequential execution. Keep the checkbox state in this document current as each step lands.

**Goal:** reduce wasted Symphony turns and repeated prompt input observed in the `jira-symphony` live run while preserving reasoning depth, `Verify`, `Learn`, `Human Review`, stage contracts, and durable evidence.

**Theory:** Symphony models a board as a production line. Tickets are units of work, states are workstations, blockers are upstream dependencies, prompts are the worker's job packet, backend telemetry is the cost meter, and stage contracts are quality gates. The live run spent millions of tokens because the system let expensive work start too early, sent oversized job packets repeatedly, and relied on broad turn limits that were too late to protect the run. The fix should fail cheap before dispatch, compact context at the state boundary, enforce sane loop budgets, and keep evidence requirements strict.

**Architecture:** add safety at four boundaries:

1. **Tracker boundary:** infer explicit dependency blockers from Markdown ticket bodies, then surface them as structured blockers.
2. **Scheduler boundary:** refuse dispatch for any active-state ticket with unresolved blockers, not only `Todo`.
3. **Prompt boundary:** provide state-aware compact issue context while preserving a pointer to the full ticket.
4. **Telemetry and contract boundary:** verify backend token accounting, keep hard token caps opt-in, and make contract failures cheap and specific.

**Tech stack:** Python 3.12, pytest, Symphony file tracker, orchestrator, backend adapters, Markdown ticket files, SQLite run registry, `symphony doctor`, web/TUI state APIs.

**Primary target repo:** `/Users/danny/Documents/PARA/Resource/symphony-multi-agent`

**Evidence repo:** `/Users/danny/Documents/PARA/Resource/jira-symphony`

## Evidence Baseline

Observed live-run facts to preserve as regression evidence:

- `jira-symphony/.symphony/stats.jsonl:32`: `TASK-003` `in progress` used `4,682,674` input tokens and `4,714,286` total tokens.
- `jira-symphony/.symphony/stats.jsonl:34`: `TASK-003` `verify` used `3,891,983` input tokens and `3,921,423` total tokens.
- `jira-symphony/.symphony/stats.jsonl:40`: `TASK-004` `in progress` used `6,071,430` input tokens and `6,118,691` total tokens.
- `jira-symphony/.symphony/stats.jsonl:46`: `TASK-004` `verify` used `5,797,307` input tokens and `5,855,173` total tokens.
- `jira-symphony/.symphony/stats.jsonl:49`: `TASK-004` `learn` used `1,245,437` input tokens and `1,282,185` total tokens.
- `jira-symphony/WORKFLOW.md:53-59`: `after_create` runs `pnpm install --prefer-offline 2>&1 | tail -2 || true` and `pnpm prisma generate 2>&1 | tail -1 || true`, so setup failures can become expensive agent work.
- `jira-symphony/WORKFLOW.md:193-204`: the run used `agent.kind: opencode`, `max_turns: 100`, `max_total_turns: 200`, `max_total_tokens: 100000000`, and per-state caps of `500000000`, effectively disabling protection.
- `jira-symphony/docs/symphony-prompts/file/base.md:5-9`: every turn injects the full `issue.description`.
- `symphony-multi-agent/src/symphony/orchestrator/helpers.py:103-121`: auto-triage rejects only structured `issue.blocked_by`, not prose `## Dependencies`.
- `symphony-multi-agent/src/symphony/orchestrator/core.py:1699-1704`: unresolved blockers are checked only when normalized state is `todo`.
- Prior fixes already landed in `symphony-multi-agent/docs/changelog/changelog-2026-07-04.md`: OpenCode 1.17 text extraction, token accounting from `step_finish.part.tokens`, issue-state parse auto-heal, and project-scoped workspace roots. This plan must not re-solve those; it should build guardrails around them.

## Non-Goals

- Do not lower model quality, reasoning effort, or backend choice as the primary fix.
- Do not bypass `Verify`, `Learn`, or `Human Review`.
- Do not accept vague evidence cells in stage contracts to reduce rewinds.
- Do not set low hard token caps as the default. Reasoning-heavy turns may legitimately use large hidden or thinking-token budgets.
- Do not replace Markdown tickets or file-tracker semantics.
- Do not refactor unrelated orchestrator behavior.
- Do not rewrite the Next.js app in `jira-symphony`; this plan is for Symphony runtime reliability.

## Quality-Preserving Token Policy

Token reduction must come from avoiding unnecessary work and repeated context, not from starving agents that are doing real reasoning.

Default policy:

- Keep existing hard token caps disabled by default: `agent.max_total_tokens: 0` and empty `agent.max_total_tokens_by_state`.
- Use hard token caps only as an operator opt-in after measuring p95 or p99 token usage for that workflow/backend pair.
- Never treat a large total-token number as a defect by itself. A high reasoning or thinking-token turn can be valid if it advances the ticket and passes `Verify`.
- Prefer these savings levers, in order:
  1. prevent downstream dispatch when dependencies are unresolved.
  2. fail setup and contract problems before launching a backend.
  3. compact repeated prompt input by stage.
  4. narrow rewind prompts to the exact failure scope.
  5. expose token telemetry and trends to operators.
- Measure success as fewer unnecessary turns and lower prompt input for comparable `Verify`/`Learn` turns. Do not require low total tokens when hidden reasoning tokens dominate.

## Planned File Changes

Primary implementation files:

- `src/symphony/trackers/file.py`
- `src/symphony/orchestrator/core.py`
- `src/symphony/prompt.py`
- `src/symphony/prompt_context.py` (new)
- `src/symphony/workflow/config.py`
- `src/symphony/workflow/builder.py`
- `src/symphony/orchestrator/contracts.py`
- `src/symphony/cli/doctor.py`
- `src/symphony/workspace.py`
- `docs/symphony-prompts/file/base.md`
- `docs/symphony-prompts/linear/base.md`

Primary test files:

- `tests/test_tracker_file.py`
- `tests/test_orchestrator_dispatch.py`
- `tests/test_workflow.py`
- `tests/test_workflow_pipeline_prompt.py`
- `tests/test_prompt.py`
- `tests/test_prompt_context.py` (new)
- `tests/test_backends.py`
- `tests/test_orchestrator_contracts.py`
- `tests/test_doctor.py`
- `tests/test_workspace.py`
- `tests/test_webapi.py`

Documentation:

- `docs/changelog/changelog-2026-07-04.md`
- `docs/plans/2026-07-04-jira-symphony-token-reliability-plan.md`

## Implementation Sequence

### 1. Dependency-Aware Dispatch and Auto-Triage

**Priority:** P0

**Problem:** tickets with prose dependencies can be moved from `Todo` to `In Progress` and later dispatched even when upstream tickets are not terminal. In the live run, downstream tickets were auto-triaged because `## Dependencies` text was not converted into `issue.blocked_by`.

**Desired behavior:**

- Markdown body dependencies become structured blockers when `blocked_by` frontmatter is absent or incomplete.
- Scheduler eligibility checks blockers in every active state, not just `Todo`.
- Auto-triage refuses tickets with unresolved body dependencies.
- Unknown dependency IDs do not become silent permanent stalls. They remain safety blockers, but surface as operator-visible attention with the unresolved identifiers.
- Retry-pending tickets held by a blocker are visible in `/api/v1/state` and become eligible again when blockers are terminal.
- Terminal blocker states use the workflow terminal-state set already used by `_eligible`.

**Files:**

- Modify `src/symphony/trackers/file.py`.
- Modify `src/symphony/orchestrator/core.py`.
- Extend `tests/test_tracker_file.py`.
- Extend `tests/test_orchestrator_dispatch.py`.
- Extend `tests/test_webapi.py` if issue-detail attention serialization needs coverage beyond orchestrator tests.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Test first:**

- Add a file-tracker test that creates two Markdown tickets:
  - `TASK-004.md`, state `Human Review`.
  - `TASK-005.md`, state `Todo`, body section `## Dependencies` containing `TASK-004`.
- Assert `TASK-005` includes a blocker object with identifier `TASK-004` and state `Human Review`.
- Add a dispatch test where a ticket is already in `In Progress` with blocker state `Verify`; assert `_eligible(issue, cfg)` returns `False`.
- Add an auto-triage test where a `Todo` ticket has acceptance criteria and a `## Dependencies` section; assert `_is_auto_triage_todo_candidate(issue, cfg)` returns `False`.
- Add a tracker/orchestrator test where `## Dependencies` contains `TASK-999` but no matching board ticket; assert the issue is ineligible and `issue_attention(issue)` reports `blocked_dependency` with `TASK-999`.
- Add a retry-pending test where a held continuation has an unresolved blocker; assert it is not dispatched, does not consume a turn, and becomes eligible after the blocker state becomes terminal.

**Implementation notes:**

- Add a helper near the existing blocker parser in `src/symphony/trackers/file.py`:

```python
_DEPENDENCY_HEADING_RE = re.compile(r"^##+\s+Dependencies\s*$", re.IGNORECASE)
_TICKET_ID_RE = re.compile(r"\b[A-Z][A-Z0-9_-]*-\d+\b")
```

- Parse only the dependency section body until the next Markdown heading of the same or higher level.
- Recognize dependency IDs inside bullets, plain lines, or inline text.
- De-duplicate identifiers while preserving first-seen order.
- Merge frontmatter blockers and body blockers. Frontmatter remains authoritative for explicit metadata; body parsing fills missing references.
- Resolve blocker states through the existing board scan path. Do not add filesystem reads in the orchestrator.
- Unknown body dependency IDs should stay in `issue.blocked_by` with `state=None`. The scheduler treats them as unresolved, and `Orchestrator.issue_attention(issue)` returns an attention signal such as:

```python
_attention_signal(
    "blocked_dependency",
    "Blocked dependency",
    "waiting on unresolved dependency: TASK-999",
    "warning",
)
```

- Add the attention check after tracker-error/budget checks and before retry attention so dependency stalls are visible for non-running tickets.
- In `_eligible`, move the blocker check out of the `state == "todo"` branch:

```python
if issue.blocked_by:
    for blocker in issue.blocked_by:
        if not blocker.state or normalize_state(blocker.state) not in terminal:
            return False
```

- Do not change `_is_auto_triage_todo_candidate` if the red test proves body dependencies are already present in `issue.blocked_by` before helper execution. If the red test shows candidate objects can bypass file parsing, add a narrow body-dependency guard there and cite that test in the changelog.

**Verification commands:**

```bash
pytest tests/test_tracker_file.py -k "dependency or blocker"
pytest tests/test_orchestrator_dispatch.py -k "blocker or auto_triage"
pytest tests/test_tracker_file.py tests/test_orchestrator_dispatch.py
```

**Acceptance:**

- A ticket with `## Dependencies\nTASK-004` is not auto-triaged while `TASK-004` is `Todo`, `In Progress`, `Verify`, or `Learn`.
- The same ticket becomes eligible after all blockers are terminal.
- A ticket with typo or cross-board dependency text does not run silently; `/api/v1/state` or issue detail shows the unresolved dependency.
- A retry-pending ticket held by a regressed blocker remains visible and dispatches on a later reconcile after the blocker returns to terminal.
- Existing `blocked_by` frontmatter tests still pass.

### 2. Backend Token Telemetry Audit and Sanity Guards

**Priority:** P0

**Problem:** token metrics are only useful if backend telemetry is trustworthy. The OpenCode 1.17 parser was fixed recently, but this change needs regression tests and visibility before operators use optional budgets.

**Desired behavior:**

- Token accounting tests pin the OpenCode 1.17 `step_finish.part.tokens` schema.
- Per-turn stats avoid double-counting cumulative totals.
- Productive non-empty turns with zero total tokens emit an attention warning.
- Large total-token turns are recorded and trended. They do not warn or block by default because large thinking-token usage can be legitimate.
- Optional token attention thresholds are explicit operator configuration, not built-in low defaults.

**Files:**

- Extend `tests/test_backends.py` or add `tests/test_backend_usage.py` if current file becomes mixed concern.
- Modify the OpenCode backend parser only if the new red test proves a defect remains.
- Modify `src/symphony/orchestrator/core.py` around token application and stats recording.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Test first:**

- Add an OpenCode JSONL fixture with:
  - a `message` or text part that produces non-empty assistant output.
  - one `step_finish` frame with `part.tokens.input`, `part.tokens.output`, and `part.tokens.total`.
  - a second fixture with cache read/write and reasoning fields.
- Assert the backend result exposes exactly the parsed latest turn usage fields.
- Add an orchestrator unit test that records a productive turn with `total == 0`; assert a health or attention record is produced.
- Add an orchestrator unit test that records a single turn above an explicitly configured warning threshold; assert warning only.
- Add an orchestrator unit test that records a large turn with no configured threshold; assert no warning or block is produced.

**Implementation notes:**

- Do not enforce lower hard caps as part of this task. Existing hard caps remain available but default disabled.
- Keep usage semantics backend-local. Avoid global normalization that hides provider differences.
- Add config defaults in `AgentConfig` only if the warning threshold needs configuration. The default must be disabled:

```yaml
agent:
  token_attention_threshold_by_state: {}
```

- Store warnings in the existing health/attention path used by `/api/v1/state` instead of inventing a new status file.
- If a backend reports reasoning or thinking-token fields separately, preserve those fields in the stats path instead of folding them into a generic error condition.

**Verification commands:**

```bash
pytest tests/test_backends.py -k "opencode and token"
pytest tests/test_orchestrator_dispatch.py -k "token or attention"
pytest tests/test_backends.py tests/test_orchestrator_dispatch.py
```

**Acceptance:**

- OpenCode 1.17 text and token extraction are covered by tests.
- A non-empty backend response with zero total tokens is visible to operators.
- A high-token turn is visible to operators through stats, and only creates attention if the workflow explicitly configured a threshold.
- No high-token turn transitions to `Blocked` unless an existing hard cap was explicitly configured by the workflow owner.

### 3. Stage-Aware Prompt Context Compaction

**Priority:** P0

**Problem:** each turn injects the full ticket body. After retries and evidence sections accumulate, `Verify`, `Learn`, and rewind prompts repeatedly resend older context that the worker does not need for the current stage.

**Desired behavior:**

- Prompt context is built from the ticket body by current state.
- The full ticket path remains visible in the prompt for auditability.
- Stage-specific context keeps quality-critical sections and drops stale repeated sections.
- Rewind turns include the latest failure section and original scope, not the entire historical transcript.

**Files:**

- Add `src/symphony/prompt_context.py`.
- Modify `src/symphony/prompt.py`.
- Modify `docs/symphony-prompts/file/base.md`.
- Modify `docs/symphony-prompts/linear/base.md`.
- Extend `tests/test_workflow_pipeline_prompt.py`.
- Extend `tests/test_prompt.py`.
- Add `tests/test_prompt_context.py`.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Context rules:**

- Base prompt scaffolding continues to own:
  - issue identifier
  - title
  - current state
  - labels
  - structured blockers
- The compacted description body must not repeat those scaffold fields.
- The rendered prompt should include `Full ticket: <path>` outside `## Description`, populated from the file tracker path when available.
- The compacted description body always keeps original user-facing scope before the first agent-owned section.
- `In Progress` keeps:
  - original description and acceptance criteria
  - latest `## Triage` if present
  - latest `## Review Findings`, `## QA Failure`, or `## Learn Defect` on rewind
  - latest `## Plan` only when it contains unresolved work
- `Verify` keeps:
  - original scope and acceptance criteria
  - latest implementation summary
  - latest evidence manifest
  - changed file list if available from the ticket
  - stage-contract checklist
- `Learn` keeps:
  - original scope
  - latest `## Implementation`
  - latest `## QA Evidence`
  - latest acceptance scorecard
  - latest merge/wiki/docs notes
- Rewind keeps:
  - original scope
  - latest failure section that caused the rewind
  - exact referenced artifact paths under `docs/<ticket>/` or `work/<ticket>/`
  - the current state transition instruction

**Test first:**

- Build a synthetic ticket body with repeated `## Plan`, `## Review Findings`, `## QA Failure`, `## Implementation`, and long historical logs.
- Assert `build_issue_prompt_context(issue, state="Verify")` contains the latest implementation and evidence sections but excludes older repeated failure sections.
- Assert rewind context contains the newest failure section and excludes stale earlier failure sections.
- Assert `build_issue_prompt_context(issue, state="Verify")` does not contain `TASK-004:`, `Current state:`, labels, or blocker scaffolding because `base.md` already renders them.
- Add a rendered-prompt integration test using `build_first_turn_prompt` and `docs/symphony-prompts/file/base.md`; assert the final prompt omits a long stale historical section that existed in the raw `issue.description`.
- Add a rendered-prompt length regression test: compacted rendered prompt length is smaller than the un-compacted rendered prompt for the synthetic repeated-history ticket.
- Assert prompt rendering includes `Full ticket: kanban/TASK-004.md` or the equivalent tracker path outside `## Description`.

**Implementation notes:**

- Keep Markdown parsing simple and deterministic:
  - parse headings and section bodies.
  - do not use a Markdown renderer.
  - preserve section body text exactly for retained sections.
- Use section allowlists by normalized state.
- Add a byte or character cap per retained section only after section selection; this cap is about repeated prompt input, not reasoning tokens. It should keep heading plus the first and last useful lines and must not truncate in the middle of a code fence.
- Prefer a pure function:

```python
def build_issue_prompt_context(
    issue: Issue,
    *,
    state: str,
    is_rewind: bool = False,
) -> str:
    sections = parse_ticket_sections(issue.description or "")
    selected = select_sections_for_state(
        sections,
        state=state,
        is_rewind=is_rewind,
    )
    return render_selected_sections(selected)
```

- In `prompt.py`, add optional parameters to `build_prompt_env` and `build_first_turn_prompt`:

```python
def build_prompt_env(
    issue_obj: Any,
    attempt: int | None,
    *,
    compact_issue_context: bool = False,
    full_ticket_path: str | None = None,
    language: str | None = None,
    is_rewind: bool = False,
) -> dict[str, Any]:
    issue_dict = issue_obj.to_template_dict()
    if compact_issue_context:
        issue_dict = dict(issue_dict)
        issue_dict["description"] = build_issue_prompt_context(
            issue_obj,
            state=issue_dict.get("state") or "",
            is_rewind=is_rewind,
        )
    if full_ticket_path:
        issue_dict = dict(issue_dict)
        issue_dict["full_ticket_path"] = full_ticket_path
    return {"issue": issue_dict, "attempt": attempt, "language": language}
```

- When `compact_issue_context` is true, copy `issue_dict`, replace only `issue_dict["description"]` with the compact description-body text, and set `issue_dict["full_ticket_path"] = full_ticket_path`.
- Modify both built-in base templates to render:

```jinja
{% if issue.full_ticket_path %}Full ticket: {{ issue.full_ticket_path }}{% endif %}
```

outside `## Description`.
- In `Orchestrator._run_agent_attempt` and `_rebuild_backend_for_phase`, compute `full_ticket_path` only for trackers that expose `find_path(identifier)`. For the file tracker, use `self._tracker.find_path(issue.identifier)` through a small helper; otherwise pass `None`.
- If direct tracker access is not available at the call site, add a small orchestrator helper:

```python
def _ticket_prompt_path(self, cfg: ServiceConfig, issue: Issue) -> str | None:
    find_path = getattr(self._tracker, "find_path", None)
    if cfg.tracker.kind != "file" or find_path is None:
        return None
    path = find_path(issue.identifier)
    return str(path) if path is not None else None
```

- Keep the compaction feature behind `agent.compact_issue_context` until the rendered-prompt tests pass.
- Keep a config flag for rollback:

```yaml
agent:
  compact_issue_context: false
```

Default should stay `false` until the integration tests prove rendered prompt size drops and no scaffold fields are duplicated. Flip to `true` in the same patch only if the changelog records before/after prompt-size evidence.

**Verification commands:**

```bash
pytest tests/test_prompt_context.py
pytest tests/test_workflow_pipeline_prompt.py -k "prompt or rewind or verify"
pytest tests/test_prompt_context.py tests/test_workflow_pipeline_prompt.py
```

**Acceptance:**

- Prompt tests prove quality-critical sections remain for each stage.
- Rendered-prompt tests prove compaction is wired and actually reduces repeated prompt input.
- Rendered-prompt tests prove identifier/title/state/labels/blockers are not duplicated inside `## Description`.
- Repeated historical sections are not sent by default.
- Operators can still inspect the full Markdown ticket from the prompt path.

### 4. State-Local Watchdogs and Soft Token Visibility

**Priority:** P1

**Problem:** global `max_turns`, `max_total_turns`, and optional hard token caps are too coarse. They protect only after a ticket has already burned large amounts, and low token caps can punish valid thinking-heavy work. The live `jira-symphony` workflow set turn limits high enough to be effectively disabled.

**Desired behavior:**

- Workflow config supports per-state turn caps for repeated no-progress loops.
- Existing hard token caps remain disabled by default.
- Optional token attention thresholds report unusual spend without blocking.
- Budget exhaustion from turn caps transitions to the configured `budget_exhausted_state`.
- Existing workflows without the new fields retain current behavior unless defaults are intentionally changed in a migration note.

**Files:**

- Modify `src/symphony/workflow/config.py`.
- Modify `src/symphony/workflow/builder.py`.
- Modify `src/symphony/orchestrator/core.py`.
- Extend `tests/test_workflow.py`.
- Extend `tests/test_orchestrator_dispatch.py`.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Proposed config for a reasoning-heavy workflow:**

```yaml
agent:
  max_state_turns_by_state:
    "In Progress": 6
    Verify: 3
    Learn: 3
  max_total_tokens: 0
  max_total_tokens_by_state: {}
  token_attention_threshold_by_state: {}
```

The turn caps stop repeated same-state loops. `Todo` is omitted because file-board auto-triage usually moves actionable tickets without backend turns, so a `Todo` turn cap would be misleading protection. Token caps stay off so large reasoning turns are allowed when they are productive.

**Implementation notes:**

- Add `max_state_turns_by_state: dict[str, int] = Field(default_factory=dict)`.
- Normalize state names once when loading config.
- Effective state cap order:
  1. `max_state_turns_by_state[normalized_state]`
  2. existing `max_state_turns`
  3. no per-state cap
- Keep hard token caps disabled for a backend unless the workflow owner explicitly configured them after reviewing measured usage.
- If `token_attention_threshold_by_state` is added, treat it as attention-only. It must not call `_persist_budget_exhausted_state`.
- If a cap trips, append a concise budget section naming:
  - ticket
  - state
  - observed turns or tokens
  - configured limit
  - next required human action
- If only an attention threshold trips, append no ticket section by default. Surface it in health/state APIs to avoid polluting the ticket with cost noise.

**Test first:**

- Add a workflow-config parse test for quoted and unquoted state names.
- Add an orchestrator test where `Verify` reaches two no-progress turns and transitions to `Blocked`.
- Add an orchestrator test where `In Progress` can use more turns than `Verify` under the same workflow.
- Add a config test proving `max_total_tokens` still defaults to `0`.
- Add an orchestrator test proving a high-token turn does not block when no hard cap is configured.

**Verification commands:**

```bash
pytest tests/test_workflow.py -k "max_state_turns or max_total_tokens"
pytest tests/test_orchestrator_dispatch.py -k "budget or no_stage"
pytest tests/test_workflow.py tests/test_orchestrator_dispatch.py
```

**Acceptance:**

- Per-state caps load from YAML and are visible in the effective config.
- Turn caps produce deterministic `Blocked` transitions with a readable budget artifact.
- Token attention thresholds produce operator-visible attention without blocking.
- Existing workflows with only `max_state_turns` still behave as before.

### 5. Hook and Workspace Preflight Guardrails

**Priority:** P1

**Problem:** setup hooks can hide dependency or environment failures, and workspace-root collisions can create expensive failures after dispatch. Both should be visible before the first agent turn.

**Desired behavior:**

- `symphony doctor` warns when hooks contain common failure-masking patterns.
- Hook output records enough context to diagnose failures without rerunning agents.
- Workspace root collisions are detected before dispatch.
- Default hook warnings do not block unless workflow config opts in.

**Files:**

- Modify `src/symphony/cli/doctor.py`.
- Modify `src/symphony/workspace.py`.
- Extend `tests/test_doctor.py`.
- Extend `tests/test_workspace.py`.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Hook warning patterns:**

- `|| true` after package install, code generation, migrations, or test commands.
- `tail -n` or `tail -<number>` on setup command output.
- known setup failure strings in captured hook output:
  - `PrismaConfigEnvError`
  - `Cannot resolve environment variable`
  - `Traceback`
  - `ModuleNotFoundError`

**Implementation notes:**

- `doctor` should report warnings with exact workflow line numbers when possible.
- Do not block on text scanning alone by default. Add an opt-in:

```yaml
hooks:
  fail_on_warning_patterns: true
```

- Capture complete hook stdout/stderr to a per-ticket artifact under the workspace metadata area. The console can stay concise.
- Workspace collision detection:
  - compute expected workspace path from project-scoped root logic.
  - if the path exists, inspect its metadata marker.
  - if marker points to a different workflow dir, board path, or repository, block before dispatch.

**Test first:**

- Add a doctor test with a workflow containing `pnpm install 2>&1 | tail -2 || true`; assert warning text includes line number and command.
- Add a workspace test with an existing path whose metadata marker references another workflow; assert workspace creation fails with a specific error.

**Verification commands:**

```bash
pytest tests/test_doctor.py -k "hook or warning"
pytest tests/test_workspace.py -k "collision or hook"
pytest tests/test_doctor.py tests/test_workspace.py
```

**Acceptance:**

- The `jira-symphony` hook masking pattern is detectable before dispatch.
- Existing non-masked hooks are not flagged.
- Workspace collisions fail before any backend agent starts.

### 6. Contract-Failure Fast Rewind Scope

**Priority:** P1

**Problem:** stage-contract failures are quality-preserving, but if the prompt resends too much history or the failure text is vague, the next turn can burn a large amount fixing only formatting/evidence paths.

**Desired behavior:**

- Contract checks return exact failing rows and expected evidence shape.
- Rewind prompts for contract failures include only:
  - original task scope
  - failing contract rows
  - expected artifact path pattern
  - current ticket path
- Contract checks continue to run against the raw full ticket body, not the compacted prompt body.
- A new standalone `symphony ticket-check` CLI is not part of this plan; it is useful but independently shippable.

**Files:**

- Modify `src/symphony/orchestrator/contracts.py`.
- Modify `src/symphony/orchestrator/core.py`.
- Extend `tests/test_orchestrator_contracts.py`.
- Extend `tests/test_workflow_pipeline_prompt.py`.
- Update `docs/changelog/changelog-2026-07-04.md`.

**Implementation notes:**

- Reuse the existing contract validator. Do not implement a second parser.
- Failure objects should include:
  - contract name
  - section
  - row or bullet index where available
  - found value
  - expected evidence shape
- Rewind prompt compaction from Task 3 should treat contract failure objects as first-class context.
- Leave CLI exposure for a follow-up plan after this reliability pass proves prompt and scheduler behavior.

**Test first:**

- Add a contract test with prose evidence such as `validated in source` and assert the failure says the evidence must be a durable path such as `docs/TASK-004/qa/evidence.md` or `work/TASK-004/verify.log`.
- Add a prompt test asserting a contract rewind includes failing rows but not full historical logs.
- Add a prompt test asserting contract validation still reads the raw `issue.description` even when prompt compaction is enabled.

**Verification commands:**

```bash
pytest tests/test_orchestrator_contracts.py -k "evidence or contract"
pytest tests/test_workflow_pipeline_prompt.py -k "contract"
```

**Acceptance:**

- Contract failures stay strict.
- Formatting-only or evidence-path failures rewind with narrow context.
- Rewind context is narrow and actionable.

### 7. Operator Visibility and Live-Run Measurement

**Priority:** P1

**Problem:** token savings must be proven on the same surfaces operators already use, not only unit tests.

**Desired behavior:**

- `/api/v1/state` shows blocker, token, budget, and contract attention clearly.
- `/api/v1/runs?limit=5` shows run endings with `Blocked` reason when turn caps or explicitly configured hard token caps trip.
- `scripts/smoke_web_api.py` exercises the relevant health surfaces.
- A controlled rerun on `jira-symphony` produces lower input tokens without skipping gates.

**Files:**

- Extend `scripts/smoke_web_api.py` only if current state smoke omits the new attention fields.
- Extend web API tests if a test module already covers state serialization.
- Add a measurement section to `docs/changelog/changelog-2026-07-04.md` after live verification.

**Live verification setup:**

Use a temp workspace under `/private/tmp` and a real `jira-symphony` workflow copy. Do not mutate the user's live board unless explicitly instructed.

Suggested commands after unit tests pass:

```bash
python -m pytest tests/test_tracker_file.py tests/test_orchestrator_dispatch.py tests/test_prompt_context.py tests/test_workflow.py tests/test_backends.py tests/test_doctor.py tests/test_workspace.py tests/test_orchestrator_contracts.py
symphony doctor /private/tmp/jira-symphony-rerun/WORKFLOW.md
python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999
```

Monitor:

```bash
curl -s http://127.0.0.1:9999/api/v1/state
curl -s 'http://127.0.0.1:9999/api/v1/runs?limit=5'
```

**Acceptance metrics:**

- No ticket with unresolved dependencies dispatches.
- `Todo` auto-triage does not move dependent tickets to `In Progress`.
- `Verify` and `Learn` still execute where required.
- Stage-contract failures remain blocking and specific.
- For comparable `Verify` and `Learn` turns, prompt input tokens fall materially because repeated historical sections are removed. A target of 30 percent input-token reduction is useful, but a smaller reduction is acceptable if the retained context is required for quality.
- Total tokens are not capped in the live measurement unless the workflow owner explicitly opts in; reasoning-heavy turns may remain large.
- No productive OpenCode turn records zero tokens.
- Any budget-triggered `Blocked` transition includes the state, observed count, limit, and next action.

## Per-Task Execution Checklists

### Task 1 Checklist: Dependency-Aware Dispatch

- [x] Add `tests/test_tracker_file.py::test_body_dependencies_become_blockers_with_state`.

```bash
pytest tests/test_tracker_file.py::test_body_dependencies_become_blockers_with_state -q
```

Expected before implementation: fail because `## Dependencies` IDs are not in `issue.blocked_by`.

- [x] Add `tests/test_tracker_file.py::test_unknown_body_dependency_remains_blocker_without_state`.

```bash
pytest tests/test_tracker_file.py::test_unknown_body_dependency_remains_blocker_without_state -q
```

Expected before implementation: fail because unknown body dependency IDs are ignored.

- [x] Add `tests/test_orchestrator_dispatch.py::test_active_state_issue_with_unresolved_blocker_is_ineligible`.

```bash
pytest tests/test_orchestrator_dispatch.py::test_active_state_issue_with_unresolved_blocker_is_ineligible -q
```

Expected before implementation: fail because `_eligible(issue, cfg)` gates blockers only in `todo`.

- [x] Add `tests/test_orchestrator_dispatch.py::test_issue_attention_reports_unresolved_dependency`.

```bash
pytest tests/test_orchestrator_dispatch.py::test_issue_attention_reports_unresolved_dependency -q
```

Expected before implementation: fail because `issue_attention(issue)` has no dependency-blocked signal.

- [x] Implement body dependency parsing in `src/symphony/trackers/file.py`, hydrate states through the existing board scan, and keep unknown IDs with `state=None`.
- [x] Move the blocker eligibility check in `src/symphony/orchestrator/core.py` outside the `todo` branch.
- [x] Add dependency-blocked attention in `Orchestrator.issue_attention(issue)`.
- [x] Run:

```bash
pytest tests/test_tracker_file.py -k "dependency or blocker"
pytest tests/test_orchestrator_dispatch.py -k "blocker or dependency or auto_triage"
pytest tests/test_webapi.py -k "attention"
```

Expected after implementation: pass.

- [x] Update `docs/changelog/changelog-2026-07-04.md` with the dependency parser, unknown-ID behavior, and active-state gating rationale.

### Task 2 Checklist: Token Telemetry Without Starving Reasoning

- [x] Add or extend OpenCode 1.17 usage fixtures in `tests/test_backends.py`.
- [x] Add tests proving `step_finish.part.tokens` fields are parsed and cumulative totals delta without double-counting.

```bash
pytest tests/test_backends.py -k "opencode and token" -q
```

Expected before implementation only if a parser regression exists: fail with mismatched token fields. If it already passes, keep the test as a regression pin.

- [x] Add `tests/test_orchestrator_dispatch.py::test_high_token_turn_without_threshold_records_without_attention`.

```bash
pytest tests/test_orchestrator_dispatch.py::test_high_token_turn_without_threshold_records_without_attention -q
```

Expected before implementation: pass if current defaults already avoid blocking; keep it to protect reasoning-heavy turns.

- [x] Add `tests/test_orchestrator_dispatch.py::test_productive_zero_token_turn_reports_attention`.

```bash
pytest tests/test_orchestrator_dispatch.py::test_productive_zero_token_turn_reports_attention -q
```

Expected before implementation: fail if no attention exists for productive zero-token telemetry.

- [x] Implement attention-only zero-token telemetry in the existing health/attention path.
- [x] If adding `agent.token_attention_threshold_by_state`, prove default `{}` produces no warning.
- [x] Run:

```bash
pytest tests/test_backends.py tests/test_orchestrator_dispatch.py -k "token or attention"
```

Expected after implementation: pass, with no hard token cap enabled by default.

- [x] Update the changelog to state explicitly that high thinking-token use is not a failure condition.

### Task 3 Checklist: Rendered Prompt Compaction

- [x] Add `tests/test_prompt_context.py::test_verify_context_keeps_latest_evidence_and_drops_stale_history`.
- [x] Add `tests/test_prompt_context.py::test_compact_description_does_not_duplicate_base_scaffolding`.

```bash
pytest tests/test_prompt_context.py -q
```

Expected before implementation: fail because `src/symphony/prompt_context.py` does not exist.

- [x] Add `tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description`.

```bash
pytest tests/test_prompt.py::test_compact_issue_context_changes_rendered_prompt_description -q
```

Expected before implementation: fail because `build_first_turn_prompt` does not accept compact-context inputs.

- [x] Add `tests/test_workflow_pipeline_prompt.py::test_file_base_prompt_renders_full_ticket_path_outside_description`.

```bash
pytest tests/test_workflow_pipeline_prompt.py::test_file_base_prompt_renders_full_ticket_path_outside_description -q
```

Expected before implementation: fail because `issue.full_ticket_path` is not rendered.

- [x] Implement `src/symphony/prompt_context.py` as a pure Markdown-section selector.
- [x] Add `compact_issue_context` and `full_ticket_path` parameters through `build_prompt_env` and `build_first_turn_prompt`.
- [x] Thread `full_ticket_path` from file tracker `find_path(identifier)` through `Orchestrator._run_agent_attempt` and `_rebuild_backend_for_phase`.
- [x] Update `docs/symphony-prompts/file/base.md` and `docs/symphony-prompts/linear/base.md` together so built-in prompt anchors stay aligned.
- [x] Run:

```bash
pytest tests/test_prompt_context.py
pytest tests/test_prompt.py -k "compact or first_turn"
pytest tests/test_workflow_pipeline_prompt.py -k "prompt or description or full_ticket"
```

Expected after implementation: pass, and rendered compact prompts are shorter for repeated-history tickets.

- [x] Keep `agent.compact_issue_context` default `false` unless before/after rendered-prompt evidence is recorded in the changelog.

### Task 4 Checklist: State-Local Watchdogs

- [x] Add `tests/test_workflow.py::test_build_service_config_reads_state_turn_caps`.
- [x] Add `tests/test_workflow.py::test_max_total_tokens_defaults_disabled_for_reasoning_heavy_work`.

```bash
pytest tests/test_workflow.py -k "state_turn_caps or max_total_tokens_defaults" -q
```

Expected before implementation: first test fails if per-state turn caps do not exist; second should pass and protect current default.

- [x] Add `tests/test_orchestrator_dispatch.py::test_verify_state_turn_cap_blocks_with_budget_artifact`.
- [x] Add `tests/test_orchestrator_dispatch.py::test_high_token_turn_without_hard_cap_does_not_block`.
- [x] Add `tests/test_orchestrator_dispatch.py::test_token_attention_threshold_never_persists_budget_state`.
- [x] Implement `agent.max_state_turns_by_state` parsing and normalized lookup.
- [x] Use per-state turn cap before global `max_state_turns`; do not connect attention thresholds to `_persist_budget_exhausted_state`.
- [x] Run:

```bash
pytest tests/test_workflow.py -k "state_turn_caps or max_total_tokens_defaults"
pytest tests/test_orchestrator_dispatch.py -k "budget or no_stage or token"
pytest tests/test_workflow_mutate.py::test_rename_column_updates_per_state_maps
```

Expected after implementation: pass.

- [x] Update the changelog with the difference between turn caps, attention thresholds, and opt-in hard token caps.

### Task 5 Checklist: Hook and Workspace Preflight

- [x] Add `tests/test_doctor.py::test_after_create_warns_on_masked_install_output`.
- [x] Add `tests/test_doctor.py::test_after_create_warning_does_not_fail_by_default`.

```bash
pytest tests/test_doctor.py -k "after_create and warn" -q
```

Expected before implementation: fail because `check_after_create_hook` only checks placeholders.

- [x] Add `tests/test_workspace.py::test_workspace_collision_blocks_before_after_create`.
- [x] Add `tests/test_workspace.py::test_workspace_board_root_collision_blocks_before_after_create`.
- [x] Add `tests/test_doctor.py::test_after_create_warning_can_fail_by_policy`.
- [x] Add `tests/test_doctor.py::test_after_create_warns_on_known_setup_failure_text`.
- [x] Add `tests/test_workspace.py::test_hook_failure_preserves_full_output_artifacts`.
- [x] Add `tests/test_workspace.py::test_hook_output_artifact_records_setup_failure_patterns`.
- [x] Add `tests/test_workspace.py::test_hook_timeout_writes_output_artifact`.
- [x] Add `tests/test_workflow.py::test_hooks_warning_policy_defaults_to_nonfatal_and_can_opt_in`.
- [x] Extend `src/symphony/cli/doctor.py::check_after_create_hook` to detect `|| true` and `tail` masking in setup commands.
- [x] Extend workspace collision checks using a root-level workspace ownership marker; block only when marker points to another workflow, board, or repo.
- [x] Persist complete hook stdout/stderr under workspace metadata so failed setup can be diagnosed after workspace cleanup.
- [x] Run:

```bash
pytest tests/test_doctor.py -k "hook or after_create"
pytest tests/test_workspace.py -k "collision or after_create"
```

Expected after implementation: pass.

- [x] Update the changelog with why hook warnings default to non-blocking.

### Task 6 Checklist: Contract-Failure Rewind Scope

- [x] Add `tests/test_orchestrator_contracts.py::test_contract_failure_reports_expected_evidence_path_shape`.
- [x] Add `tests/test_orchestrator_contracts.py::test_contract_failure_rejects_placeholder_evidence_cells`.
- [x] Add `tests/test_orchestrator_contracts.py::test_contract_failure_note_round_trips_backticked_evidence_scope`.
- [x] Add `tests/test_workflow_pipeline_prompt.py::test_contract_rewind_prompt_uses_failing_rows_not_full_history`.
- [x] Add `tests/test_orchestrator_phase_transition.py::test_contract_validation_uses_raw_ticket_body_when_prompt_compacted`.
- [x] Add `tests/test_orchestrator_dispatch.py::test_apply_dispatch_env_uses_latest_contract_failure_scope`.

```bash
pytest tests/test_orchestrator_contracts.py -k "evidence or contract"
pytest tests/test_workflow_pipeline_prompt.py -k "contract"
```

Expected before implementation: fail only for the new narrow-rewind behavior; existing strict contract tests should keep passing.

- [x] Reuse `src/symphony/orchestrator/contracts.py`; do not add a second contract parser.
- [x] Feed structured contract failures into the rewind-scope path used by Task 3.
- [x] Leave a standalone `symphony ticket-check` CLI for a separate plan.
- [x] Run the same two test commands again.

Expected after implementation: pass.

### Task 7 Checklist: Live Measurement

- [x] Create a temp copy of `jira-symphony` under `/private/tmp`; do not mutate the live board.
- [x] Run the targeted unit suite from the Full Verification Checklist.
- [x] Run `symphony doctor` against the copied workflow and confirm hook warnings are visible.
- [x] Start the service on a free port and run `python scripts/smoke_web_api.py --base-url http://127.0.0.1:<port>`.
- [x] Poll `/api/v1/state` and `/api/v1/runs?limit=5` during a controlled run.
- [x] Compare rendered prompt input tokens for the copied board's comparable `Verify` turn; no active `Learn` ticket was present in the copied board.
- [x] Record before/after numbers and any quality-gate failures in `docs/changelog/changelog-2026-07-04.md`.

## Rollout Strategy

1. Land Task 1 first. It prevents unnecessary downstream dispatch and has low risk.
2. Land Task 2 before any workflow owner relies on optional hard token caps. It proves the meter.
3. Land Task 3 with `agent.compact_issue_context: false` by default, then enable it for a copied workflow only after rendered-prompt evidence shows no duplicated scaffold and lower repeated input.
4. Land Task 4 with conservative per-state defaults. If live tests show false blocks, keep defaults off and document recommended `jira-symphony` workflow values.
5. Land Task 5 and Task 6 as operator-quality improvements.
6. Run the live measurement pass and record before/after numbers in the changelog.

## Risk Matrix

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Body dependency parser treats a historical mention as a blocker | Ticket waits unnecessarily | Parse only the `## Dependencies` section. Ignore other sections. |
| Per-state turn caps block legitimate deep implementation work | Quality loss | Use higher `In Progress` cap than `Verify`/`Learn`; emit readable `Blocked` artifacts; keep config override. |
| Low hard token caps block valid thinking-heavy turns | Quality loss | Keep hard token caps default-disabled; rely on dependency gating, prompt compaction, and attention-only telemetry. |
| Prompt compaction drops context needed by Verify | False QA failure or missed defect | Stage-specific allowlists, red tests with repeated sections, full-ticket path in every prompt. |
| Token telemetry warning creates noisy attention | Operator fatigue | Warning only for productive zero-token turns or explicitly configured threshold exceedances; no hard block unless a workflow owner opted into a hard cap. |
| Hook warning pattern catches intentional `|| true` | False warning | Report as warning by default; fail only with `hooks.fail_on_warning_patterns: true`. |
| Contract CLI duplicates validator logic | Drift | Reuse `contracts.py` validator and expose structured results. |

## Rejected Alternatives

- **Lower model quality or reasoning effort:** saves tokens but directly risks poorer implementation and weaker Verify outcomes.
- **Skip Verify or Learn on small tickets:** reduces turns by weakening the quality gate that caught real defects.
- **Prompt-only shortening as the first fix:** useful but incomplete; it does not stop dependent tickets from starting too early.
- **Global low `max_turns`:** can block legitimate complex tasks while still allowing expensive first turns.
- **Auto-advance `In Progress` to `Verify` after no progress:** risks promoting incomplete work. Blocking with evidence is safer.
- **Accept prose evidence in stage contracts:** would reduce rewinds by lowering proof quality. Keep durable artifact paths.

## Full Verification Checklist

- [x] Red tests fail for dependency parsing, active-state blocker eligibility, and auto-triage refusal.
- [x] Dependency tests pass after tracker and scheduler changes.
- [x] OpenCode text and token telemetry tests pass.
- [x] Prompt compaction tests prove stage-critical context is retained.
- [x] Per-state watchdog tests pass.
- [x] Hook and workspace preflight tests pass.
- [x] Contract-failure prompt and contract tests pass.
- [x] `pytest` targeted suite passes:

```bash
pytest tests/test_tracker_file.py tests/test_orchestrator_dispatch.py tests/test_workflow.py tests/test_workflow_pipeline_prompt.py tests/test_prompt.py tests/test_prompt_context.py tests/test_backends.py tests/test_orchestrator_contracts.py tests/test_doctor.py tests/test_workspace.py tests/test_webapi.py
```

- [x] `symphony doctor` reports the intended warnings on a copied `jira-symphony` workflow.
- [x] Web smoke proves `/api/v1/state` and `/api/v1/runs?limit=5` expose the new safety signals.
- [x] Controlled copied-workflow smoke records rendered prompt-token evidence and preserves `Verify`, `Learn`, `Human Review`, and strict contract behavior through targeted tests.
- [x] Full repository suite passes after lifecycle fixture evidence was updated for the stricter Verify contract:
  `rtk pytest -q` -> 1,108 passed, 2 skipped.

## Claude Review Applied

Claude Code reviewed the plan in read-only mode on 2026-07-04 and found the architecture sound but called out concentrated risks in prompt compaction and dependency handling. This revision incorporates the review as follows:

- Rendered-prompt tests now prove compaction is wired end-to-end, not only in a pure helper.
- Compacted descriptions no longer own identifier, title, state, labels, or blocker scaffolding that `base.md` already renders.
- `full_ticket_path` now has a defined source: file tracker `find_path(identifier)` threaded through orchestrator prompt construction.
- Unknown dependency IDs remain safety blockers but must surface as `blocked_dependency` attention so typos are visible.
- Active-state blocker gating now includes retry-pending visibility and recovery expectations.
- The standalone `symphony ticket-check` CLI is deferred to a separate plan to keep this plan focused.
- `compact_issue_context` stays default-disabled until rendered-prompt evidence proves it saves repeated input without dropping quality-critical context.

## Implementation Handoff Notes

- Keep each task in a small commit or patch set. Do not mix prompt compaction with dependency dispatch changes.
- Update `docs/changelog/changelog-2026-07-04.md` with the reasoning behind each decision and rejected shortcut.
- Preserve existing OpenCode 1.17 fixes. Add tests around them before changing token-budget behavior.
- Use temp copies for live reruns. Do not mutate the active `jira-symphony` board unless explicitly approved.
- Treat `/api/v1/state` as the headless truth surface for stuck runs and attention warnings.
