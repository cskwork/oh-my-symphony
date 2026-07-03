# Requirements: Human Review Confirmation Gate

## Introduction

Symphony's production pipeline intentionally stops at `Human Review` after
Learn. Agents must not mark a ticket `Done`; a human confirms that final state
after checking QA evidence, browser proof, wiki updates, and merge status.

The current service web board does not expose a `Confirm Done` action on Human
Review cards, even though the standalone board viewer and TUI already have this
concept. The result is a bad operator state: the run did the right thing by
asking for human confirmation, but the board used during a service run does not
provide the matching control.

RERUN-204 also exposed a contract-sensitive evidence issue: Verify table cells
can point to files that exist on disk but still fail the contract when cited in
the wrong coordinate system. The same spec covers that guard because both
issues affect the final run handoff.

## Glossary

| Term | Definition |
|---|---|
| Human Review | Terminal holding state where agents hand work to the operator. |
| Confirm Done | Explicit operator action that moves a Human Review ticket to `Done`. |
| Service web board | The SPA served by the running Symphony service from `src/symphony/web/static`. |
| Standalone board viewer | The separate `tools/board-viewer` app. It already has a Human Review confirm path. |
| Docs root | The per-ticket evidence root, for example `docs/RERUN-204/`. |
| Evidence cell | A table cell in Verify sections such as `## Security Audit` or `## AC Scorecard` that the contract parser treats as proof path input. |

## Requirements

### Requirement 1: Service Board Confirm Action

**User story:** As an operator, I want the service web board to show a
`Confirm Done` button on Human Review cards, so that a valid agent handoff can
be completed without editing Markdown by hand.

**Acceptance criteria:**

1.1 WHEN a file-board ticket is in `Human Review` THEN the service web board
SHALL render an explicit `Confirm Done` action on that card.
1.2 WHEN the board is in active scope and Human Review cards are rendered in
the terminal section THEN the same action SHALL be visible there.
1.3 WHEN the board is in all-columns scope THEN Human Review cards SHALL expose
the same action.
1.4 WHEN a ticket is not in `Human Review` THEN the service web board SHALL NOT
show `Confirm Done`.
1.5 WHEN the board is read-only because the tracker is not `file` THEN the
service web board SHALL NOT show a mutating confirm control.

### Requirement 2: Explicit Confirm API

**User story:** As an operator, I want confirmation to use a narrow API, so
that `Done` records mean "a human confirmed this Human Review handoff" rather
than "some generic state patch moved the card".

**Acceptance criteria:**

2.1 WHEN `POST /api/v1/issues/{identifier}/confirm-done` is called for a file
ticket currently in `Human Review` THEN the API SHALL move it to `Done`.
2.2 WHEN the ticket is missing THEN the API SHALL return 404 with a readable
error code.
2.3 WHEN the ticket is in any other state THEN the API SHALL return 409 and
leave the ticket unchanged.
2.4 WHEN confirmation succeeds THEN the API SHALL record the stats transition
from `human review` to `done` and request an orchestrator refresh.
2.5 WHEN confirmation fails THEN the board SHALL keep the card in place and
show the API error without pretending the ticket is Done.

### Requirement 3: Evidence Path Contract Guard

**User story:** As a worker or reviewer, I want Verify evidence path rules to
be visible and testable, so that a future run does not rewind because evidence
exists but is cited in the wrong coordinate system.

**Acceptance criteria:**

3.1 WHEN Verify evaluates `## Security Audit` evidence cells THEN paths SHALL
be interpreted relative to the ticket docs root.
3.2 WHEN Verify evaluates `## AC Scorecard` evidence cells THEN paths SHALL be
interpreted relative to the ticket docs root.
3.3 WHEN source-file proof is needed THEN table cells SHALL cite a docs-root
artefact such as `qa/details.md`, and that artefact may contain the source
anchor.
3.4 WHEN the rule is documented THEN `docs/llm-wiki/INDEX.md` SHALL include a
`verify-evidence-contract` entry that points to the host wiki page.
3.5 WHEN a regression fixture uses a repo-root evidence path like
`docs/RERUN-204/qa/qa.log` inside a docs-root evidence cell THEN the contract
test SHALL fail.

### Requirement 4: Run-Handoff Regression Proof

**User story:** As a maintainer, I want tests that exercise the real service
board path, so that the next live Symphony run cannot end in Human Review with
no visible confirmation action.

**Acceptance criteria:**

4.1 WHEN static service-board contract tests run THEN they SHALL assert the
confirm action label, API client method, and Human Review state guard.
4.2 WHEN web API tests run THEN they SHALL prove the confirm route moves only
Human Review tickets to Done.
4.3 WHEN browser E2E is enabled THEN it SHALL create or seed a Human Review
card, click `Confirm Done`, and assert the card appears as Done.
4.4 WHEN the service-board confirm action is absent THEN at least one default
test SHALL fail without requiring a live browser.

### Requirement 5: Pipeline Compatibility

**User story:** As a maintainer, I want the final gate fixed without weakening
the production pipeline.

**Acceptance criteria:**

5.1 The Learn prompt SHALL continue to say that agents set `Human Review`, not
`Done`.
5.2 The Done prompt SHALL continue to require human confirmation before Done
handling.
5.3 The standalone board viewer and TUI confirm behaviors SHALL remain
compatible with the service board.
5.4 Generic issue editing SHALL remain backward-compatible unless a separate
breaking-change spec explicitly removes direct state edits.

## Non-Functional Requirements

- **Safety:** The confirm API must be state-gated and idempotent for already
  Done behavior only if that is explicitly tested; otherwise wrong-state
  requests return 409.
- **Accessibility:** The button must have readable text or an aria label that
  includes `Confirm Done`.
- **Compatibility:** API additions are additive. Existing board read
  endpoints, issue patching, TUI hotkeys, and standalone viewer routes keep
  working.
- **Traceability:** Every implementation decision and rejected alternative is
  recorded in the date changelog.

## Out of Scope

- Letting agents mark tickets Done.
- Replacing Markdown tickets as the human source of truth.
- Rebuilding the web board framework.
- Changing active or terminal workflow states.
- Changing release numbering or branch merge policy.
