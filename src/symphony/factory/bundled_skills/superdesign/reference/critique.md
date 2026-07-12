# critique - CRITIQUE mode

Review an existing design. No design change. Output a findings report.

## Input

Code (HTML/JSX/CSS), a screenshot, or a URL. For a URL or self-contained HTML, render it with `playwright-cli` (`reference/playwright-cli.md`) to judge the painted page; for a URL also fetch source for the detector. A screenshot alone cannot render - eyeball it against the checklists and say so (the deterministic gate needs source to scan).

## Run

1. **Detector (deterministic)**: run `templates/anti-slop-gate.mjs <source>` and `templates/contrast-gate.mjs <pairs.json>` (enumerate text/bg pairs from the CSS). Gate failures are blocking findings.
2. **Taste pass**: check against `taste-core.md` pre-flight (hero fit, eyebrow count, split-header, zigzag, one accent/radius/theme, real images, copy).
3. **impeccable quality pass**: `impeccable-rules.md` judgment checks (padding, touch targets, heading levels, card overuse, dated easing).
4. **Render pass (URL/HTML only)**: render with `playwright-cli` at the framings the surface targets; check hero fit, no overflow, CTA does not wrap, console clean. Screenshot-only input -> note it cannot render.
5. **Engagement pass (when the page has a conversion goal)**: `reference/engagement.md` checks - primary action obvious, useful loading/empty/error, real-data proof where the kind expects it, outcome-led CTA copy. HIGH/MEDIUM findings, not gate rules.

## Report

Findings by severity (blocking gate fails first, then HIGH/MEDIUM), each with file:line (or region) + a concrete fix. End with: overall verdict + the single highest-leverage change. Do not edit the design - hand the report back.

Done when: report delivered with verdict + highest-leverage change; the design untouched.
