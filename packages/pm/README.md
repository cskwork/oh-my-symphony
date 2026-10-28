# oh-my-symphony-pm

Optional PM Workspace for [oh-my-symphony](../../README.md): the Artifacts
page, `symphony pm demo`, `/api/v1/pm/*` routes and the `pm:` keys in
`WORKFLOW.md`. The core `oh-my-symphony` package runs without it.

Install it next to core from a checkout of this repository:

```bash
python -m pip install -e . -e packages/pm
# workbook (XLSX) exchange:
python -m pip install -e ".[pm-exchange]" -e packages/pm
```

The Python package keeps the `symphony.pm` import path. Browser assets live in
`src/symphony/pm/static/` and are served at `/static/pm/`. Rebuild the vendored
browser bundle from this directory with `npm ci && npm run build:pm-assets`.

Licensed under Apache-2.0, like the core distribution; see `LICENSE` and
`NOTICE` at the repository root.
