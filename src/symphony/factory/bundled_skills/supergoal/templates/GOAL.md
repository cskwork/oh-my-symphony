# GOAL - <topic>

Single source of "done". Only the verifier ticks a box; unticking needs regression evidence.
Never delete or reword an unmet criterion - append. Mid-run discovered musts are APPENDED as new
unchecked criteria tagged `(surfaced: ...)`. Ambiguous/product-changing candidates go to
`## Decision Gates` as `ask-user`, not into criteria.

## Original Request

> <user prompt, verbatim>

## Spec

<refined detailed spec: behavior, scope, data, constraints, non-goals>

## Success Criteria

Each item is falsifiable and names its verification method.

- [ ] <behavior, falsifiable> - verify: `<command | API call | browser step | diff check>`
- [ ] <surfaced behavior> - verify: `<test file::test name>` (surfaced: implied by <reason it is
  required though the prompt never stated it>)

## QA Cases (web apps only)

Browser scenarios the QA agent drives via playwright-cli; evidence under `qa/`.

- [ ] <route + steps + expected> - evidence: `qa/<file>`

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix / no-op / ask-user | open / resolved |  |  |  |
