<!--
Concepts distilled from impeccable (https://github.com/pbakaus/impeccable, Apache-2.0).
No upstream code copied; the detector in templates/anti-slop-gate.mjs is an independent implementation.
Attribution in reference/sources.md.
-->

# impeccable-rules - anti-pattern guardrails

The "why" behind `templates/anti-slop-gate.mjs`. Models trained on the same SaaS templates produce predictable tells; these rules + the detector remove them. Used at Critique.

## Hard don'ts

- No overused fonts (Arial, Inter, system defaults) without an explicit neutral read.
- No gray text on colored backgrounds.
- No pure black/gray - always tint.
- No wrapping everything in cards, no cards nested in cards.
- No bounce/elastic easing (reads dated).

## Detector targets (machine-checkable subset)

These map to gate rules. Each is grep-able in built HTML/JSX/CSS:

- Em-dash / en-dash as a separator in visible text.
- AI-purple family as primary accent (`#7c3aed`, `#8b5cf6`, `#a855f7`, `violet-*`, `purple-*` on CTAs/gradients).
- `Inter` set as the default/only font without a neutral justification comment.
- `cubic-bezier` bounce/elastic (overshoot) or `easeInOutBack`-style easing.
- Pure `#000` / `#fff` used as text or background fill.
- Placeholder identities: `John Doe`, `Sarah`, `Acme`, `Nexus`, `SmartFlow`, `lorem ipsum`.
- Filler verbs in copy: `elevate`, `seamless`, `unleash`, `next-gen`, `revolutionize`, `supercharge`.
- `box-shadow` pure-black on light backgrounds (`rgba(0,0,0,0.3+)`).
- Manual scroll listeners (`addEventListener('scroll'`), `window.scrollY` in render).

## Quality checks (human/critic judgment, not gate)

Cramped padding, small touch targets (<44px), skipped heading levels, side-tab borders, dark glows, decorative-only dots, equal 3-card rows, split-header pattern, eyebrow overuse. These are critique findings; the designer fixes, the critic re-checks.

## Flow

Critique runs the gate (deterministic) AND eyeballs the quality checks (judgment). Gate failures are blocking; quality findings are logged with file:line + a concrete fix and addressed before Verify passes.
