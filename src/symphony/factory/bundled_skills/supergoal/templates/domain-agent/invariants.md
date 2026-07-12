# Invariants

Rules that must not break during feature work or bug fixes.

## Rules

### `<invariant>`

- Rule: `<business or safety rule>`
- Why it matters: `<risk if broken>`
- Scenario check: `<normal, boundary, or failure case that proves the rule matters>`
- Source: `<file/doc/test/evidence>`
- Verification: `<command, code path, or check>`
- Grounding: `verified -- <probe run + result> | unverified -- <why it cannot be executed>`
- Confidence: `<high|medium|low>`
- Last verified: `<iso-date>`
