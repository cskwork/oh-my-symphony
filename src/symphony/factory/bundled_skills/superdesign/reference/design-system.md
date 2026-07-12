# design-system - SYSTEM mode

Produce tokens + theming, no full page. Output is a token file (CSS vars / Tailwind theme / JSON) the user can drop in.

## Tokens to define

- **Color**: one accent (sat < 80%), neutral ramp (off-black -> off-white, tinted, not pure), semantic (bg/surface/text/muted/border/success/warn/error). Lock the palette.
- **Type**: family (avoid Inter default) + scale (`display/h1/h2/h3/body/small`) with size + line-height + tracking. Fluid `clamp()` where useful.
- **Spacing**: one scale (4/8-based). **Radius**: one system. **Shadow**: tinted to bg, a small named set.
- **Motion**: duration + easing tokens (no bounce/elastic), reduced-motion variant.

## Theming

Light + dark via one strategy (CSS vars/semantic tokens or Tailwind `dark:`). Both modes meet WCAG AA. Off-black/off-white, not pure.

## Verify

Enumerate every text/bg pair to `contrast-pairs.json`, run `templates/contrast-gate.mjs`. Run `anti-slop-gate.mjs` on any sample markup using the tokens. Deliver tokens + a one-screen usage example.

Done when: token file + usage example delivered; contrast gate green on every pair; sample passes anti-slop.
