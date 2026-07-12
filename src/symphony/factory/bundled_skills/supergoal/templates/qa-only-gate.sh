#!/usr/bin/env bash
# /supergoal QA-ONLY gate — the literal exit condition for a QA-ONLY run (reference/qa-only.md).
# QA-ONLY writes no code and runs no delivery gate, so THIS is its terminal backstop. It enforces:
#   1) the human report exists with its required anchor sections (so the user always gets a readable result),
#   2) the underlying browser/CLI QA evidence passes (delegates to qa-gate.sh — same as in-pipeline QA),
#   3) the run stayed within its action budget (action_count <= action_cap; default cap 100),
#   4) if a DB was read, it was marked read-only and recorded NO write SQL (the read-only backstop).
# NEVER edit this script to make a non-compliant QA-ONLY run pass — re-run QA properly instead.
#
# Usage: qa-only-gate.sh <vault-dir> <browser|cli>
#   <vault-dir>  the run's changelog folder, e.g. docs/changelog/2026-06/05-qa-checkout
#   browser|cli  app type exercised: a browser app or a CLI/library

set -euo pipefail

usage() { echo "usage: qa-only-gate.sh <vault-dir> <browser|cli>" >&2; exit 2; }
[ $# -ge 2 ] || usage
VAULT="$1"; APPTYPE="$2"
VERIF="$VAULT/QA.md"
REPORT="$VAULT/report.md"
STATE="$VAULT/state.json"
LEDGER="$VAULT/qa/scenario-ledger.md"
fail() { echo "QA-ONLY-GATE FAIL: $*" >&2; exit 1; }

case "$APPTYPE" in browser|cli) ;; *) usage ;; esac

echo "== /supergoal QA-ONLY gate =="
echo "vault: $VAULT  app-type: $APPTYPE"

# 1) Human report present with its required anchor sections (English anchors; prose may be any language).
[ -s "$VAULT/brief.md" ] || fail "brief.md missing/empty — QA scope was never recorded"
[ -s "$LEDGER" ] || fail "qa/scenario-ledger.md missing/empty — Impact Matrix and scenario shards were not recorded"
grep -qiF 'Impact Matrix' "$LEDGER" \
  || fail "qa/scenario-ledger.md is missing the Impact Matrix section"
grep -qiE '^[[:space:]]*##[[:space:]]+Shards([[:space:]]|$)' "$LEDGER" \
  || fail "qa/scenario-ledger.md is missing the Shards section"
grep -qiE '^[[:space:]]*(\||[-*])[^\n#]*(PASS|FAIL|BLOCKED|NOT[ -]?COVERED)[^\n#]*(qa/|evidence|reason|risk|blocked|not[ -]?covered)' "$LEDGER" \
  || fail "qa/scenario-ledger.md has no scenario outcome with status and evidence/reason"
[ -s "$REPORT" ] || fail "report.md missing/empty — the human QA report was not written (templates/qa-report.md)"
while IFS= read -r h; do
  # Match as a real heading (start-of-line, optional trailing space), not a substring buried in prose.
  grep -qiE "^[[:space:]]*${h}[[:space:]]*$" "$REPORT" \
    || fail "report.md is missing the '$h' heading (templates/qa-report.md)"
done <<'EOF'
## Impact coverage
## What worked
## What didn't
## What I discovered
## Reproduction notes
## Not covered
## How to re-run
EOF
echo "  ok: report.md has all required anchor headings"

# 2) Underlying QA evidence (browser as-is/to-be + driver, or CLI smoke) via the shared qa-gate.sh.
QAGATE="$(dirname "$0")/qa-gate.sh"
[ -f "$QAGATE" ] || fail "qa-gate.sh not found next to qa-only-gate.sh"
bash "$QAGATE" "$VAULT" "$APPTYPE" || fail "underlying QA evidence gate failed (see qa-gate output above)"

# 3) Action budget: action_count must be recorded and within action_cap (default 100).
[ -s "$STATE" ] || fail "state.json missing/empty — action_count not recorded"
command -v node >/dev/null 2>&1 || fail "node is required for the action-cap check"
counts="$(node -e '
const fs = require("fs");
let j;
try { j = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); }
catch (e) { console.error("state.json is not valid JSON"); process.exit(3); }
const cap = (typeof j.action_cap === "number") ? j.action_cap : 100;
const cnt = j.action_count;
if (typeof cnt !== "number") { console.error("state.json has no numeric action_count"); process.exit(4); }
process.stdout.write(cnt + " " + cap);
' "$STATE" 2>&1)" || fail "action-cap check: $counts"
cnt="${counts%% *}"; cap="${counts##* }"
[ "$cnt" -le "$cap" ] 2>/dev/null \
  || fail "action_count ($cnt) exceeds action_cap ($cap) — re-scope the QA run; do not raise the cap to pass"
echo "  ok: action_count $cnt within cap $cap"

# 4) DB read-only backstop: if a DB was used, EVERY 'DB:' line must be marked read-only and no write
#    statement (DML/DDL/DCL/stored-proc) may be recorded anywhere in QA.md.
if [ -s "$VERIF" ] && grep -qiE '^[[:space:]]*[-*]?[[:space:]]*DB:' "$VERIF"; then
  # Per-line, so a second DB connection cannot ride in unmarked behind a first read-only one.
  while IFS= read -r dbline; do
    printf '%s' "$dbline" | grep -qiF 'read-only' \
      || fail "a '## QA' DB: line is not marked read-only: ${dbline}"
  done < <(grep -iE '^[[:space:]]*[-*]?[[:space:]]*DB:' "$VERIF")
  if grep -qiE 'INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|DELETE[[:space:]]+FROM|UPDATE[[:space:]]+[^[:space:]]+[[:space:]]+SET|MERGE[[:space:]]+INTO|TRUNCATE([[:space:]]|$)|DROP[[:space:]]+(TABLE|DATABASE|INDEX|VIEW|SCHEMA)|ALTER[[:space:]]+(TABLE|DATABASE|INDEX|VIEW|SCHEMA)|CREATE[[:space:]]+(TABLE|DATABASE|INDEX|VIEW|SCHEMA)|GRANT[[:space:]]|REVOKE[[:space:]]|CALL[[:space:]]' "$VERIF"; then
    fail "## QA records a DB write statement — QA-ONLY DB access is read-only (SELECT/SHOW/DESCRIBE/EXPLAIN only)"
  fi
  echo "  ok: DB used, every DB: line marked read-only, no write SQL recorded"
fi

echo "== QA-ONLY GATE PASS =="
