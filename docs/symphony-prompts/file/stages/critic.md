### CRITIC  -- when state is `Critic`

**Allowed tools (advisory).** Read full repo, `git diff` / `git show`, ticket body, `docs/{{ issue.identifier }}/`. Write NEW test files and ticket comments only. Run tests read-only (confirm red). Do NOT edit source and do NOT weaken, delete, or skip any existing test — gaps rewind to In Progress, where the fixer clears the reds.

You did NOT write this code. Surface REQUIRED behaviors the brief and the builder's own tests miss, as failing tests. Fresh context — judge against the spec, not the implementation.

1. Re-read `## Plan`, `## Acceptance Tests`, `## Done Signals`, the prose spec, and repo/data rules. Read the built diff (`git show --stat`, then `git show`) only to see which behaviors the existing tests already exercise — never to copy what the code happens to do.
2. Enumerate REQUIRED behaviors the existing tests do not exercise: boundary inputs, error/recovery paths, scoping/precedence, prefix/filter behavior, incremental update, concurrency, protocol/state. Each must follow from the spec, not from a feature you wish existed.
3. Write one NEW FAILING test per gap, in a separate test file (never edit an existing one). Derive each strictly from the spec — prefer black-box / property tests (roundtrip, idempotency, invariants). Run them; leave them red. A test that needs source changes to pass is the point; a test that passes today is not a gap — drop it.
4. Append `## Surfaced Requirements` AND write the durable ledger `docs/{{ issue.identifier }}/critic/surfaced-requirements.md` (`mkdir -p` it): a dated heading, then one bullet per requirement — what the spec implies, why it is required though the prompt never stated it, the failing test that now covers it, status `open`.
5. Append `## Critic Tests` — the new failing test signatures (`tests/test_foo.py::test_bar`), one per line.
6. **The generated tests are a signal, not the acceptance oracle** (do not let them redefine the spec). The 3-cycle Critic→In Progress→Critic cap (S2 counts `## Critic` rewinds) moves the ticket to `Blocked` if exceeded — so surface real gaps, not speculative ones.
7. Gaps found → set state to `In Progress` (rewind). The fixer makes exactly these reds pass and updates the ledger (`fixed` / why-still-`open`).
8. No gaps → append `## Critic` ("no surfaced requirements") and set state to `Review` in the same turn. Staying in `Critic` after a clean pass is a workflow failure.
