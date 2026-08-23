# Run-attempt phase-transition refactor implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Reduce `_run_agent_attempt`'s local complexity by extracting its
behavior-preserving phase-transition transaction behind one private typed
interface.

**Architecture:** Keep the outer Run attempt coroutine as the owner of loop,
cancellation, release-gate, cleanup, and exit behavior. Move only contract
adjudication, rewind accounting, backend rerouting/rebuild, phase-local resets,
and the explicit rewind decision into `_transition_agent_phase`, carrying
changing values in an immutable `_AgentPhaseState`. Keep transition reporting
in the caller after it rebinds the replacement backend.

**Tech stack:** Python 3.12+, asyncio, dataclasses, pytest, Ruff, Pyright.

---

### Task 1: Preserve and extract the phase-transition transaction

**Files:**

- Modify: `src/symphony/orchestrator/core.py`
- Retain: `tests/test_orchestrator_phase_transition.py`
- Retain: `tests/test_agent_lifecycle_e2e.py`
- Retain: `tests/test_orchestrator_contract_integration.py`

**Step 1: Re-run the unchanged characterization gate**

Run:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_orchestrator_phase_transition.py `
  tests\test_agent_lifecycle_e2e.py `
  tests\test_orchestrator_contract_integration.py `
  --basetemp .tmp\pytest-phase-before -p no:cacheprovider
```

Expected: `55 passed`. This is a characterization gate, so it must pass on the
unchanged implementation before refactoring; no production edit is allowed if
it does not.

**Step 2: Add the immutable private phase state**

Add near the other private orchestration values in `core.py`:

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

Do not export it from `symphony.orchestrator` and do not add a public interface.

**Step 3: Extract one private transition operation**

Add `Orchestrator._transition_agent_phase`. It must preserve the exact current
statement ordering from the existing `if is_phase_transition` block. It returns
an `_AgentPhaseTransition` containing the updated state and exact rewind
decision, or `None` only for the existing rewind-budget stop path. It must let
exceptions propagate so the caller retains the existing
`phase_transition_error` mapping.

The helper must:

- obtain `_IssueDebug` through `self._issue_debug`;
- use a full issue refresh before stage-contract evaluation;
- keep contract note/state-write/full-refresh ordering;
- reroute from `base_cfg`, never the already routed config;
- rebuild through `_rebuild_backend_for_phase`;
- reset the existing session IDs, completion marker, per-phase token high-water
  values, per-state token totals, EMA state total, and budget/watchdog flags;
- preserve lifetime token totals.

**Step 4: Replace the inline block with the typed call**

Construct `_AgentPhaseState` from the existing locals, call the helper, break
when it returns `None`, and otherwise rebind the six changing values. Only
after rebinding, emit the existing transition log, lifecycle event, and stats
in their original order. This ordering ensures a reporting exception reaches
the outer `finally` with the replacement backend already owned by the caller.
Keep the caller's `try/except` and outcome strings exactly as they are.

**Step 5: Run the focused gate**

Run the Step 1 command with basetemp `.tmp\pytest-phase-after`.

Expected: `55 passed` with no changed assertions, skips, or warnings caused by
the refactor.

**Step 6: Inspect the diff before broader verification**

Run:

```powershell
git diff -- src/symphony/orchestrator/core.py tests
git diff --check
```

Reject the batch if it changes public exports, outcome strings, event payloads,
tracker write order, release checks, or cleanup/exit code.

### Task 2: Independent review and complete verification

**Files:**

- Update: `docs/changelog/2026-08/23-run-attempt-phase-refactor/delivery-proof.md`
- Update: `docs/changelog/2026-08/23-run-attempt-phase-refactor/README.md`

**Step 1: Run spec-compliance review**

An independent reviewer must compare the actual diff to the approved design,
including the explicit scope exclusions and statement ordering.

**Step 2: Run code-quality review**

After spec compliance passes, an independent reviewer must assess whether the
new private interface is deep enough, immutable, precisely named, and easier to
maintain than the inline block. Important findings must be fixed and re-reviewed.

**Step 3: Run release-sensitive integration tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_orchestrator_release_contract_integration.py `
  --basetemp .tmp\pytest-phase-release -p no:cacheprovider
```

Record passes and capability-based skips separately.

**Step 4: Run repository gates**

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\symphony-pyright.exe
.\.venv\Scripts\symphony.exe doctor .\WORKFLOW.md
git diff --check
```

**Step 5: Run the full suite using the documented detached Windows launcher**

Launch `.venv\Scripts\python.exe -m pytest -q` in a hidden detached process,
write JUnit output into the repository evidence directory, wait for completion,
and inspect the XML counts. Do not infer success from process disappearance.

**Step 6: Update delivery proof and report**

Record exact commands, exit codes/counts, pre-existing failures, skips,
unverified platform risks, and reviewer findings. Do not commit until the proof
is green and the user accepts the result.
