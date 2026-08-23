# Run-attempt phase-transition refactor design

## Context

`Orchestrator._run_agent_attempt` owns one durable Run attempt but currently
mixes release authority, workspace/backend startup, phase transitions, turn
execution, release gates, cleanup, and worker-exit handling in one coroutine.
The handoff at commit `1a5a7cece37032be333f4efb1050fb5561290793`
recommends splitting the phase-transition behavior first.

The existing domain model remains authoritative:

- a Run attempt keeps one `run_id`, workspace, lease, start time, and terminal
  outcome;
- a phase transition is an event inside that attempt, not a new attempt;
- refreshed tracker IDs must not replace the stable running issue ID;
- continuation checkpoints are written only after completed turns;
- release authority remains host-owned and fail-closed.

## Decision

Extract only the current phase-transition transaction from
`_run_agent_attempt` into one private, same-file operation. Pass the changing
phase values through an immutable `_AgentPhaseState` value and return the
updated value. Return `None` when the existing rewind budget stops the loop.

The operation owns this ordered behavior:

1. determine whether the transition is a rewind;
2. evaluate the producing stage's contract using a full ticket refresh;
3. append warnings or atomically rewind a failed contract in the existing
   order;
4. consume and enforce the rewind budget;
5. re-resolve the destination backend from the unrouted workflow config;
6. stop and rebuild the backend for the destination phase;
7. reset only per-phase session and token bookkeeping.

The caller immediately rebinds the returned backend before it emits the
phase-transition log, lifecycle event, and statistics in their existing order.
Keeping that final reporting sequence in the caller preserves exception-safe
cleanup: if reporting raises, the outer `finally` still owns and stops the
replacement backend.

The outer attempt loop continues to own pause handling, turn execution,
release gates, artifact salvage, cancellation, and identity-gated worker-exit
cleanup.

## Interface

The private state value contains only values that legitimately change across
the transaction:

```python
@dataclass(frozen=True)
class _AgentPhaseState:
    issue: Issue
    cfg: ServiceConfig
    client: AgentBackend
    first_prompt: str
    current_state: str
    known_app_release: bool

@dataclass(frozen=True)
class _AgentPhaseTransition:
    state: _AgentPhaseState
    is_rewind: bool
```

The operation receives the stable attempt facts separately: running issue ID,
unrouted config, workspace path, attempt metadata, documentation language,
the producing state and its tracker casing, and the lifetime turn number.
`_IssueDebug` remains orchestrator-owned and is resolved by issue ID inside the
operation rather than exposed through the interface.

The typed transition result makes the rewind decision explicit rather than
requiring the caller to infer it from the debug counter's mutation. This is an
internal seam only. It adds no public interface, adapter, module,
configuration, persistence shape, or event type.

## Error handling and compatibility

- Exceptions continue to be mapped by the caller to
  `phase_transition_error`; the operation does not invent new outcomes. After
  a successful rebuild, the caller rebinds the replacement backend before any
  potentially raising reporting operation.
- Tracker note/state-write order, full-versus-minimal refresh choices, backend
  stop/start order, and log/event/stat order remain unchanged.
- Backend cleanup uncertainty and PID ownership remain handled by the existing
  rebuild/finally paths.
- No behavior, schema, CLI, API, workflow, or migration change is intended.

## Verification

The pre-refactor preservation baseline is the existing lifecycle,
phase-transition, and contract-integration suites. The focused baseline passed
unchanged with `55 passed in 1.50s`.

After extraction, run:

1. the same 55-test focused gate;
2. release-contract integration tests because transition ordering is
   release-authority sensitive;
3. the full pytest suite using the repository's detached Windows recipe;
4. Ruff, Pyright, `symphony doctor ./WORKFLOW.md`, and `git diff --check`.

## Rejected approaches

- Full attempt-runner extraction: too much cancellation, cleanup, and release
  authority risk for one batch.
- Worker-exit state-machine extraction: deliberately deferred until the phase
  seam is stable.
- Claude/Pi inheritance migration: backend protocols differ in streaming,
  timeout, resume, error-ordering, and redaction behavior.
- New public executor interface or module: only one implementation exists, so
  the seam would be speculative.
- Mutable context object: it would hide writes and make concurrent state risks
  harder to review.

No ADR is warranted: this private extraction is reversible, unsurprising, and
does not change the domain model.
