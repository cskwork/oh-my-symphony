# engagement - conversion + UX craft (load when the brief earns it)

Load at Build when the Design Read names a primary user action - sign up, buy, book, subscribe, install -
i.e. SaaS / consumer / commerce / marketing. Skip for editorial, portfolio, docs, or art-led briefs:
forcing conversion patterns onto them is its own slop. Pairs with `taste-core.md` (its bans still hold).

Every rule below is a mechanism you build, not a vibe. Each pairs a positive move with the slop it
replaces, so it survives the anti-slop ethos.

## Conversion hierarchy

- One dominant primary action per view. Give it the most visual weight (size, contrast, isolation); make
  every secondary action quieter. Two equal-weight CTAs is a non-decision the user feels.
- Order value-first: what it does and the proof it works, before the form. The eye should land on the
  outcome, then the action.
- Short, single-offer pages: one above-the-fold CTA. Repeat the same CTA down a long page; never compete
  two different primary intents above the fold.

## Hero that converts

- Lead the H1 with one concrete claim - a specific number or named outcome - over a stock photo + generic
  tagline. A single-stat or named-claim hero out-converts a stock hero; an unquantified "trusted by
  thousands" does not.
- Keep the hero within the viewport (taste-core rule) AND make its first line earn the scroll.

## Action-driving CTA copy

- First-person, outcome-named labels: "Get my audit", "Start my free trial" - not "Submit", "Sign up",
  "Learn more". This refines taste-core's 1-3 word CTA: prefer the outcome label even at 3-4 words.
- One friction-reducer line directly under the button: "Takes 2 minutes. No credit card." Removes the
  unspoken "what happens when I click".

## States as engagement, not blanks

- Loading: a content-shaped skeleton (boxes sized to the final content, so there is no layout shift) over
  a spinner. Reserve the real dimensions. A spinner is the fallback, not the default.
- Optimistic UI: apply a user-triggered change to the UI immediately, reconcile when the request resolves,
  roll back on error. The interface should feel faster than the API.
- Empty state = onboarding: a one-line purpose + the single first action, never a blank panel.
- Error state = recovery: say what failed in plain words and offer the retry/next step inline; never a
  dead end or a raw stack trace.

## Trust + social proof (real data only)

- Place proof where friction peaks: a rating/review/security badge or guarantee adjacent to the purchase
  or signup CTA, not parked in the footer.
- Prefer named, quantified proof and video testimonials over vague claims. Real-time signals (live
  inventory, recent activity) only when driven by real data.
- Hard ban (extends taste-core's fake-precision ban): no fabricated counts, no urgency timer that resets
  every visit. Faked scarcity destroys trust on the return visit.

## Micro-interaction feedback

- Every interactive element gets immediate state feedback: hover, active/pressed, success/confirm - tied
  to a real action. Confirm each step of onboarding/checkout so the user never wonders "did that work?"
- Inline-validate forms at the field, on blur, with a recovery hint - not one error wall on submit.
- Calm and functional, not gamified. Motion that distracts from the action is slop (bounce/elastic easing
  stays banned).

## Motion that guides attention

- Use sequenced/staggered entrance to set reading order and draw the eye toward the primary action - a
  reason from taste-core's hierarchy/feedback/state/story set, never decoration.
- Reveals run off the main thread (native scroll-driven CSS / View Transitions per `web.md`), with a
  `prefers-reduced-motion` fallback. No manual scroll listeners (banned).

## Progressive disclosure

- Defer secondary fields and advanced options behind a reveal; ask for the minimum to reach first success.
- Map the empty-state-to-first-success path explicitly; do not dump full complexity on first contact.

## Accessibility as an engagement lever (WCAG 2.2)

- Visible focus indicators, keyboard-operable everything, no pointer-gesture-only actions, minimum target
  size 24x24 CSS px (44/48 on mobile, see `mobile.md`), accessible auth (no memory/transcription-only
  step). These lower bounce and support load for everyone, not just compliance.

## Critic's engagement pass (judgment, not a gate)

The Critic checks these as HIGH/MEDIUM findings, never as a deterministic gate rule (engagement is
contextual): primary action obvious; loading/empty/error are useful not blank; proof present where the
kind expects it; key interactions have visible feedback; primary CTA copy is outcome-led.
