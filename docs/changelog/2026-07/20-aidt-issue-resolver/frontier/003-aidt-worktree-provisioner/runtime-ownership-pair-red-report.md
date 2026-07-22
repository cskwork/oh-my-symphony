# Frontier 003 Runtime Ownership-Pair RED Report

Date: 2026-07-21
Scope: test-only identifier/path ownership-pair regression
Verdict: **RED AS REQUIRED / PRODUCT REPAIR REQUIRED**

## Decision and theory

Removal authority is the pair `(identifier, workspace_path)`, not either scalar independently. Once an exact durable
manifest plus ownership record proves that child A owns path A, passing path A with any other child identifier must
remain owned and fail before cleanup. A route-loader `None` for child B cannot downgrade path A to `UNMANAGED`, because
that would permit generic fallback over a durably owned AIDT path.

The regression is contained in the existing
`test_delegate_converts_post_recognition_exceptions_to_owned_error` public test through one bounded helper:

- child A (`A20-1193--viewer-api`) receives an exact ready manifest and aligned non-tombstoned ownership record;
- recognized child B (`A20-1194--viewer-api`) plus A's recorded path remains the existing mismatch control and returns
  owned `path_invalid` with zero cleanup;
- unknown canonical child B (`A20-1195--viewer-api`) has no loader entry, so the bounded loader returns `None`; A's
  recorded path must still return owned `path_invalid`, never `UNMANAGED`, with zero cleanup.

The helper checks the cleanup call list before checking disposition, so the RED result still proves no cleanup call
escaped. The bounded category is `path_invalid`, matching the already frozen identifier/path mismatch contract.

## Exact evidence

Focused runtime:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_runtime.py
1 failed, 7 passed in 1.33s
```

The recognized-other-child row passes first. The sole failure is the loader-`None` row:

```text
actual:   DelegateResult.unmanaged()
expected: DelegateResult.owned_error("path_invalid")
cleanup:  0 calls
```

Every previously repaired R1-R4 row remains green.

Static evidence:

```text
../../.venv/bin/ruff check --no-cache tests/test_aidt_worktree_runtime.py
All checks passed!

../../.venv/bin/pyright tests/test_aidt_worktree_runtime.py
0 errors, 0 warnings, 0 informations

Test AST structure scan
public_tests 8
functions 83
max_function_lines 47
max_nesting 3
over_50 []
over_nesting_4 []

No-index whitespace checks for the runtime test and this report
exit 1 with no output for each untracked file
```

The no-index exit is the expected content-difference status; empty output proves no whitespace finding.

No product, facade, manifest, provisioner, Core, or integration file was edited. No network, live repository, Git
mutation, backend, Jira action, or commit was performed.
