---
name: superdesign
description: superdesign - web/mobile UI design past a deterministic anti-slop gate. Use for "/superdesign", "design X", "make this beautiful", "landing page", "redesign UI", "design system / tokens", "critique this design", "explore directions", "generate image / icon / logo".
---

# /superdesign - intent-driven elegance

Intent -> trend-aware direction that fits -> elegant UI -> verified against a deterministic anti-slop gate. One-line CSS tweak: skip this skill, edit directly.

## Core principles

- Elegance is baseline, not padding. Polished = correct, not extra credit.
- Read intent first. Design to the brief (kind, audience, brand, constraints), never to a default template.
- Trend-aware != trend-chasing. Apply only trends that serve the intent. Record the trend read, dated.
- Anti-slop enforced, not eyeballed. Every surface passes `anti-slop-gate.mjs` + contrast gate. Designer never self-approves.
- Diverse by intent. Route to the aesthetic family + medium that fit; no single house style.
- Self-contained + graceful fallback. Asset/trend tooling degrades: works without Stitch/gpt-image-2/WebSearch; missing tools -> documented placeholder + baked snapshot, never faked assets or invented data. Rendered verification is the one hard dependency: build modes render with `playwright-cli` (the only render driver, `reference/playwright-cli.md`); if it cannot be installed, STOP and ask - never substitute a headless render.
- Verify vs ground truth. Run the static gate AND render the built surface with `playwright-cli`, honor reduced-motion + WCAG AA, report what passed with command output.
- Hard stops. External publish (deploy/push/post) or destructive steps need explicit consent; ambiguous brief -> one question (non-interactive run: conservative read, assumption logged in the brief, proceed).

## Mode (classify the design intent, state it in one line)

| Signal | Mode | Approach |
|---|---|---|
| new UI / landing / app / screen from scratch | CREATE | default loop |
| redesign / modernize / improve / refresh existing UI | REDESIGN | audit-first (`reference/redesign.md`) |
| tokens / palette / type / theme / design system only | SYSTEM | `reference/design-system.md` |
| critique / audit / review (no build) | CRITIQUE | review-only, run detectors (`reference/critique.md`) |
| explore / moodboard / directions / "what style" | EXPLORE | divergent, trend-heavy, no commit (`reference/explore.md`) |
| image / icon / illustration / SVG / logo (asset only) | ASSET | fallback chain (`reference/assets.md`) |

Medium (web vs mobile/native) is orthogonal - decide in the loop, load `reference/web.md` or `reference/mobile.md`.

Tie-breaks (one mode wins): "critique and fix" -> REDESIGN (CRITIQUE never edits). "explore then build" -> EXPLORE; build only after the user picks. Tokens AND a page asked -> the deliverable decides (token file = SYSTEM, page = CREATE). Assets needed inside a build stay in CREATE/REDESIGN; ASSET only when the asset is the whole ask.

## Default loop (CREATE / REDESIGN) - role-separated

Author-independent roles. Single surface -> inline, switch role with a fresh re-read. 2+ surfaces or a parallel asset batch -> orchestrate agents, one Designer per surface; scaffolding may fan out, look-and-feel stays deep-and-narrow.

**Vault** = one work dir per surface, default `.superdesign/<surface>/`: holds `design-brief.md`, `trend-pulse.md`, `claims.md`, `contrast-pairs.json` (start each from `templates/`). `preflight-gate.sh <vault> <source files>` reads it - no vault, no gate. Create it at step 1.

1. **Read (brief).** Infer kind, audience, vibe, references, brand, quiet constraints (a11y/regulation override aesthetics). State: `Reading this as: <kind> for <audience>, <vibe> language, leaning <system or family>.` Two reads diverge -> ask ONE question; else do not ask; non-interactive -> conservative read + logged assumption. Record in the vault. (`reference/design-brief.md`)
2. **Trend pulse.** `WebSearch` current trend lane by default; on failure use `reference/trend-snapshot.md` (dated) and warn it may be stale. Reuse a same-kind pulse <=30 days old instead of re-searching (note the reuse). Keep only intent-serving trends. Record dated in the vault `trend-pulse.md`. (`reference/trend-research.md`)
3. **Direction.** Set dials `DESIGN_VARIANCE` / `MOTION_INTENSITY` / `VISUAL_DENSITY`. Pick official design system OR one aesthetic family (`reference/aesthetics.md`) OR one trend lane - never mix. Pick medium, load `web.md` or `mobile.md`.
4. **Build (Designer).** Implement to `reference/taste-core.md` (always authority) + chosen family/medium. Assets via `reference/assets.md`. Engagement-bearing brief (Read names a primary action - sign up/buy/book/subscribe) -> also `reference/engagement.md`. Data-dense business app (admin/dashboard/console/internal tool) -> also `reference/dashboard.md` (density-first dials, app shell, no marketing hero). Enforce anti-default + reduced-motion + computed contrast. No self-approval; append `claims.md` per surface with a `Framings:` line. (`agents/designer.md`)
5. **Critique (independent; no design edits).** Re-read `taste-core.md` + `impeccable-rules.md`. Enumerate every text/bg pair into vault `contrast-pairs.json`. Run `templates/preflight-gate.sh` (-> `anti-slop-gate.mjs` + `contrast-gate.mjs`) on the source, then render the surface with `playwright-cli` (`reference/playwright-cli.md`), write the `## Render` block, and run `templates/render-gate.sh`. Log every violation. (`agents/design-critic.md`)
6. **Verify.** Fix each violation, smallest change; re-run BOTH gates (preflight + render) until green. Report passes with output. Fresh violation loops critique -> fix; stop on green. Cap: 3 critique->fix cycles; same rule still failing -> stop, report remaining violations honestly.

Roles -> personas: build=`agents/designer.md`, critique=`agents/design-critic.md`, trends=`agents/trend-scout.md`, assets=`agents/asset-producer.md`.

## Mode contract (deliverable + done-when)

No-build modes (SYSTEM/CRITIQUE/EXPLORE/ASSET): load the mode's reference file, deliver its row, skip the loop. ASSET chain: `gpt-image-2` -> Stitch -> real web image/SVG -> placeholder.

| Mode | Deliverable | Verified by |
|---|---|---|
| CREATE / REDESIGN | surface code + vault | `templates/preflight-gate.sh` (static) AND `templates/render-gate.sh` (rendered via playwright-cli) green, output reported |
| SYSTEM | token file + one-screen usage example | `contrast-gate.mjs` on every pair; `anti-slop-gate.mjs` on the sample |
| CRITIQUE | findings report (severity, file:line, fix, verdict) | detectors ran on the input; URL/HTML input also rendered + `render-gate.sh` (screenshot-only input cannot render - note it); zero edits |
| EXPLORE | 2-4 divergent directions + one recommendation | directions genuinely differ; nothing built |
| ASSET | asset file(s) + manifest (tier used, substitutions) | links resolve; palette matches; placeholders flagged |

## Reference map (load only what the phase needs)

| Read | When |
|---|---|
| `reference/design-brief.md` | Read: infer brief, one-line read, single question |
| `reference/trend-research.md` | Trend pulse: search, evaluate, record |
| `reference/trend-snapshot.md` | Trend pulse fallback: dated offline snapshot + refresh |
| `reference/taste-core.md` | Build + Critique: always-on anti-slop visual authority |
| `reference/aesthetics.md` | Direction: pick <=1 aesthetic family (selection map) |
| `reference/impeccable-rules.md` | Critique: anti-pattern guardrails behind the detector |
| `reference/web.md` | Build: web stack + layout |
| `reference/mobile.md` | Build: mobile/native (iOS HIG, Material 3, RN/SwiftUI/Compose) |
| `reference/engagement.md` | Build: conversion/engagement craft when the brief names a primary action (SaaS/consumer/commerce/marketing) |
| `reference/dashboard.md` | Build: data-dense business app / admin / dashboard / internal tool (density-first overlay; no marketing hero) |
| `reference/playwright-cli.md` | Critique: the only render driver - render the built surface, then run `render-gate.sh` |
| `reference/assets.md` | Build / ASSET: image + SVG fallback chain |
| `reference/redesign.md` | REDESIGN: audit-first protocol |
| `reference/design-system.md` | SYSTEM: tokens, scales, theming |
| `reference/critique.md` | CRITIQUE: review-only flow |
| `reference/explore.md` | EXPLORE: divergent directions |
| `reference/sources.md` | Install commands, sources, attribution |

## Final checklist

- [ ] Mode + medium stated; brief read one line; trends pulsed + dated (or snapshot fallback disclosed)
- [ ] One accent, one type system, one radius, one theme strategy; dials declared
- [ ] Real/generated assets (no div-mockups); reduced-motion + WCAG AA honored
- [ ] Engagement-bearing brief -> `reference/engagement.md` applied (primary action obvious, useful states, real-data proof, outcome-led CTA); editorial/portfolio left alone
- [ ] Data-dense business app (admin/dashboard/console/internal tool) -> `reference/dashboard.md` applied (no marketing hero; app shell + density dials VAR 3-5/MOT 2-3/DEN 6-8; KPI north-star with deltas; tabular numerals; colorblind-safe status; per-widget loading/empty/error; tables virtualized past ~50 rows)
- [ ] Mode contract met: build modes -> `templates/preflight-gate.sh` (static) AND `templates/render-gate.sh` (rendered via playwright-cli) green with output reported; other modes -> their verified-by row
- [ ] Smallest change for intent; surrounding style matched; no unrequested rewrites
- [ ] Any external publish / destructive step had explicit consent
