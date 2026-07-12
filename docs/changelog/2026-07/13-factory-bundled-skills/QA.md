# QA - Factory bundled skills

- Verdict: FAIL

## Before

- [x] Factory init with an empty home directory exits 1 before creating a project: `error: required skill 'supergoal' is not installed` - evidence: `env HOME=/private/tmp/symphony-empty-home PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m symphony.cli factory init /private/tmp/symphony-factory-before`

## Results

Backward-trace: pending

### Improve full spec - 2026-07-13

- Package-first resolution covers all four standard names; arbitrary custom
  names still use the configured local roots and report every searched root
  when missing.
- Existing complete project copies remain untouched without `--force`.
  Grounded gap fixed: a directory with overlay support files but no required
  `SKILL.md` entry point is now rejected as incomplete instead of being
  silently preserved as a valid customization.
- Manifest commits, per-skill licenses, explicit nested package-data patterns,
  and beginner documentation cover the approved attribution, wheel-content,
  customization, and SuperQA external-runtime requirements.
- Not final verification: wheel build/install and isolated-wheel CLI smoke
  remain assigned to Exact Verify.

### Improve edge cases - 2026-07-13

- Red tests reproduced `--force` failures for file, directory-symlink, and
  broken-symlink skill destinations. Replacement now unlinks files and
  symlinks without following their targets and recursively removes only real
  directories.
- A broken destination symlink is now treated as an existing incomplete skill
  during init preflight. Non-force init fails actionably before writing
  `WORKFLOW.md`; force replaces the link with the bundled directory.
- A built-wheel inventory comparison found all 72 non-cache bundle source
  files in the wheel. A zipped-wheel smoke copied nested Superdesign and
  SuperQA assets and replaced a file destination through `Traversable`.
- No new semantics were added for skill selection: unknown valid custom names
  retain local-root fallback, and existing valid destinations remain untouched
  without `--force`.

## Commands

| Command | Source | Proves |
|---|---|---|
| `pytest -q tests/factory/test_cli.py` | frozen_repo | CLI packaging behavior |
| `pytest -q tests/factory` | frozen_repo | factory regressions |
| `python -m build --wheel` | evaluator_owned | distributable package build |
| `/opt/anaconda3/bin/python -m build --wheel --outdir /tmp/symphony-bundle-wheel-NPTLx4` | evaluator_owned | nested bundle files are emitted into the wheel |
| `/opt/anaconda3/bin/python -m zipfile -l /tmp/symphony-bundle-wheel-NPTLx4/oh_my_symphony-0.13.0-py3-none-any.whl` | evaluator_owned | the wheel lists every pinned nested runtime asset and license |
| `env HOME=/tmp/symphony-bundle-wheel-NPTLx4/empty-home /tmp/symphony-bundle-wheel-NPTLx4/venv/bin/symphony factory init /tmp/symphony-bundle-wheel-NPTLx4/smoke-project` | evaluator_owned | an installed wheel initializes with no home-directory skills |
| `env HOME=/tmp/symphony-bundle-wheel-NPTLx4/empty-home /tmp/symphony-bundle-wheel-NPTLx4/venv/bin/symphony factory sync /tmp/symphony-bundle-wheel-NPTLx4/sync-project/wayfinder` | evaluator_owned | installed-wheel sync resolves and copies all three optional overlays with an empty home |
| `env HOME=/tmp/symphony-bundle-wheel-NPTLx4/empty-home /opt/anaconda3/bin/python -I -c 'import sys; sys.path.insert(0, "/tmp/symphony-bundle-wheel-NPTLx4/oh_my_symphony-0.13.0-py3-none-any.whl"); from pathlib import Path; import symphony; from symphony.cli.factory import _install_skills; assert ".whl/" in symphony.__file__; _install_skills(Path("/tmp/symphony-bundle-wheel-NPTLx4/zip-smoke"), {"supergoal", "superdesign", "superpm", "superqa"}, force=False)'` | evaluator_owned | zipped `importlib.resources` copies all standard skills without a filesystem assumption |
| `ruff check src/symphony/cli/factory.py tests/factory` | frozen_repo | static quality |
| `/opt/anaconda3/bin/python -m pyright src/symphony/cli/factory.py` | evaluator_owned | resource types remain statically valid |
| `git diff --check` | frozen_repo | patch hygiene |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_bundled_skills.py tests/factory/test_cli.py tests/factory/test_lifecycle.py` | agent_detected | package-first init/sync, custom fallback, project-copy validation, and factory lifecycle behavior; 34 passed |
| `/opt/anaconda3/bin/python -m ruff check src/symphony/cli/factory.py tests/factory/test_bundled_skills.py tests/factory/test_cli.py tests/factory/test_lifecycle.py` | agent_detected | focused Python static quality; passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_bundled_skills.py -k 'force_replaces or broken_project or preflights_broken'` | agent_detected | non-directory and broken-symlink destinations follow preserve/force rules; 5 passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_bundled_skills.py tests/factory/test_cli.py tests/factory/test_lifecycle.py` | agent_detected | bundled-resource, CLI, lifecycle, and edge regressions; 39 passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory` | agent_detected | full factory regression suite after edge fixes; 83 passed |
| `/opt/anaconda3/bin/python -m build --wheel --outdir /tmp/symphony-edge-wheel` | agent_detected | wheel built; inventory script found all 72 non-cache bundle source files and no extras |
| `env HOME=/tmp/symphony-edge-wheel/empty-home /opt/anaconda3/bin/python -I -c 'import sys,tempfile; sys.path.insert(0, "/tmp/symphony-edge-wheel/oh_my_symphony-0.13.0-py3-none-any.whl"); from pathlib import Path; from symphony.cli.factory import _install_skills; target=Path(tempfile.mkdtemp(prefix="zip-edge-")); d=target/"skills/supergoal"; d.parent.mkdir(parents=True); d.write_text("old"); _install_skills(target, {"supergoal", "superdesign", "superpm", "superqa"}, force=True); assert (target/"skills/superdesign/templates/anti-slop-gate.mjs").is_file(); assert (target/"skills/superqa/superqa_tui/engine.py").is_file()'` | agent_detected | zipped `Traversable` nested copy plus forced file-destination replacement; passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pyright src/symphony/cli/factory.py` | agent_detected | resource and cleanup types; 0 errors |

### R-LOOP iteration 1 implementation - 2026-07-13

- RED: recursive reference closure, full pinned preservation, public custom
  skill sync, path safety, and sync recovery produced 8 expected failures.
- GREEN: the factory-specific Supergoal router and full SuperPM router closure
  are present in package resources and generated copies; custom names are
  normalized and path-safe; `factory sync --force` is a real targeted recovery
  command; preservation validates every pinned bundled file.
- Redistribution inventory is sourced from the four pinned local checkouts and
  their license/credit files. `THIRD_PARTY_NOTICES.md` explicitly retains legal
  uncertainty; this implementation does not claim legal approval.

| Command | Source | Proves |
|---|---|---|
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_bundled_skills.py tests/factory/test_wayfinder.py tests/factory/test_cli.py -k 'recursive_reference or complete_pinned or path_safe or path_unsafe or custom_skill_from_local or force_recovers'` | agent_detected | RED before implementation: 8 failed; GREEN after implementation: 8 passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory/test_bundled_skills.py tests/factory/test_wayfinder.py tests/factory/test_cli.py` | agent_detected | focused bundled runtime, Wayfinder parser, and public CLI behavior; 53 passed |
| `PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/factory` | agent_detected | full factory regression suite; 92 passed |
| `/opt/anaconda3/bin/python -m ruff check src/symphony/cli/factory.py src/symphony/factory/wayfinder.py tests/factory` | agent_detected | static quality; passed |
| `/opt/anaconda3/bin/python -m build --wheel --outdir /tmp/symphony-bundled-skills-rloop5` | agent_detected | distributable wheel built successfully |
| `/opt/anaconda3/bin/python -c '...compare source bundle files with wheel zip members...'` | agent_detected | all 153 non-cache bundle files are present in the wheel; no missing or extra bundle files |
| `HOME=/tmp/symphony-wheel-rloop-empty-home /opt/anaconda3/bin/python -I -c '...load the wheel zip, factory init, then install all four standard skills...'` | agent_detected | zipped `importlib.resources` factory init and standard-skill copy with an empty home; deep Supergoal and SuperPM assets present |
| `git diff --check` | agent_detected | patch whitespace hygiene after final manifest-helper edit; passed |

### R-LOOP iteration 2 implementation - 2026-07-13

- RED: executable-mode, license inventory, SuperQA README, Superdesign docs,
  and extended recursive-reference tests produced 8 expected failures. The
  extended scanner also found a real missing `scripts/shoot.sh` reference in
  the shipped SuperPM storyboard HTML.
- GREEN: all source and generated shell assets are executable; wheel metadata
  records `-rwxr-xr-x`; editable-installed and direct-zipimport copies execute
  Superdesign gates directly and return their expected usage status rather
  than exit 126.
- SuperQA's pinned package metadata now has its required README. English,
  Korean, and operator factory docs state Node.js 18+, `@playwright/cli`,
  `playwright-cli install --skills`, and Chrome/Chromium requirements.
- Recursive closure covers `.cmd`, `.html`, `.js`, `.json`, `.md`, `.mjs`,
  `.py`, `.sh`, `.tcss`, and `.toml`; SuperPM now ships the referenced adapted
  storyboard screenshot helper.
- Redistribution inventory covers the four pinned skills plus taste-skill,
  impeccable, last30days-skill, Agent-Reach, storyboard-spec, sibling reuse,
  and reference-only tools. Available authoritative license texts are shipped.
  The stitch-landing-skill license fetch remained unavailable and is called
  out as a residual uncertainty. This is not legal approval.

| Command | Source | Proves |
|---|---|---|
| `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory/test_bundled_skills.py` | agent_detected | iteration-2 RED: 8 failed, 23 passed; GREEN: 30 passed |
| `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory` | agent_detected | full factory regression suite; 108 passed |
| `/opt/anaconda3/bin/ruff check src/symphony/cli/factory.py tests/factory` | agent_detected | static quality; passed |
| `/opt/anaconda3/bin/python -m build --wheel --outdir /private/tmp/symphony-autonomous-factory-wheel` | agent_detected | distributable wheel built successfully |
| `zipinfo -l /private/tmp/symphony-autonomous-factory-wheel/oh_my_symphony-0.13.0-py3-none-any.whl` | agent_detected | all bundled shell assets have executable wheel modes; README and third-party license texts are present |
| `pip install --no-deps --target <site> <wheel>; PYTHONPATH=<site> ... factory init + _install_skills` | agent_detected | installed wheel copies all four skills; direct preflight execution returns expected usage status 2 |
| `PYTHONPATH=<wheel> ... factory init + _install_skills` | agent_detected | module loads directly from `.whl`; zipped resources copy all four skills with executable modes; direct render-gate execution returns status 2 |
| `git diff --check` | agent_detected | patch whitespace hygiene; passed |

### R-LOOP iteration 3 implementation - 2026-07-13

- RED: the attribution inventory test failed because the authoritative Stitch
  MIT notice, pinned revision, and shipped-file relationship were absent.
- GREEN: Stitch is pinned at
  `4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c`; its byte-identical cskwork MIT
  notice is retained; notices and source attribution now identify the shipped
  `assets.md` and `web.md` inspiration without the stale unavailable-license
  claim. The general no-legal-approval disclaimer remains.

| Command | Source | Proves |
|---|---|---|
| `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory/test_bundled_skills.py -k 'third_party_inventory'` | agent_detected | attribution RED before implementation and pinned Stitch source/license GREEN after implementation |
| `PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/factory/test_bundled_skills.py -k 'third_party or recursive_reference or package_resources'` | agent_detected | focused attribution and bundle-closure regression |
| `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m ruff check tests/factory/test_bundled_skills.py` | agent_detected | touched Python static quality |
| `git diff --check` | agent_detected | patch whitespace hygiene |

## QA

Tool: none
UI-tier: not applicable
DB: not applicable

## Reproduction Fidelity

- Fidelity level: exact
- Residual risk from data gap: none
- Post-deploy confirmation plan: install the built wheel into an isolated environment and run factory init with an empty home.

## Residual Risk

- Implementer proof is green; independent final verification is intentionally
  left to the parent verifier.
- Legal inventory remains engineering evidence, not legal approval. Public
  release should independently verify unversioned lineage statements with
  legal review.
