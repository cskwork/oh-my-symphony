# QA: beginner autonomous development factory

## Before

- Root `WORKFLOW.file.example.md` has active states `Todo`, `In Progress`,
  `Verify`, and `Learn` and is 322 lines.
- File prompt tree totals 285 lines and duplicates planning, implementation,
  evidence, security, merge, and learning procedure.
- `symphony factory` does not exist.
- No Wayfinder-to-file-board importer exists.
- `board new` does not expose the tracker's existing `skills` field.

## After Target

- Minimal default, preserved advanced profile, safe idempotent Wayfinder sync,
  routed skill overlays, beginner CLI, independent Verify, and real OpenCode
  runtime evidence.

## Commands

| Name | Command | Purpose |
| --- | --- | --- |
| focused-factory | `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory` | Parser, sync, routing, CLI |
| template-contract | `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_templates.py tests/test_workflow_pipeline_prompt.py` | Default and advanced prompt contracts |
| cli-regression | `.venv/bin/python -m pytest -q tests/test_board_cli_subcommands.py tests/test_cli_main_routing.py` | Public command compatibility |
| lifecycle | `.venv/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_phase_transition.py` | Stage and dependency behavior |
| full-tests | `.venv/bin/python -m pytest -q` | Repository regression |
| lint | `.venv/bin/python -m ruff check src tests` | Static quality |
| types | `.venv/bin/python -m pyright` | Type safety |
| doctor | `.venv/bin/symphony doctor ./WORKFLOW.file.example.md` | Shipped default validity |
| diff | `git diff --check` | Patch hygiene |
| opencode-e2e | disposable Symphony service using `agent.kind: opencode` | Real backend and state loop |
| full-spec-improve | `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory` | Factory ownership, routing, prompt, sync, and CLI contracts after the full-spec pass |
| edge-improve | `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory tests/test_doctor.py tests/test_tracker_file.py tests/test_cli_main_routing.py tests/test_board_cli_subcommands.py` | Managed edits, safe prefixes, path compatibility, optional skill init, CLI failure propagation, and surrounding regressions |
| wheel-assets | `UV_CACHE_DIR=/tmp/uv-cache uv build --wheel --out-dir /tmp/symphony-factory-wheel-clean && unzip -l /tmp/symphony-factory-wheel-clean/*.whl` | Clean wheel includes runtime factory assets but excludes bytecode and placeholder tickets |

## Results

- [x] Focused tests pass: `456 passed, 3 skipped`; final affected subset `89 passed, 1 skipped`.
- [x] Full tests pass: `1474 passed, 5 skipped`.
- [x] Ruff passes: `ruff check src tests`.
- [x] Pyright passes: `0 errors, 0 warnings` using the project dependency environment.
- [x] Doctor passes; only the optional legacy-viewer warning appeared in the disposable project.
- [x] Real OpenCode run passes.
- [x] Backward-trace: clean.

Verdict: pass.

Backward-trace: clean

## Reproduction Fidelity

- Fidelity level: exact

## QA

- CLI integration smoke: a freshly generated project ran real OpenCode Build
  and Verify workers, reached Done, committed exactly two scoped product files,
  auto-merged to disposable main, and passed its host unittest.
- Trusted proof: `evaluator_owned` exact commands from the approved QA plan.

## Final real OpenCode proof

- Disposable generated project: `/private/tmp/symphony-factory-opencode-ship-cfCae5`.
- Idempotent Wayfinder card SHA-256: `57dff8aa3c3ad2b10c00ca73e2988ab3d39bd6612a548975c6ad743c6ab75d75` twice.
- Real OpenCode sessions: Build `ses_0a885dec3ffeA99XhT2b4VR1Mu`; Verify `ses_0a88444aeffe1GcKzxgsysfqFl`.
- Machine lifecycle: deterministic Ready promotion, Build -> Verify, terminal Done, scoped auto-commit, automatic merge, normal exit.
- Scoped commit `1e6bf5c`: only `factory_probe.py` and `test_factory_probe.py`.
- Merge `9fe55bf` landed on disposable `main`; host proof command passed with one unittest.
- No `stage_contract_failed`, token-budget failure, process-vault output, or bytecode entered the shipping proof.
- CI-hermetic rerun: empty HOME plus the exact coverage command passed
  `1474 passed, 5 skipped` at 84.12% coverage.

## Final package proof

- Wheel built successfully at `/private/tmp/symphony-factory-wheel-FWRWlR/oh_my_symphony-0.13.0-py3-none-any.whl`.
- Wheel contains the factory workflow, Build/Verify prompts, and worktree hook; no bytecode match was present.

## Delivery

- Commit: `514c552` (`feat: add autonomous development factory`).
- Pull request: https://github.com/cskwork/oh-my-symphony/pull/57 into `dev`.

## 2026-07-13 focused live-failure regression

- RED: focused collection failed because `select_skills_for_stage` did not
  exist; dependency/frontmatter and exact budget assertions were also newly
  introduced.
- GREEN: `37 passed in 0.27s` for `tests/factory/test_sync.py`,
  `tests/factory/test_lifecycle.py`, `tests/factory/test_templates.py`, and
  `tests/test_stats_skills.py`.
- Ruff: focused changed paths passed.
- `git diff --check`: passed.
- Real OpenCode rerun: not run by instruction.

## 2026-07-13 reduced-context Ready calibration

- Clean repo: `/private/tmp/symphony-factory-opencode-e2e-YGvVfw`.
- Idempotent sync hash: `f103d813b0ee57c9d2dfd1dbcfd1d2051a3b667425661c646d94d173d222fe0e` twice.
- Doctor passed on port 19124 with only the optional legacy-viewer warning.
- OpenCode used 20,962 tokens in its first reduced-context request. The
  20,000-token Ready cap cancelled before the first read, so no transition was
  possible. Ready is recalibrated to 80,000 tokens; clean lifecycle proof is
  still required.

## 2026-07-13 deterministic Ready proof

- Failed-run evidence: clean Ready requests consumed 20,962 and 87,668 tokens
  before editing despite no attached skills and a direct stage prompt.
- RED: `pytest -q tests/test_orchestrator_dispatch.py -k factory_ready` reported
  2 failed, 2 passed; Ready did not promote and transition errors were absent.
- GREEN: the same command reported `4 passed, 183 deselected`.
- Runtime rerun: intentionally not run; this correction is proved with focused
  orchestrator tests and requires parent verification with the broader suite.

## 2026-07-13 workspace-local OpenCode calibration

- Clean repo: `/private/tmp/symphony-factory-opencode-e2e-hKPtZw`.
- Idempotent sync SHA-256:
  `731055d59bdcca478153ac5357c70e62cada1672b2b4398a4af4e64bfceae966`
  twice; Doctor passed on port 19127 with only the optional legacy-viewer
  warning.
- Symphony promoted Ready -> Build without a worker, created the isolated
  worktree, completed the hook, and started real OpenCode session
  `ses_0a8eb2768ffeSUYtz7McVpdSee`.
- The 400,000 Build cap stopped at 423,500 reported tokens before any product
  file was created; the card moved to Blocked and no merge occurred.
- Exported session evidence showed the prompt's ticket path was empty and the
  complete injected Supergoal body was then read again from `SKILL.md`. This
  run is failed calibration evidence, not lifecycle proof.

## 2026-07-13 shared-board regression

- RED: runtime-ignore, ignored-card, nonempty-board rejection, and stale-local
  prompt tests failed against the previous generator/hook/path behavior.
- GREEN target: generated cards are ignored, user ignore rules survive repeat
  init, the hook is rerun-safe for the host link and fails clearly for a
  tracked stale board, and prompt locality is accepted only for the shared
  host card.
