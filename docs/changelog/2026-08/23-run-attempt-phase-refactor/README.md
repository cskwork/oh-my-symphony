# Run-attempt phase-transition refactor

## Domain brief

- Knowledge source: repository `CONTEXT.md`; durable `.domain-agent/` storage
  was declined for this run.
- Stable terms: Run attempt, Attempt event, Continuation checkpoint, Release
  gate, Release verifier, Release finalizer, Target commit.
- Invariant: a phase transition remains inside one Run attempt and does not
  replace its `run_id`, stable running issue ID, workspace, lease, or terminal
  outcome ownership.
- Entry point: `Orchestrator._dispatch` -> `LegacyStageExecutor.execute` ->
  `Orchestrator.run_legacy_stage_loop` -> `Orchestrator._run_agent_attempt`.
- Refactor seam: the phase-transition transaction currently inside
  `_run_agent_attempt`.
- Verification sources: `CONTRIBUTING.md`, focused orchestrator suites, release
  contract integration, full pytest, Ruff, Pyright, and `symphony doctor`.
- Gaps: Windows may capability-skip release tests requiring symlinks; these
  skips must be reported and Linux CI remains the complementary platform gate.

## Accepted design

Extract one private, same-file phase-transition operation with an immutable
typed state value. The caller rebinds the returned backend before transition
reporting so its existing `finally` remains exception-safe. Preserve behavior
and ordering; add no public interface or new module.

The private transition result also carries the exact rewind decision; callers
do not infer it from mutation of the debug counter.

## Rejected alternatives

- whole attempt-runner extraction in one batch;
- worker-exit state-machine extraction in this batch;
- Claude/Pi backend inheritance migration;
- public executor abstractions;
- mutable transition context.

## Adversarial review

The strongest objection was that relocating roughly 270 lines could create a
shallow helper with a broad, mutable interface. The design was revised to use
one immutable state value and to narrow further rather than accept a mutable
context or broad unrelated output tuple.

The first independent spec review found that reporting inside the helper could
raise after backend replacement but before the caller rebound its cleanup
reference. The seam was narrowed so reporting remains in the caller after the
rebind; this removes the leak path without a mutable callback or exception
wrapper.

Final independent reviews found no remaining spec, critical, important, or
minor quality issues. Static analysis exposed one optional closure capture in
unchanged finalizer code after the large coroutine became easier to analyze;
capturing the resolved finalizer identifier before the lambda restored a clean
type gate without changing runtime behavior.

## Verification summary

- Focused lifecycle/transition/contract gate: 55 passed.
- Release-contract integration: 48 passed, 18 Windows capability skips.
- Ruff: clean. Pyright: 0 errors. Patch integrity: clean.
- Native browser: 5 passed. Native lifecycle/deep/contract/release shard:
  77 passed with 18 declared Windows symlink-capability skips.
- Full detached pytest with browser E2E: 2311 passed, 80 declared platform/
  capability skips, 0 failures, 0 errors.
- `symphony doctor`: passed against the isolated scratch workflow; its only
  warning was the intentional `agent.stage_contracts: off` configuration.
- Real OpenCode and live browser verification passed. The service teardown
  defect found during that run was fixed and ordinary stop then passed twice
  against the real Windows launcher/child process shape.
