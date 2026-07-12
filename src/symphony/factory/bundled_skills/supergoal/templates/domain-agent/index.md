# Domain Agent Index

Purpose: route agents to the smallest useful domain context for this repo.

## Systems

- `<system>`: `<one-line responsibility>`

## Feature Areas

- `<feature area>`: `flows/<flow>.md`

## Common Entry Points

- `<user/ticket wording>`: `<route, symbol, file, or flow>`

## Search Keys

- `<domain term>`, `<route>`, `<DTO/entity>`, `<external system>`, `<error phrase>`

## QA Suites

- `<feature/flow>`: `qa/<suite>.md` (`<functional|data-integrity|before-after|ab|env>`) — reusable QA-ONLY Impact Matrix check, re-run on request

## Navigation

- Browser navigation map: `qa/nav-map.md` — entry/auth, routes, popups/new tabs, stable selectors, `screen -> API`; load before any browser run, self-heal on drift

## Terminology Routing

- If a user/ticket term conflicts with `glossary.md`, surface the conflict before planning.
- Prefer the canonical term in saved notes; list rejected synonyms in `glossary.md` under `Avoid`.

## Update Rules

- Keep this file short.
- Use this file as the router; do not paste every detail here.
- Add stable search keys after each verified domain-context update.
- Split broad flow files instead of making one large knowledge dump.
- Do not store secrets, raw logs, customer data, or ticket-only evidence here.
- Follow `freshness.md`: light refresh after 5 days or stale evidence, full review after 30 days.
