# Flows

Create one source-grounded flow per file.

Keep each flow narrow. If one file starts covering two ticket families, split it and add both files to
`index.md`.

Recommended shape:

```md
# <Flow Name>

## Search Keys

- <ticket words, routes, symbols, DTOs, entities>

## Summary

<one paragraph>

## Current Code Path

1. <entry point with file/symbol>
2. <service/module>
3. <data or external side effect>

## Invariants

- <rule that must not break>

## Scenario Checks

- Normal: <expected happy path>
- Boundary: <edge case that often causes ambiguity>
- Failure: <error or missing-data path to verify>

## Verification

- <command or inspection path>
- Grounding: verified -- <probe run + result> | unverified -- <why it cannot be executed>

## Last Verified

- <iso-date>: <source>
```
