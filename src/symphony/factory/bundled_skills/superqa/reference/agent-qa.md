# Agent QA procedure (EXPLORE-QA / REGRESSION)

The agent has two tools that must not be confused:

- **`playwright-cli`** - interactive exploration. Token-efficient snapshots, refs, one
  action per command. Use it to LEARN the site.
- **`superqa` engine** (`python3 -m superqa_tui ...` from the skill root) - deterministic
  replay of scenario YAMLs with side-effect capture and reports. Use it to PROVE the site.

Never hand-drive a regression with playwright-cli when scenarios exist; never generate
scenarios blind without exploring first.

## 0. Setup check

```bash
python3 -m superqa_tui doctor      # one command checks everything, prints fixes
```

## 1. Ground before driving

- Read `~/.superqa/sites/<site>/rules.md` (entry URL, login type, known popups, quirks).
- `python3 -m superqa_tui vars list <site>` - if login is needed and username/password
  are missing, ask the user once and store with `vars set`. Never echo the password.

## 2. Explore with playwright-cli

```bash
playwright-cli open <url>
playwright-cli snapshot                  # element refs
playwright-cli click <ref>              # follow the flow a user would
playwright-cli tab-list                  # after any click that may open a tab
playwright-cli tab-select <i>           # popups/new tabs: switch, re-snapshot
playwright-cli console                   # errors so far
playwright-cli requests | head -50       # API surface
playwright-cli screenshot --filename=<evidence.png>
playwright-cli close
```

Record in the site rules file as you go: entry flow, login steps, which clicks open
new tabs/popups, dialogs that appear, menu -> URL map, unstable selectors.

## 3. Generate scenario cases

Follow `reference/scenario-gen.md`. Write YAML files (schema:
`reference/scenario-format.md`) into `~/.superqa/scenarios/<site>/`. Descriptions in the
user's language - a non-developer must understand every step.

## 4. Run deterministically

```bash
cd <skill-root>
python3 -m superqa_tui run --all --site <site> --headless   # regression sweep
python3 -m superqa_tui run <scenario-name> --headless        # one case
```

Exit code 0 = all pass. Each run prints its `report.html` path.

## 5. REGRESSION mode (feature finished -> verify)

When a developer says a backend/frontend feature is done:

1. `python3 -m superqa_tui list` - find the site's existing cases.
2. Run them all (`run --all --site <site> --headless`). This is the baseline sweep.
   If visual baselines exist (`superqa baseline`), layout changes surface as
   `visual_change` effects with diff images; failing steps save `trace.zip`.
3. If the feature added new behavior, explore only the changed screens (step 2) and add
   new scenario cases for them, then run again.
4. Read the automatic diff each run prints ("지난 실행과 비교"): new failures and
   newly appeared side-effect types are regressions - report them first.
   `superqa report list` shows the full history (also written to
   `~/.superqa/reports/index.html`).

## 6. Report back

Summarize in the user's language: overall pass/fail, per-scenario one-liners, side
effects triaged (bug / known / environment), report paths. Update
`~/.superqa/sites/<site>/rules.md` with anything you had to discover the hard way.
