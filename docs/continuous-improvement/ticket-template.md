# Continuous improvement ticket template

Every ticket the heartbeat registers uses this body template. It is filled
in by the registrar module (see `docs/architecture.md`, "Continuous
improvement heartbeat") and created through
`FileBoardTracker.create_with_next_identifier(prefix="CI")` for file-tracker
boards. Trackers without a safe creation contract report
`unsupported_tracker` instead of receiving a ticket — see
`docs/continuous-improvement/rubric.md` ("Tracker support matrix").

## Fields

- **Rubric item** — which baseline check produced this finding (for example
  `pytest`, `ruff`, `pyright`, a named browser or DB probe).
- **Failing command / check name** — the exact predefined check that failed
  (for example `python -m pytest -q`).
- **Normalized failure summary** — a short, stable description of the
  failure, independent of run-to-run noise (line numbers that shift, temp
  paths, timestamps). Used as fingerprint input.
- **Evidence excerpt** — captured output relevant to the failure, capped in
  size, with obvious secrets (tokens, keys, credentials) redacted.
- **Expected behavior** — what "passed" would look like for this check.
- **Proposed fix boundary** — the area of the codebase a normal worker
  should look at, phrased as a boundary, not a prescribed diff. The
  heartbeat never edits code and never invents implementation details it
  cannot verify.
- **Verification commands** — the exact commands a worker should re-run to
  confirm the fix (usually the same command that failed, plus the full
  rubric check it belongs to).
- **CI Fingerprint** — a stable hash used for de-duplication across runs.

## Body template

```markdown
## Continuous improvement finding

- Rubric item: <rubric item name>
- Failing check: `<exact command>`
- Baseline: branch `<branch>` @ `<sha>`

### Failure summary

<normalized failure summary — one or two sentences>

### Evidence

```
<capped evidence excerpt, secrets redacted>
```

### Expected behavior

<what "passed" looks like for this check>

### Proposed fix boundary

<area / module the fix should stay within; no prescribed diff>

### Verification

Re-run before closing:

```
<verification command(s)>
```

CI Fingerprint: <hash>
```

## Fingerprint rule

The fingerprint is a stable hash (e.g. SHA-256, truncated for readability)
computed over:

1. the rubric item name,
2. the failing check's command string,
3. the normalized failure summary (with run-to-run noise stripped: line
   numbers that shift between runs, absolute temp paths, timestamps, and
   process IDs are excluded from the hash input).

The fingerprint must **not** include the evidence excerpt, the current SHA,
or the current timestamp — those change on every run even when the
underlying defect is the same, which would defeat de-duplication.

The registrar searches active tickets for a `CI Fingerprint: <hash>` line
exactly matching the computed value before creating a new ticket. See
`docs/continuous-improvement/rubric.md` ("De-duplication") for the full
create/skip/append decision.
