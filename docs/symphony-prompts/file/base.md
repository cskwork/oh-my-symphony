You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}Retry attempt {{ attempt }}. Read the previous `## Resolution`, `## Blocker`, `## QA Failure`, or `## Review Findings` section first; fix the root cause, not the symptom.{% endif %}{% if is_rewind %}Rewind turn from a Review or QA finding. Read the most recent `## Review Findings` or `## QA Failure` section first; fix exactly those items, do NOT open new scope. Agent context is fresh: only the ticket body and `docs/{{ issue.identifier }}/` survive.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

{% if issue.labels %}Labels: {{ issue.labels | join: ", " }}{% endif %}

{% if issue.blocked_by %}
This ticket depends on:
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }} ({{ blocker.state }})
{% endfor %}
{% endif %}

## Production pipeline (eight stages, no skipping)

Honour the gate matching `{{ issue.state }}`. One stage = one transition; never jump ahead.

```
  Todo  ->  Explore  ->  Plan  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Merge Gate  ->  Human Review  ->  Done
                              ^   \                ^    \                ^
                              |    +-> Blocked     |     +-> Blocked     |
                              |                    |                     |
                              +-- Review CRITICAL/HIGH/MEDIUM rewinds here|
                              +-- QA failure rewinds here ---------------+
```

- `docs/llm-wiki/` — domain knowledge base: one Markdown entry per topic + `INDEX.md`. Explore reads it before new work; Learn writes back after QA passes (the first Learn creates the directory).
- Plan turns Explore's candidates into one executable `## Plan`; In Progress must read that plan before editing code.
- Learn's Merge Gate handles feature-branch integration before `Human Review`; `Done` requires human confirmation from the TUI or web viewer.
- `docs/{{ issue.identifier }}/` — this ticket's evidence root (artefact policy: Hard rules below). Learn writes to `${LLM_WIKI_PATH:-./docs/llm-wiki}/<topic>.md`, a sibling under the same `docs/` root.
- Ticket file: `kanban/{{ issue.identifier }}.md`. Transition = edit the YAML front matter `state:` field; narrative = append body sections. Symphony reconciles on the next poll tick.
{% if token_budget %}
- Token budget: keep this turn under {{ token_budget }} completion tokens (stage EMA: {{ token_ema }}). Cut narration, never evidence.
{% endif %}

## Audience & writing style (every section you append)
{% if language == 'ko' %}
Readers include non-developers (PM / 기획자). Plain-language header first, code detail after; a non-dev must grasp what/why/how in ~30 seconds.

**Plain-Korean header (mandatory, first lines of every section except
the one-line Triage):**

```
**무엇**: <한 줄, 비-개발자도 이해 가능한 한국어>
**왜**: <한 줄, 사용자/시스템에 어떤 가치/위험이 있는지>
**As-Is → To-Be**:
- As-Is: <한 줄, 이 단계 시작 전 상태>
- To-Be: <한 줄, 이 단계 종료 후 상태>
```

Then the stage body, within these caps. Overflow goes to
`docs/{{ issue.identifier }}/<stage>/details.md` plus a final link line:
`_세부: docs/<id>/<stage>/details.md_`.

| Section                 | Body cap (after header)                | Goes in details.md instead         |
|-------------------------|----------------------------------------|-------------------------------------|
| `## Triage`             | 1-2 lines total (no header needed)     | n/a                                 |
| `## Domain Brief`       | ≤ 12 lines                             | extra path:line citations, vendor docs |
| `## Plan Candidates`    | ≤ 8 lines (1-2 per option)             | per-option diff sketches            |
| `## Recommendation`     | ≤ 5 lines                              | first-failing-test full text        |
| `## Plan`               | ≤ 10 lines                             | full step list, risk notes, fallback commands |
| `## Acceptance Tests`   | ≤ 10 lines (1 bullet per AC)           | per-test setup / fixtures           |
| `## Done Signals`       | ≤ 8 lines (1 bullet per signal)        | full payload bodies, long curl logs |
| `## Difficulty`         | ≤ 2 lines (verdict + 1-line rationale) | n/a                                 |
| `## Implementation`     | ≤ 10 lines                             | per-file change list, helper names  |
| `## Pipeline Route`     | 1 line (route + why any stage skipped) | n/a                                 |
| `## Surfaced Requirements` | ≤ 8 lines (1 bullet per requirement) | full rationale per requirement       |
| `## Critic Tests`       | ≤ 8 lines (1 test signature per line)  | full failing-test output             |
| `## Security Audit`     | exactly 7 rows (1 per check, no spillover) | per-check reasoning, suppression rationale |
| `## Review`             | ≤ 6 rows in severity table             | full check-list reasoning, fix diffs |
| `## Review Findings`    | severity table only (≤ 6 rows, 1 line each) | full check-list reasoning, fix diffs go to `docs/{{ issue.identifier }}/review/details.md` |
| `## QA Evidence`        | header + commands + 1-line `**판정**` + AC table + AC Scorecard | raw pytest/curl/Playwright output |
| `## Learnings`          | ≤ 8 lines (3-4 bullets)                | extended rationale, follow-ups      |
| `## Wiki Updates`       | ≤ 4 lines                              | n/a (wiki is the source of truth)   |
| `## Human Review`       | ≤ 18 lines across all 6 sub-sections   | full evidence dump under docs/      |
| As-Is → To-Be Report    | ≤ 20 lines across all 4 sub-sections   | full evidence dump under docs/      |

**Style rules:**

- Cite the top 1-3 `path:line` anchors only; no function signatures,
  field lists, diff hunks, or per-line walks. Extra citations and raw
  command output go to `docs/{{ issue.identifier }}/<stage>/details.md`.
- 헤더와 요약 줄은 한국어; code spans (`path:line`, identifiers, command
  output)는 영어 그대로. 코드 심볼을 한국어로 번역하지 않는다.
- Jargon needs one short parenthetical for a 기획자; longer explanations
  go to `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, don't tell. "200 passed" beats "all tests passed".
- The Plain-Korean headers alone (skipping every technical body) must
  tell the entire ticket end-to-end.
{% else %}
Readers include non-developers (PMs and product managers). Plain-language header first, code detail after; a non-dev must grasp what/why/how in ~30 seconds.

**Plain-language header (mandatory, first lines of every section except
the one-line Triage):**

```
**What**: <one line, understandable by a non-developer>
**Why**: <one line, what value or risk this carries for the user/system>
**As-Is → To-Be**:
- As-Is: <one line, state before this stage>
- To-Be: <one line, state after this stage>
```

Then the stage body, within these caps. Overflow goes to
`docs/{{ issue.identifier }}/<stage>/details.md` plus a final link line:
`_details: docs/<id>/<stage>/details.md_`.

| Section                 | Body cap (after header)                | Goes in details.md instead         |
|-------------------------|----------------------------------------|-------------------------------------|
| `## Triage`             | 1-2 lines total (no header needed)     | n/a                                 |
| `## Domain Brief`       | ≤ 12 lines                             | extra path:line citations, vendor docs |
| `## Plan Candidates`    | ≤ 8 lines (1-2 per option)             | per-option diff sketches            |
| `## Recommendation`     | ≤ 5 lines                              | first-failing-test full text        |
| `## Plan`               | ≤ 10 lines                             | full step list, risk notes, fallback commands |
| `## Acceptance Tests`   | ≤ 10 lines (1 bullet per AC)           | per-test setup / fixtures           |
| `## Done Signals`       | ≤ 8 lines (1 bullet per signal)        | full payload bodies, long curl logs |
| `## Difficulty`         | ≤ 2 lines (verdict + 1-line rationale) | n/a                                 |
| `## Implementation`     | ≤ 10 lines                             | per-file change list, helper names  |
| `## Pipeline Route`     | 1 line (route + why any stage skipped) | n/a                                 |
| `## Surfaced Requirements` | ≤ 8 lines (1 bullet per requirement) | full rationale per requirement       |
| `## Critic Tests`       | ≤ 8 lines (1 test signature per line)  | full failing-test output             |
| `## Security Audit`     | exactly 7 rows (1 per check, no spillover) | per-check reasoning, suppression rationale |
| `## Review`             | ≤ 6 rows in severity table             | full check-list reasoning, fix diffs |
| `## Review Findings`    | severity table only (≤ 6 rows, 1 line each) | full check-list reasoning, fix diffs go to `docs/{{ issue.identifier }}/review/details.md` |
| `## QA Evidence`        | header + commands + 1-line `**Verdict**` + AC table + AC Scorecard | raw pytest/curl/Playwright output |
| `## Learnings`          | ≤ 8 lines (3-4 bullets)                | extended rationale, follow-ups      |
| `## Wiki Updates`       | ≤ 4 lines                              | n/a (wiki is the source of truth)   |
| `## Human Review`       | ≤ 18 lines across all 6 sub-sections   | full evidence dump under docs/      |
| As-Is → To-Be Report    | ≤ 20 lines across all 4 sub-sections   | full evidence dump under docs/      |

**Style rules:**

- Cite the top 1-3 `path:line` anchors only; no function signatures,
  field lists, diff hunks, or per-line walks. Extra citations and raw
  command output go to `docs/{{ issue.identifier }}/<stage>/details.md`.
- Plain-language header and summary lines in English; code spans
  (`path:line`, identifiers, command output) stay as-is — never translate
  code symbols.
- Jargon needs one short parenthetical for a non-developer; longer
  explanations go to `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, don't tell. "200 passed" beats "all tests passed".
- The Plain-language headers alone (skipping every technical body) must
  tell the entire ticket end-to-end.
{% endif %}

## Hard rules (every stage)

- Never skip a stage. Never mark `Done` without `## QA Evidence`, a
  successful Learn Merge Gate into the target branch, and explicit human
  confirmation from `Human Review`.
- Never silence failing tests, hide errors, or add fake success paths.
  Fix the root cause or move the ticket to `Blocked`.
- Touch only what the ticket requires. No drive-by refactors.
- Record non-trivial decisions in `log/changelog-YYYY-MM-DD.md`
  (append; do not overwrite).
- Every artefact lives under `docs/{{ issue.identifier }}/<stage>/`
  (`mkdir -p` it yourself) — never in `qa-artifacts/`, `runs/`, ad-hoc
  `tests/e2e/<name>/`, or sibling `docs/` files. Learn's `docs/llm-wiki/`
  write-back is a sibling under `docs/`, not under this ticket's root.
- Backward transitions are pipeline, not failure: `Review → In Progress`
  (CRITICAL/HIGH/MEDIUM findings) and `QA → In Progress` (test/spec
  failure, including any server-reported HIGH issue). Each rewind starts
  the next In Progress turn with a fresh agent context; only the ticket
  body and `docs/{{ issue.identifier }}/` carry over — what you didn't
  write down is gone.
- Rewind cap: Symphony counts every rewind at runtime; exceeding
  `agent.max_attempts` ({{ agent.max_attempts }}) moves the ticket to
  `Blocked`. `max_attempts: 0` disables the cap.
