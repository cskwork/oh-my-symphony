# QA - <topic>

All testing results as succinct plain-language checklist sentences. Evidence lives in `qa/`.

- Verdict: PASS | FAIL | PARTIAL

## Before

- [x] <observed pre-change behavior / reproduced symptom / preserve-baseline> - evidence: `qa/<file>` or command output

## Results

- [x] <one sentence: what was exercised and what happened> - `<command>` (frozen_repo)
- [x] <neighbor baseline re-run, no unnamed drift> - `<command>` (evaluator_owned)

Backward-trace: clean | <orphan file:line list>

## Commands

| Command | Source | Proves |
|---|---|---|
|  | frozen_repo / evaluator_owned / agent_detected |  |

## QA

Tool: playwright-cli
UI-tier: <Expressive|Functional, UI runs only>
DB: <dialect, read-only, when used>
- as-is: `qa/as-is-<view>.png`  to-be: `qa/to-be-<view>.png`
- <per-scenario pass/fail + repro details for failures>

## Reproduction Fidelity

- Fidelity level: exact | prod-snapshot | synthetic-representative | synthetic-minimal | not-reproduced
- Residual risk from data gap:
- Post-deploy confirmation plan:

## Residual Risk

- Not proven:
- Follow-up:
