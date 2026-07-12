# engagement - conversion craft overlay (Expressive surfaces with a primary action)

Load inside the Designer subagent, on top of `reference/taste-skill-v2.md`, when the Design Read names a
primary user action - sign up / buy / book / subscribe / install (SaaS, consumer, commerce, marketing).
Skip for editorial, portfolio, docs, or art-led briefs: forcing conversion patterns there is its own slop.
taste-skill-v2's bans still hold; this only adds the conversion-specific deltas below. Each rule is a
mechanism you build, paired with the slop it replaces.

## Conversion hierarchy

- One dominant primary action per view; give it the most visual weight (size, contrast, isolation). Every
  secondary action is quieter. Two equal-weight CTAs is a non-decision the user feels.
- Value-first order: outcome + proof before the form. Repeat the same CTA down a long page; never run two
  different primary intents above the fold.

## Hero that converts

- Lead the H1 with one concrete claim - a specific number or named outcome - not a stock photo + generic
  tagline. Refines taste's hero rule: still fits the viewport, but the first line earns the scroll. An
  unquantified "trusted by thousands" does not convert.

## CTA copy (refines taste's 1-3 word CTA)

- First-person, outcome-named: "Get my audit", "Start my free trial" - not "Submit" / "Sign up" /
  "Learn more". Prefer the outcome label even at 3-4 words.
- One friction-reducer line under the button: "Takes 2 minutes. No credit card."

## States as engagement (taste already requires the states; this is the conversion bar)

- Loading: content-shaped skeleton sized to the final content (no layout shift), not a bare spinner.
- Optimistic UI: apply the user-triggered change immediately, reconcile on resolve, roll back on error.
- Empty = onboarding (one purpose line + the single first action); Error = recovery (plain-language cause
  + inline retry, never a dead end or a raw stack trace).

## Trust + social proof (real data only - extends taste's fake-precision ban)

- Put proof where friction peaks: a rating/review/security badge or guarantee adjacent to the CTA, not in
  the footer. Prefer named, quantified proof.
- Hard ban: no fabricated counts, no urgency timer that resets every visit. Faked scarcity destroys trust
  on the return visit.

## Micro-interaction feedback

- Every interactive element gets immediate hover/active/success feedback tied to a real action. Confirm
  each onboarding/checkout step so the user never wonders "did that work?".
- Inline-validate forms at the field on blur with a recovery hint, not one error wall on submit.
- Calm and functional, not gamified (bounce/elastic easing stays banned).

## Motion that guides attention

- Sequenced/staggered entrance to set reading order toward the primary action - a taste hierarchy reason,
  never decoration.
- Reveals run off the main thread (native scroll-driven CSS / View Transitions API), with a
  `prefers-reduced-motion` fallback. No manual scroll listeners (banned).

## Progressive disclosure

- Defer secondary fields and advanced options behind a reveal; ask the minimum to reach first success.
  Map the empty-state-to-first-success path explicitly.

## Accessibility as an engagement lever (WCAG 2.2)

- Visible focus, keyboard-operable everything, no pointer-gesture-only actions, minimum target 24x24 CSS
  px (44/48 on mobile). Lowers bounce and support load, not just compliance.

## Critic's engagement pass (judgment, not a gate)

Checked as HIGH/MEDIUM findings, never a deterministic gate rule (engagement is contextual): primary
action obvious; loading/empty/error useful not blank; proof present where the kind expects it; key
interactions have visible feedback; primary CTA copy is outcome-led.

<!-- Adapted from superdesign-skill reference/engagement.md (sibling skill, 2026-06-20). -->
