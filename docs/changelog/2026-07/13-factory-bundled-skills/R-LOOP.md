# R-LOOP - verifier -> implementer loop channel

## 2026-07-13T00:00 iteration 1

- [ ] GOAL criterion 1: generated Supergoal must actually support the WAYFINDER prompt; current bundle is missing `reference/wayfinder.md`, delivery-gate files, and other paths advertised by its router.
- [ ] GOAL criterion 2: standard overlays must be usable runtimes; SuperPM currently advertises 13 missing route files. Add a recursive reference-integrity test for bundled and generated copies instead of a hand-picked shallow list.
- [ ] GOAL criterion 3: custom skills must work through public `factory sync`; Wayfinder currently rejects every non-standard skill before local fallback. Permit path-safe custom names and prove end to end.
- [ ] Recovery behavior: incomplete generated skills tell users to pass a nonexistent sync `--force`. Add a truthful safe recovery path and test it.
- [ ] Preservation behavior: validate existing customized standard skills against the complete pinned runtime manifest so incomplete routers are not silently preserved.
- [ ] Redistribution: enumerate relevant third-party derivative sources and include applicable upstream license notices; record any remaining legal uncertainty honestly.

Regression: the earlier 83-test factory suite remains green, but it did not cover semantic reference closure or public custom-skill routing.
Next: expand the pinned bundle to the complete advertised runtime closure (or narrow purpose-built routers), add recursive closure and public CLI tests, fix recovery messaging/option, update notices, then rerun focused tests and wheel smokes.

## 2026-07-13T00:01 iteration 2

- [ ] Generated shell gates must be executable: current source/wheel/generated `.sh` assets are `0644`, and direct Superdesign gate execution fails with exit 126. Preserve executable modes in source/wheel and explicitly set safe executable bits when copying package resources.
- [ ] Redistribution inventory must enumerate the actual upstream/adapted sources named by shipped files and retain applicable license notices where available. Remove unrelated derivative files from the factory-scoped Supergoal closure if they are not required. Keep explicit residual legal uncertainty; do not claim legal approval.
- [ ] Bundled SuperQA must support its advertised editable-install route or stop advertising it; its `pyproject.toml` requires a missing `README.md`.
- [ ] Beginner docs must state Superdesign's actual Node 18+, `@playwright/cli`, and browser setup prerequisites alongside SuperQA requirements.
- [ ] Recursive reference tests must scan every shipped referenced asset extension, including `.html`, `.toml`, `.tcss`, and `.cmd`.

Regression: iteration 1 closed the incomplete Supergoal/SuperPM closure, public custom sync, force recovery, and shallow preservation defects; 92 factory tests remain green.
Next: add red tests for modes/install/docs/reference extensions, fix the five listed gaps, then rerun wheel execution and inventory checks.

## 2026-07-13T00:02 iteration 3

- [ ] Correct the Stitch attribution record: its authoritative MIT license is available, byte-matches the included cskwork MIT text, and current upstream commit is `4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c`. Remove the stale unavailable-license claim, pin the manifest entry, and make `sources.md` describe the actual shipped `assets.md`/`web.md` inspiration rather than a non-shipped landing page.
- [ ] Replace the test that codifies the stale uncertainty with assertions for the pinned source and retained MIT notice.

Regression: all functional and packaging checks are green (108 factory tests; 160/160 wheel inventory; installed and zipimport execution); only factual attribution evidence remains red.
Next: patch notices, manifest, sources, and test; run focused attribution/closure tests plus diff check.
