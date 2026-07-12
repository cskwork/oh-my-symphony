#!/usr/bin/env bash
# /supergoal commit gate — the literal precondition for commit/merge in a GREENFIELD/DEBUG/LEGACY run
# (reference/delivery-gate.md "Commit gate"). Run it and see PASS before committing/merging into the
# target/integration branch. It blocks the commit while the run vault is not green:
#   1) GOAL.md exists and its ## Success Criteria is seeded (>=1 checkbox row),
#   2) no decision gate is still open (ask-user/unresolved => block; resolve or ask the user, do not commit),
#   3) every Success Criterion and QA Case is checked — an unchecked '- [ ]' (including a surfaced
#      criterion or a leftover placeholder row) blocks commit,
#   4) PLAN.md ## Approval is approved-by-user or auto-approved (a pending plan blocks),
#   5) QA.md Backward-trace is exactly 'clean' (no orphan scope),
#   6) non-exact Reproduction Fidelity records residual risk and post-deploy confirmation (QA.md),
#   7) QA.md ## Results has >=1 checked row and no unchecked row,
#   8) QA verdict is PASS (FAIL or PARTIAL/incomplete blocks); for an app run, browser/CLI QA evidence passes,
#   9) at least one trusted command (frozen_repo/evaluator_owned) backs QA.md,
#  10) exactly one Z-<date>.md completion marker exists with Branch: and Completed: lines.
# NEVER edit this script to make a non-green run commit — resolve the gap or ask the user instead.
#
# Usage: commit-gate.sh <vault-dir> [browser|cli|none]
#   <vault-dir>      the run's changelog folder, e.g. docs/changelog/2026-06/30-commit-gate
#   browser|cli|none app exercised: a browser app, a CLI/library, or none (no app QA to delegate)

set -euo pipefail

usage() { echo "usage: commit-gate.sh <vault-dir> [browser|cli|none]" >&2; exit 2; }
[ $# -ge 1 ] || usage
VAULT="$1"; APPTYPE="${2:-none}"
GOAL="$VAULT/GOAL.md"
PLAN="$VAULT/PLAN.md"
QAMD="$VAULT/QA.md"
fail() { echo "COMMIT-GATE FAIL: $*" >&2; exit 1; }

case "$APPTYPE" in browser|cli|none) ;; *) usage ;; esac

echo "== /supergoal commit gate =="
echo "vault: $VAULT  app-type: $APPTYPE"

# 1) Goal present and seeded: GOAL.md exists and ## Success Criteria has at least one checkbox row.
[ -s "$GOAL" ] || fail "GOAL.md missing/empty — no success criteria to commit against"
criteria_rows="$(awk '
  /^[[:space:]]*##[[:space:]]+Success Criteria/ { ing=1; next }
  /^[[:space:]]*##[[:space:]]/ { ing=0 }
  ing && /^[[:space:]]*-[[:space:]]*\[[ xX]\]/ { rows++ }
  END { print rows+0 }
' "$GOAL")"
[ "$criteria_rows" -ge 1 ] || fail "GOAL.md ## Success Criteria has no checkbox row — seed the criteria at Frame before commit"
echo "  ok: success criteria seeded ($criteria_rows)"

# 2) Decision gates resolved: in the '## Decision Gates' table, no Status cell (col 3) still reads 'open'.
#    Catches unresolved ask-user gates AND an un-filled placeholder row ('open / resolved').
open_gate="$(awk -F'|' '
  /^[[:space:]]*##[[:space:]]+Decision Gates/ { ing=1; next }
  /^[[:space:]]*##[[:space:]]/ { ing=0 }
  ing && /^[[:space:]]*\|/ {
    s=$4; gsub(/^[[:space:]]+|[[:space:]]+$/,"",s); l=tolower(s)
    if (l ~ /(^|[^a-z])open([^a-z]|$)/) { print "OPEN"; exit }
  }
' "$GOAL")"
[ -z "$open_gate" ] || fail "an open decision gate remains in GOAL.md — resolve it or ask the user about the requirement; do not commit on an unresolved/ask-user gate"
echo "  ok: no open decision gate"

# 3) Every criterion checked: no unchecked '- [ ]' row under ## Success Criteria or ## QA Cases.
#    A surfaced criterion the verifier has not ticked, or a leftover placeholder row, blocks here —
#    remove placeholder rows that do not apply instead of ticking them.
unchecked="$(awk '
  /^[[:space:]]*##[[:space:]]+(Success Criteria|QA Cases)/ { ing=1; next }
  /^[[:space:]]*##[[:space:]]/ { ing=0 }
  ing && /^[[:space:]]*-[[:space:]]*\[[[:space:]]\]/ { n++ }
  END { print n+0 }
' "$GOAL")"
[ "$unchecked" -eq 0 ] || fail "GOAL.md has $unchecked unchecked success criterion/QA case — the verifier must tick every box (or the requirement must be resolved with the user) before commit"
echo "  ok: every success criterion and QA case checked"

# 4) Plan approved: PLAN.md ## Approval Status is approved-by-user or auto-approved. A pending status,
#    a placeholder, or a missing PLAN.md blocks — implementation without an approved plan cannot commit.
[ -s "$PLAN" ] || fail "PLAN.md missing/empty — no approved plan to commit against"
approval_line="$(awk '
  /^[[:space:]]*##[[:space:]]+Approval/ { ing=1; next }
  /^[[:space:]]*##[[:space:]]/ { ing=0 }
  ing && tolower($0) ~ /^[[:space:]]*-?[[:space:]]*status:/ { print; exit }
' "$PLAN")"
[ -n "$approval_line" ] || fail "PLAN.md ## Approval has no Status line — record the approval (user OK or auto-approved) before commit"
approval_l="$(printf '%s' "$approval_line" | tr '[:upper:]' '[:lower:]')"
if printf '%s' "$approval_l" | grep -q 'pending'; then
  fail "PLAN.md approval is pending (or the placeholder is unfilled) — get the user's explicit OK, or record the autonomous auto-approval, before commit"
fi
printf '%s' "$approval_l" | grep -qE 'approved-by-user|auto-approved' \
  || fail "PLAN.md approval status is neither approved-by-user nor auto-approved — a plan without approval cannot commit"
echo "  ok: plan approved"

# 5) Backward trace clean: QA.md attests the diff maps back to GOAL.md criteria with no orphan scope.
[ -s "$QAMD" ] || fail "QA.md missing/empty — no verification evidence to commit against"
backward_line="$(grep -m1 -E '^[[:space:]]*Backward-trace:' "$QAMD" || true)"
[ -n "$backward_line" ] || fail "QA.md missing Backward-trace line — reverse trace scope before commit"
backward_norm="$(printf '%s' "$backward_line" | tr '[:upper:]' '[:lower:]' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g; s/[[:space:]]+/ /g')"
[ "$backward_norm" = "backward-trace: clean" ] \
  || fail "Backward-trace is not clean — remove orphan scope or get explicit user acceptance before commit"
echo "  ok: backward trace clean"

# 6) Reproduction Fidelity: exact runs are minimal. Non-exact prod/proxy runs must record the data gap's
#    residual risk and a post-deploy confirmation plan; a synthetic green alone is not conclusive proof.
proof_field() {
  local label="$1"
  awk -v label="$label" '
    /^[[:space:]]*##[[:space:]]+Reproduction Fidelity/ { ing=1; next }
    /^[[:space:]]*##[[:space:]]/ { ing=0 }
    ing {
      line=$0
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      low=tolower(line); want=tolower(label) ":"
      if (index(low, want) == 1) {
        sub(/^[^:]*:[[:space:]]*/, "", line)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        print line
        exit
      }
    }
  ' "$QAMD"
}
fidelity="$(proof_field "Fidelity level")"
[ -n "$fidelity" ] || fail "QA.md missing Reproduction Fidelity fidelity level"
fidelity_l="$(printf '%s' "$fidelity" | tr '[:upper:]' '[:lower:]' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
case "$fidelity_l" in
  exact)
    echo "  ok: reproduction fidelity exact"
    ;;
  prod-snapshot|synthetic-representative|synthetic-minimal|not-reproduced)
    residual="$(proof_field "Residual risk from data gap")"
    confirm="$(proof_field "Post-deploy confirmation plan")"
    if ! printf '%s' "$residual" | grep -qE '[[:alnum:]]' || printf '%s' "$residual" | grep -qiE '^(todo|tbd|none|n/a|<.*>|\(.*\))$'; then
      fail "non-exact Reproduction Fidelity missing residual risk from data gap"
    fi
    if ! printf '%s' "$confirm" | grep -qE '[[:alnum:]]' || printf '%s' "$confirm" | grep -qiE '^(todo|tbd|none|n/a|<.*>|\(.*\))$'; then
      fail "non-exact Reproduction Fidelity missing post-deploy confirmation plan"
    fi
    echo "  ok: non-exact reproduction fidelity records residual risk and post-deploy plan"
    ;;
  *)
    fail "unknown or placeholder Reproduction Fidelity level '$fidelity' — use exact, prod-snapshot, synthetic-representative, synthetic-minimal, or not-reproduced"
    ;;
esac

# 7) Results evidenced AND green: QA.md ## Results has >=1 checked row and no unchecked row. The checkbox
#    is the status — an unchecked result is not green, and zero rows is an unevidenced after target.
results_state="$(awk '
  /^[[:space:]]*##[[:space:]]+Results/ { ing=1; next }
  /^[[:space:]]*##[[:space:]]/ { ing=0 }
  ing && /^[[:space:]]*-[[:space:]]*\[[xX]\]/ { checked++ }
  ing && /^[[:space:]]*-[[:space:]]*\[[[:space:]]\]/ { unchecked++ }
  END {
    if (unchecked>0) print "RED";
    else if (checked>0) print "OK";
    else print "EMPTY";
  }
' "$QAMD")"
case "$results_state" in
  OK)    echo "  ok: results evidenced (green)" ;;
  RED)   fail "QA.md ## Results has an unchecked row — the after target is not green; fix it before commit" ;;
  *)     fail "QA.md ## Results has no checked row — the after target is not evidenced; finish verification before commit" ;;
esac

# 8) QA verdict: block on ANY FAIL/PARTIAL anywhere in the vault (not just the first), and on an un-filled
#    '<PASS | FAIL | PARTIAL>' placeholder (a started-but-incomplete QA report). For an app run, delegate
#    browser/CLI evidence to the shared qa-gate.sh.
if grep -rhiE 'Verdict:[[:space:]]*(FAIL|PARTIAL)([[:space:]]|$)' "$VAULT" >/dev/null 2>&1; then
  fail "QA verdict FAIL/PARTIAL present — failed/incomplete QA blocks commit; finish QA or ask the user"
fi
if grep -rhiE 'Verdict:[[:space:]]*(<|PASS[[:space:]]*\|)' "$VAULT" >/dev/null 2>&1; then
  fail "QA report has an un-filled Verdict placeholder — QA is incomplete; finish it or ask the user"
fi
if [ "$APPTYPE" != none ]; then
  QAGATE="$(dirname "$0")/qa-gate.sh"
  [ -f "$QAGATE" ] || fail "qa-gate.sh not found next to commit-gate.sh — cannot verify $APPTYPE QA evidence"
  bash "$QAGATE" "$VAULT" "$APPTYPE" || fail "QA evidence gate failed (see qa-gate output above)"
fi
echo "  ok: QA verdict clean"

# 9) A trusted command backs the proof (agent-detected commands cannot be the whole proof).
grep -qE 'frozen_repo|evaluator_owned' "$QAMD" \
  || fail "no trusted command (frozen_repo/evaluator_owned) in QA.md — agent-detected alone cannot prove done"
echo "  ok: trusted command present"

# 10) Completion marker: exactly one Z-<date>.md, created only after every criterion was checked (checks
#     1-3 above prove that), carrying the run branch and the completion timestamp.
z_count=0; z_file=""
for z in "$VAULT"/Z-*.md; do
  [ -e "$z" ] || continue
  z_count=$((z_count+1)); z_file="$z"
done
[ "$z_count" -ge 1 ] || fail "no Z-<date>.md completion marker — it is written only when every GOAL.md criterion is checked; finish the run before commit"
[ "$z_count" -eq 1 ] || fail "multiple Z-*.md completion markers in the vault — a run completes once; remove the stray marker"
[ -s "$z_file" ] || fail "$(basename "$z_file") is empty — record the branch and completion timestamp"
grep -qE '^[[:space:]]*-[[:space:]]*Branch:' "$z_file" \
  || fail "$(basename "$z_file") missing 'Branch:' line — record where the work was done"
grep -qE '^[[:space:]]*-[[:space:]]*Completed:' "$z_file" \
  || fail "$(basename "$z_file") missing 'Completed:' line — record the completion timestamp"
echo "  ok: completion marker $(basename "$z_file") present"

echo "== COMMIT GATE PASS =="
