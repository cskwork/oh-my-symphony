# claims - designer self-audit (one entry per surface)

The designer records what was built and HOW to prove it. The critic runs the run-to-prove; the designer never self-approves.

## <surface name>

- Mode + medium: <CREATE|REDESIGN> / <web|mobile>
- Design Read: <kind> for <audience>, <vibe>, leaning <system or family>
- Dials: DESIGN_VARIANCE <n> / MOTION_INTENSITY <n> / VISUAL_DENSITY <n>
- Accent / type / radius / theme: <locked values>
- Assets: <generated tier used | placeholders flagged>
- Claim: <what is done and elegant>
- Framings: <390x844 mobile, 1440x900 desktop>  (designer declares the viewports the critic must render)
- run-to-prove: `bash templates/preflight-gate.sh <vault> <source files>`

## Render (critic fills after rendering the surface with playwright-cli; never the designer)

- Tool: playwright-cli                            (the only sanctioned render driver - reference/playwright-cli.md)
- Screenshots: render/<view>.png ...              (one per declared framing, saved under the vault)
- Console: clean                                  (zero render errors at every framing; warnings allowed)
- render-to-prove: `bash templates/render-gate.sh <vault>`
