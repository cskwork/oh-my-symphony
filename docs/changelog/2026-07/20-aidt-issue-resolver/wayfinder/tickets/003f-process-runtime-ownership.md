# 003f - Process runtime ownership

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003e

Unblocks: 003g, 003h

## Goal and interface contract

Own the process-lifetime seam exposed by `AidtWorktreeRuntime`: publish immutable configuration generations, admit
one candidate, delegate prepare/guard/remove while retaining durable path ownership, and expose bounded health.
Callers use dispositions and capabilities; manifest/provisioner imports remain lazy when the profile is disabled.

## Historical file ownership

- `src/symphony/aidt_worktree/runtime.py`
- `src/symphony/aidt_worktree/__init__.py`
- `tests/test_aidt_worktree_runtime.py`

The executed three-file slice exceeds 500 net lines and is therefore a historical umbrella, not a Build ticket.

## Acceptance and proof

- Only `UNMANAGED` permits generic fallback; handled, preserved, and owned-error outcomes are final and bounded.
- Generation/admission/guard/authorization identities match exactly; retries and cleanup cannot transfer ownership
  across identifiers, paths, managers, or generations.
- Disabled imports remain inert and health contains only bounded counters/status/ref/category/time fields.
- Proof surfaces: `tests/test_aidt_worktree_runtime.py` and `R-LOOP.md` R4/runtime matrices.

## Scope boundary

Does not own atomic Core/manager publication, generic workspace code, ticket polling, backend construction, or docs.
