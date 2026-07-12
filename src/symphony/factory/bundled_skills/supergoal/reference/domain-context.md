# Domain context - repo-local knowledge without prompt bloat

Domain context is a durable, searchable knowledge pack at the target repo root. It stays separate from
the run vault and model memory.

- Vault: per-run evidence and decisions in `docs/changelog/<YYYY-MM>/<DD-topic>/` (one folder per
  run: `GOAL.md`, `PLAN.md`, `QA.md`, `R-LOOP.md`, `run-state.json`, `qa/` evidence, and `Z-<date>.md`
  on completion).
- Domain context: local reusable domain facts, stored by default in `.domain-agent/`.
- Model memory: optional reminders only; never the source of truth for a fix.

Current docs/code always win. Treat `.domain-agent/` as a routing index for code, tests, terms, and
flows.

Load for GREENFIELD domain fit, DEBUG proof/root-cause routing, and LEGACY affected-code maps. Skip for
pure TEACH unless the user asks about repo domain.

## First-run setup

Check for `<knowledgePath>/config.json` (default `.domain-agent` at the target repo root). If it does
not exist, ask one concise question:

```text
I do not see domain-agent knowledge for this repo. Should I create it at `.domain-agent/`, or use another path?
```

Then create the chosen folder from `templates/domain-agent/`; write `config.json` (repo, path, `language`,
dates); Add the chosen path to the repo root `.gitignore` if absent; record the path in the run vault
`PLAN.md`. Before Build, only knowledge scaffolding, `.gitignore`, and vault setup may write. If the
user declines storage, use an ephemeral Domain Brief in the vault.

Set `language` to the repo's docs language (SKILL.md), or `mixed` when no single one dominates.

## File contract

Use these folder/file names even when the user chooses a different root path:

- `config.json` - version, repo, language, knowledgePath, createdAt, lastUpdated.
- `index.md` - router only: systems, feature areas, flow files, search keys; short enough to read first.
- `glossary.md` - domain terms as used in THIS repo (not generic industry meaning); pick one
  canonical term per concept and list avoided aliases.
- `invariants.md` - business/safety rules; each with a source, confidence, and one way to verify it.
- `code-map.md` - stable entry points (routes, services, repositories, DTOs, migrations, queues,
  jobs, external clients); a map, not an architecture document.
- `test-map.md` - exact commands: targeted/integration tests, build/type/lint, fixtures, env
  prerequisites.
- `flows/*.md` - one source-grounded feature/business flow per file, named what tickets search for
  (`auth-session.md`, `checkout-refund.md`).
- `decisions/*.md` - durable domain decisions only; prefer the repo's own decision system; save here
  only when the choice is hard to reverse, surprising without context, and a real tradeoff.
- `tickets/*.md` - reusable postmortems only (a stable failure mode, invariant, or repro pattern);
  not every ticket.
- `qa/*.md` - Reusable QA suites from QA-ONLY runs (`reference/qa-only.md`): Impact Matrix, scenario
  list, `Comparison:` type, named DB checks, saved baseline values, reproduction notes, coverage,
  uncovered areas, residual risks, and the re-run command / Playwright spec path; indexed in
  `index.md` under `## QA Suites`. No secrets, raw rows, or PII.
- `qa/nav-map.md` - one browser navigation map: entry/auth, `screen -> URL`, popup/new-tab handling,
  stable selectors, real API calls. Load before browser QA/observe and self-heal on drift
  (`reference/qa.md` "Navigation map"). No secrets, raw rows, or PII.

## Retrieval loop (every run; capped)

Goal: a small working brief, not a domain encyclopedia.

1. Read `config.json`, then `index.md` (keep it as the router).
2. Extract ticket keywords: routes, errors, domain terms, DTO/entity names, external systems,
   expected behavior.
3. Search the knowledge folder with literal terms first.
4. Select the smallest relevant set - usually `glossary.md`, `invariants.md`, `test-map.md`, and one
   `flows/*.md`.
5. Verify load-bearing saved facts against current code with structural tools (`codegraph_context`,
   `codegraph_explore`, call paths) and find current entry points.
6. Write a compact `## Domain Brief` to the run vault `PLAN.md` (beside the grounding ledger).

Hard caps: Select at most five domain files for a phase unless the human approves more. If more seem
relevant, route through `index.md` again and name the uncertainty. Prefer one `flows/*.md`; two means the
ticket may span domains. Keep the Domain Brief under 80 lines. Split broad files by feature area and
update `index.md`.

## Freshness loop

Do not refresh the whole pack every run; verify selected facts against current code.

- Light refresh threshold: 5 days after `config.json.lastUpdated` - run CodeGraph status for affected
  repos, check docs/source changed since `lastUpdated`, re-read only selected + cited files, patch
  stale entries surgically.
- Full review threshold: 30 days after `config.json.lastUpdated`, major repo changes, or repeated
  stale-context misses - review router, code map, test map, invariants, high-traffic flows; split broad
  files instead of appending context.
- Triggered refresh: saved knowledge conflicts with current code, or a run proves a stable new
  routing fact. `qa/nav-map.md` is verified against the live site on every browser run; correct a
  drifted row (selector, route, popup target, API path) in place as a triggered refresh, not a
  whole-app re-crawl.

## Domain Brief format

```md
## Domain Brief

- Knowledge path: `.domain-agent/`
- Selected knowledge files: `index.md`, `flows/<name>.md`, `test-map.md`
- Stable terms: <term -> meaning>
- Terminology conflicts: <conflicts found, or 'none'>
- Invariants: <rules that affect this change>
- Current-code verification: <symbols/routes/files checked now>
- Entry points: <route/method/class/file>
- Test commands: <targeted proof commands>
- Gaps: <unknowns, or 'none'>
```

By default, later phases receive only the Domain Brief.

## Saving loop

Save new knowledge only when ALL hold: stable across future tickets; verified against current
code/tests/docs; no secrets/tokens/raw logs/customer data/PII; improves future routing, reproduction,
planning, or verification. Ticket-specific evidence stays in the vault.

Do not save: Raw investigation transcripts, long code excerpts, complete tickets, temporary
hypotheses, or facts one current-code lookup finds just as fast.

Append surgically: term -> `glossary.md`; invariant -> `invariants.md`; entry point -> `code-map.md`;
command -> `test-map.md`; flow -> `flows/<flow>.md`; stable failure pattern ->
`tickets/<short-slug>.md`; reusable QA suite -> `qa/<suite>.md` (indexed under `## QA Suites`);
browser navigation fact (route, popup/new-tab, stable selector, `screen -> API`) -> `qa/nav-map.md`. After
each save, update `index.md` search keys and `config.json.lastUpdated`; keep entries short and split
files that accumulate unrelated content.

Terminology updates: capture a stable routing term; challenge vague/overloaded words against selected
files, current docs, and glossary before adding a synonym; keep implementation details in
`code-map.md`/`flows/*.md`/`test-map.md`, not glossary; record wording conflicts in the Domain Brief.

Decision updates: prefer the repo's existing decision records; use `decisions/*.md` only for choices
hard to reverse, surprising without context, and made between real alternatives - never ordinary
implementation notes.

## Trust rules

1. Saved knowledge can be stale. Verify load-bearing facts against current code before the plan
   freezes.
2. Saved knowledge is local and ignored by default. Do not assume another machine has it.
3. Do not commit `.domain-agent/` unless the user explicitly asks for a sanitized publication path.
4. `.gitignore` must contain the chosen knowledge path before local knowledge is written.
5. If saved context conflicts with code, write the conflict in the Domain Brief and let current code
   drive the plan.
