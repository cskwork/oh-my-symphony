# playwright-cli - the single render-verification driver

`playwright-cli` (`@playwright/cli`, microsoft/playwright-cli) is THE and ONLY sanctioned driver for
rendering a built surface to verify it. No Playwright MCP, no Puppeteer, no hand-rolled headless-Chrome
screenshot script, no eyeballing a code block. It runs inside the Critic (`agents/design-critic.md`),
never the orchestrator. Token-efficient by design: it does not force page data into the model.

Static surfaces only here - this verifies a rendered design (a single HTML, a `file://` page, or a dev
URL). The network/auth/tab flows from the sibling supergoal skill are out of scope.

## Get the driver (inside the Critic)

1. `command -v playwright-cli` - if absent, `npm install -g @playwright/cli@latest` (Node 18+).
2. `playwright-cli install --skills` - installs the upstream skill locally so the driver self-documents;
   `playwright-cli --help` still works without it.
3. Browser binary: headless by default. Use system Chrome with `--browser=chrome`, or fetch one with
   `npx playwright install chromium`.
4. **Hard stop on install failure.** If the driver genuinely cannot be installed (offline, no npm,
   blocked), STOP and ask the user to install it. Never substitute a headless-Chrome render or any other
   tool - `render-gate.sh` requires `Tool: playwright-cli` and fails anything else. There is no skip path.

## Verify a static surface

- `playwright-cli open file://<abs-path>` (or `goto <dev-url>`) - render the surface.
- `playwright-cli resize <w> <h>` - set each declared framing (e.g. `390 844` mobile, `1440 900` desktop)
  and re-check at each. Verify the rendered page: hero fits the viewport, no horizontal overflow, the
  primary CTA does not wrap, content is not clipped.
- `playwright-cli screenshot --filename=.superdesign/<surface>/render/<view>.png` - the as-rendered proof,
  one per framing, saved into the vault's `render/` dir.
- `playwright-cli console` - read console output. Zero render errors is the bar (lines beginning
  `error`/`uncaught`/`unhandled`); warnings are allowed. Record the verdict as `Console: clean`.
- `playwright-cli snapshot` - the accessibility tree, to confirm focus order and roles.
- `playwright-cli close` when done.

## Record the evidence

The Critic writes a `## Render` block into the surface vault's `claims.md`: the framings checked, a
`Tool: playwright-cli` line, the screenshot paths, and `Console: clean`. Then it runs
`bash templates/render-gate.sh <vault>`, which fails unless that evidence is present and the driver line
is playwright-cli. The designer never writes the `Tool:` line - it is verifier-authored.

Full command list: `playwright-cli --help` or the upstream skill
(`playwright-cli install --skills`; https://github.com/microsoft/playwright-cli).
