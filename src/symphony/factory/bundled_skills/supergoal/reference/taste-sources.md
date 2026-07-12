# tasteskill - sources & install reference

Loaded on demand from `reference/taste-skill-v2.md` when you need a design system's install/setup commands, canonical source links, or the Apple Liquid Glass web approximation.


## Install commands

```bash
npm install @material/web
npm install @fluentui/react-components
npm install @fluentui/web-components @fluentui/tokens
npm install @carbon/react @carbon/styles
npm install @radix-ui/themes
npx shadcn@latest init
npx shadcn@latest add button card badge separator input
npm install --save @primer/css
npm install @primer/react-brand
npm install govuk-frontend
npm install uswds
yarn add @atlaskit/css-reset @atlaskit/tokens @atlaskit/button @atlaskit/badge @atlaskit/section-message @atlaskit/card
npm install bootstrap
```

Shopify Polaris web components: add the Shopify API key meta tag and Polaris script from Shopify CDN.

## Canonical sources

- Material: https://github.com/material-components/material-web, https://material-web.dev/theming/material-theming/, https://m3.material.io/develop/web
- Fluent: https://fluent2.microsoft.design/get-started/develop, https://fluent2.microsoft.design/components/web/react/, https://github.com/microsoft/fluentui, https://learn.microsoft.com/en-us/fluent-ui/web-components/
- Carbon: https://carbondesignsystem.com/, https://github.com/carbon-design-system/carbon
- Shopify Polaris: https://shopify.dev/docs/api/app-home/web-components, https://github.com/Shopify/polaris-react
- Atlassian: https://atlassian.design/get-started/develop, https://atlassian.design/tokens/design-tokens
- Primer: https://primer.style/, https://github.com/primer/css, https://github.com/primer/brand
- GOV.UK: https://design-system.service.gov.uk/, https://github.com/alphagov/govuk-frontend
- USWDS: https://designsystem.digital.gov/documentation/developers/, https://github.com/uswds/uswds
- Bootstrap: https://getbootstrap.com/docs/5.3/
- Tailwind: https://tailwindcss.com/docs/dark-mode, https://tailwindcss.com/blog/tailwindcss-v4
- Radix: https://www.radix-ui.com/themes/docs/components/theme, https://github.com/radix-ui/themes
- shadcn/ui: https://ui.shadcn.com/docs, https://github.com/shadcn-ui/ui
- Native CSS: MDN `backdrop-filter`, `prefers-color-scheme`, `prefers-reduced-motion`, Grid, scroll-driven animations; https://drafts.csswg.org/scroll-animations-1/
- Apple: HIG Materials, Liquid Glass docs, Adopting Liquid Glass, SwiftUI Material.

## Apple Liquid Glass web approximation

Do not treat web CSS snippets as official Apple Liquid Glass. Official Liquid Glass is Apple-platform
material. Web can only approximate it with `backdrop-filter`, transparent layers, borders, highlights,
gradients, motion, and strong contrast fallbacks. Label comments as approximation.

Minimum safe skeleton: isolated relative container, inherited radius, subtle translucent background,
`backdrop-filter: blur(...) saturate(...)`, inner border/highlight pseudo-elements, dark-mode variant,
reduced-transparency fallback with solid fill, and enough contrast without blur.
