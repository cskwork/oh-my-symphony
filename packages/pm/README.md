# oh-my-symphony-pm

Optional PM Workspace for [oh-my-symphony](../../README.md): the Artifacts
page, `symphony pm demo`, `/api/v1/pm/*` routes and the `pm:` keys in
`WORKFLOW.md`. The core `oh-my-symphony` package runs without it.

Not on PyPI. Install it next to core from a checkout of this repository; it
pins the same core version (`oh-my-symphony==0.29.0`):

```bash
python -m pip install ./packages/pm
# editable, with workbook (XLSX) exchange:
python -m pip install -e ".[pm-exchange]" -e packages/pm
# into a uv tool install:
uv tool install --force . --with ./packages/pm
```

Without this package, `symphony pm` prints the install hint and exits 2, the
web board hides the Artifacts entry, and enabling a `pm:` capability is a
config error.

The Python package keeps the `symphony.pm` import path. Browser assets live in
`src/symphony/pm/static/` and are served at `/static/pm/`. Rebuild the vendored
browser bundle from this directory with `npm ci && npm run build:pm-assets`.
The demo and approvals need Node.js 20.19.0 or newer. Guide:
[docs/pm-workspace.md](../../docs/pm-workspace.md).

Licensed under Apache-2.0, like the core distribution; see `LICENSE` and
`NOTICE` at the repository root.
