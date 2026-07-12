# sources - install commands, sources, attribution

## Design system install

```
npm install @material/web                         # Material 3
npm install @fluentui/react-components            # Fluent (or @fluentui/web-components @fluentui/tokens)
npm install @carbon/react @carbon/styles          # Carbon
npm install @radix-ui/themes                      # Radix
npx shadcn@latest init                            # shadcn/ui
npm install @primer/css   # or @primer/react-brand # Primer
npm install @atlaskit/tokens                       # Atlassian
npm install bootstrap                              # Bootstrap 5.3
```

Shopify Polaris web components: via CDN. govuk-frontend / uswds: per their docs.

## Dashboard kits, tables, charts (data-dense apps; see `dashboard.md`)

```
npx shadcn@latest add sidebar-07 dashboard-01     # app shell + KPI/table blocks (https://ui.shadcn.com/blocks)
npm install @tanstack/react-table @tanstack/react-virtual  # headless table + virtualization
npm install ag-grid-community ag-grid-react        # enterprise grid (pivot/group/aggregate)
npm install @mui/x-data-grid                        # only inside an MUI app
npm install recharts                                # React/SVG default charts
npm install @tremor/react                           # KPI-card + chart kit (built on Recharts; retheme)
npm install @nivo/core @nivo/line                   # many-type SSR charts
npm install @visx/visx                              # bespoke D3 primitives
npm install echarts echarts-for-react               # Canvas/WebGL big-data; lazy-load
npm install uplot                                   # fastest time-series / streaming
npm install victory                                 # a11y-first (built-in ARIA + keyboard)
npm install @observablehq/plot                      # grammar-of-graphics exploration
npm install cmdk                                     # Cmd/Ctrl+K command palette
```

## Canonical sources

- Material: https://material-web.dev , https://m3.material.io
- Fluent: https://fluent2.microsoft.design
- Carbon: https://carbondesignsystem.com
- Radix: https://radix-ui.com
- shadcn/ui: https://ui.shadcn.com
- Primer: https://primer.style
- Atlassian: https://atlassian.design , AWS Cloudscape: https://cloudscape.design , Ant Design: https://ant.design
- shadcn dashboard blocks: https://ui.shadcn.com/blocks , Vercel Geist: https://vercel.com/geist
- TanStack Table: https://tanstack.com/table , TanStack Virtual: https://tanstack.com/virtual
- AG Grid: https://ag-grid.com , MUI X: https://mui.com/x/react-data-grid
- Recharts: https://recharts.org , Tremor: https://tremor.so , Nivo: https://nivo.rocks , visx: https://airbnb.io/visx
- ECharts: https://echarts.apache.org , uPlot: https://github.com/leeoniya/uPlot , Victory: https://commerce.nearform.com/open-source/victory , Observable Plot: https://observablehq.com/plot
- cmdk: https://cmdk.paco.me
- Apple HIG: https://developer.apple.com/design/human-interface-guidelines
- Tailwind v4: https://tailwindcss.com
- Motion: https://motion.dev , GSAP: https://gsap.com

## Asset tooling

- gpt-image-2 skill (Codex/ChatGPT subscription) - local skill, image gen/edit.
- Stitch MCP: https://stitch.withgoogle.com - UI screen generation.
- Simple Icons: https://simpleicons.org , devicon: https://devicon.dev - real brand SVG.
- Unsplash / Pexels - stock photography.

## Verification tooling

- playwright-cli (`@playwright/cli`, microsoft/playwright-cli, Apache-2.0) - the only render-verification driver. Install: `npm install -g @playwright/cli@latest` then `playwright-cli install --skills`; browser via `--browser=chrome` or `npx playwright install chromium`. https://github.com/microsoft/playwright-cli . If it cannot be installed, the Critic stops and asks - no substitute (`reference/playwright-cli.md`).

## Third-party attribution

- **taste-skill** (https://github.com/leonxlnx/taste-skill, MIT) - `taste-core.md` and `aesthetics.md` are condensed, adapted derivatives. Anti-slop rules + aesthetic families.
- **impeccable** (https://github.com/pbakaus/impeccable, Apache-2.0) - `impeccable-rules.md` distills its anti-pattern concepts; `templates/anti-slop-gate.mjs` is an independent re-implementation (no upstream code copied), built on Anthropic's frontend-design lineage.
- **stitch-landing-skill** (https://github.com/cskwork/stitch-landing-skill, MIT) - Stitch flow inspiration for `assets.md` and `web.md`.
- **supergoal-skill** (cskwork, MIT) - skill structure, `contrast-gate.mjs`, agent/gate/test format adapted from the sibling skill. The playwright-cli single-driver pattern and `render-gate.sh` adapt its `reference/playwright-cli.md` + `qa-gate.sh` (the render gate is an independent re-implementation, scoped to static-surface rendering).

## Install superdesign

```
git clone <superdesign-skill-repo> ~/skills/superdesign-skill
ln -s ~/skills/superdesign-skill ~/.claude/skills/superdesign
```
Then `/superdesign <design intent>`.
