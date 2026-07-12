# GOAL: beginner autonomous development factory

## Original Request

> good implement with pr and make this default in oh my symphony and do a test run with opencode agents

Approved design request:

> make plan for simplifying default template so i can use agent supergoal to wayfind spec then each ticket in wayfind goes into oh my symphony and each task completes like the supergoal process using supergoal skill and no need of alot of other things minimal and simple so user can customize as they want. easy for beginners to use easily as an autonomous software dev loop. in design auto use superdesign and in research superpm for what customer want in making spec. an autonomouse lucractive software development factory is waht i want

> and superqa to you can suggest improvement

## Spec

Implement the approved autonomous-development factory plan. Make a small
file-board profile the default Oh My Symphony beginner path while preserving
the existing production workflow as an advanced profile. Import Supergoal
Wayfinder tickets idempotently, route Supergoal/Superdesign/SuperPM/SuperQA from
explicit ticket metadata, expose beginner `symphony factory` commands, and
prove the workflow with focused/full tests plus a disposable real OpenCode-agent
run. Publish the result as a pull request into `dev`.

## Success Criteria

- [x] Default file workflow exposes only `Ready`, `Build`, and `Verify`; verify with template contract tests and Doctor.
- [x] Existing production workflow remains available as an advanced profile; verify with legacy prompt/lifecycle tests.
- [x] Wayfinder sync validates required fields, preserves dependencies, routes skills, is idempotent, and does not overwrite execution evidence; verify with parser/sync tests.
- [x] `symphony factory init|sync|start` and `board new --skills` work through public CLI tests.
- [x] Browser/UI tickets route Superdesign and SuperQA; product research/spec tickets route SuperPM; ambiguous tickets receive Supergoal only.
- [x] Verify failures rewind to Build; green proof reaches Done; blocked authority/environment reaches Blocked; verify with lifecycle tests.
- [x] README and Symphony operator skill document the beginner default and advanced customization path.
- [x] Focused tests, full pytest, Ruff, Pyright, Doctor, and diff checks pass.
- [x] A disposable real OpenCode-agent run proves dispatch and the starter state loop, or is recorded as Not proven with the exact external blocker.
- [ ] Feature branch is pushed and a reviewer-facing PR into `dev` is open.

## QA Cases

- Clean temporary repo: `factory init --agent opencode` creates the default bundle without overwriting existing files.
- Valid two-ticket Wayfinder graph: first sync creates two cards; second sync creates none; dependency remains intact.
- UI/browser ticket: generated card attaches `supergoal`, `superdesign`, and `superqa`.
- Customer-research ticket: generated card attaches `supergoal` and `superpm`.
- Failed Verify evidence: ticket returns to Build and dependent ticket remains ineligible.
- Real OpenCode run: worker starts, receives the correct stage prompt, transitions the ticket, and exits cleanly.

## Decision Gates

- Source/base: `dev` at `ee04e5d`.
- Target/integration: `dev` via PR.
- Run branch: `feat/autonomous-dev-factory`.
- Original checkout remains untouched during implementation.
