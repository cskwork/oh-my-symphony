# AF-14 Codex token-usage schema

Decision: close AF-14 as not protocol-reachable; no production accounting change.

## Evidence

- Installed CLI: `codex-cli 0.144.0` (`codex --version`).
- Current schema command:
  `codex app-server generate-json-schema --experimental --out /private/tmp/codex-schema-af14-20260710`.
- The generated `v2/ThreadTokenUsageUpdatedNotification.json` requires both
  `ThreadTokenUsage.last` and `ThreadTokenUsage.total`.
- The checked-in Codex 0.130 schema at
  `docs/SMA-20/explore/codex-schema/v2/ThreadTokenUsageUpdatedNotification.json`
  has the same required fields. The two notification schema files are
  byte-identical (SHA-256
  `fe70a73653ae9e3fffb0db84d1312f47ac47d92526c2d44461492cd864ada3ad`).

## Conclusion

A valid `thread/tokenUsage/updated` notification cannot contain `last`
without `total` in either verified protocol version. The suspected
`total=1000 -> last=200 -> total=1200` rebase path is therefore unreachable
under the protocol contract. Adding defensive accounting would introduce an
untested alternate wire contract, so `src/symphony/backends/codex.py` remains
unchanged.
