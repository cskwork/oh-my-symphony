# oh-my-symphony-release

Optional app release contracts for [oh-my-symphony](../../README.md)
(ADR 0002, `docs/adr/0002-machine-enforced-app-release-contracts.md`):
host-owned release gates for `app-release` tickets, repair cycles, finalizer
guards and `symphony release check`. The core `oh-my-symphony` package runs
without it.

Not on PyPI. Install it next to core from a checkout of this repository; it
pins the same core version (`oh-my-symphony==0.29.0`):

```bash
python -m pip install ./packages/release
# editable:
python -m pip install -e . -e packages/release
# into a uv tool install:
uv tool install --force . --with ./packages/release
```

Install it before upgrading a board that already has release tickets.
Usage: [Machine gate for application releases](../../docs/reference.md#machine-gate-for-application-releases).

Without this package, core fails closed for release work: a ticket that
carries the `app-release`, `app-release-finalizer` or `release-cycle-verifier`
label, or durable release authority in `state.db`, is refused and moved to
`Blocked` with the install hint; `symphony doctor` fails on such a board and
`symphony release check` exits 2. The release tables stay in core, so a
`state.db` remains valid with or without the package.

Licensed under Apache-2.0, like the core distribution; see `LICENSE` and
`NOTICE` at the repository root.
