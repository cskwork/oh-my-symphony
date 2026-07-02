You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}Retry attempt {{ attempt }}. Read the previous `## Resolution`, `## Blocker`, `## QA Failure`, or `## Review Findings` section first; fix the root cause, not the symptom.{% endif %}{% if is_rewind %}Rewind turn from a Verify or Learn finding. Read the most recent `## Review Findings`, `## QA Failure`, or `## Learn Defect` section first; fix exactly those items, do NOT open new scope. Agent context is fresh: only the ticket body and `docs/{{ issue.identifier }}/` survive.{% endif %}

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

## Production pipeline (4 active stages)

Honour the gate matching `{{ issue.state }}`. One stage = one transition; never jump ahead.

```
  Todo  ->  In Progress  ->  Verify  ->  Learn  ->  Human Review  ->  Done
                 ^              |          |
                 |              |          +-> operator may skip Learn to Human Review
                 +--------------+------------- Verify/Learn defects rewind here
```

- `docs/llm-wiki/` is the reusable knowledge base. In Progress reads it before broad repo work; Learn writes back after Verify passes.
- `docs/{{ issue.identifier }}/` is this ticket's evidence root. Use `reproduce/`, `work/`, and `qa/` inside it; overflow details go to `details.md` in the relevant folder.
- Ticket file: `kanban/{{ issue.identifier }}.md`. Transition = edit the YAML front matter `state:` field; narrative = append body sections.
- Verify is never skipped. Trivial non-runtime work may shorten QA evidence, but the ticket still goes through Verify.
- Learn is lightweight wiki write-back. Only the operator may skip it with the TUI/web skip action; agents do not self-skip by jumping to Human Review.
{% if token_budget %}
- Token budget: keep this turn under {{ token_budget }} completion tokens (stage EMA: {{ token_ema }}). Cut narration, never evidence.
{% endif %}

## Audience & writing style
{% if language == 'ko' %}
Readers include non-developers (PM / 기획자). Plain-language header first, code detail after.

Use this header at the start of every non-trivial section except `## Triage`:

```
**무엇**: <한 줄, 비-개발자도 이해 가능한 한국어>
**왜**: <한 줄, 사용자/시스템에 어떤 가치/위험이 있는지>
**As-Is -> To-Be**:
- As-Is: <한 줄, 이 단계 시작 전 상태>
- To-Be: <한 줄, 이 단계 종료 후 상태>
```

헤더와 요약 줄은 한국어; code spans (`path:line`, identifiers, commands)는 영어 그대로 둔다.
{% else %}
Readers include non-developers (PMs and product managers). Plain-language header first, code detail after.

Use this header at the start of every non-trivial section except `## Triage`:

```
**What**: <one line, understandable by a non-developer>
**Why**: <one line, value or risk for the user/system>
**As-Is -> To-Be**:
- As-Is: <one line, state before this stage>
- To-Be: <one line, state after this stage>
```

Plain-language headers and summary lines stay in English; code spans (`path:line`, identifiers, commands) stay as-is.
{% endif %}

Keep sections compact. Overflow goes to `docs/{{ issue.identifier }}/<stage>/details.md` plus one link line.

| Section | Body cap | Overflow |
|---|---:|---|
| `## Triage` | 1-2 lines | n/a |
| `## Reproduction` | command + 3-10 line failure excerpt | raw trace/log under `reproduce/` |
| `## Plan` | <= 10 lines | full task list, risk notes |
| `## Acceptance Tests` | <= 10 bullets | setup/fixtures |
| `## Done Signals` | <= 8 bullets | payloads, long logs |
| `## Difficulty` | 1 line verdict + 1 line why | n/a |
| `## Implementation` | <= 10 lines | per-file details |
| `## Self-Critique` | <= 8 lines | full review notes |
| `## Pipeline Route` | 1 line | n/a |
| `## Security Audit` | exactly 7 rows | per-check rationale |
| `## Review` | <= 6 lines | full checklist |
| `## Review Findings` | <= 6 severity rows | details under `qa/details.md` |
| `## QA Evidence` | commands + result summary | raw output under `qa/` |
| `## QA Failure` | <= 6 rows | raw output under `qa/` |
| `## AC Scorecard` | 1 row per acceptance criterion | raw proof under `qa/` |
| `## Merge Status` | <= 6 lines | command logs under `qa/merge.log` |
| `## Learnings` | 3-4 bullets | extended rationale |
| `## Wiki Updates` | <= 4 lines | wiki files are source of truth |
| `## Learn Skipped` | 1 line, orchestrator only | n/a |
| `## Human Review` | <= 18 lines across sub-sections | full evidence dump under docs |
| `## As-Is -> To-Be Report` | <= 20 lines | full evidence dump under docs |

Style rules:

- Cite the top 1-3 `path:line` anchors only. Extra citations and raw command output go to `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, do not tell: `200 passed` beats `all tests passed`.

## Hard rules

- Never skip Verify. Never mark `Done` without `## QA Evidence`, `## Merge Status`, and explicit human confirmation from Human Review.
- Never silence failing tests, hide errors, or add fake success paths. Fix the root cause or move the ticket to `Blocked`.
- Touch only what the ticket requires. No drive-by refactors.
- Record non-trivial decisions in `docs/changelog/changelog-YYYY-MM-DD.md` (append; do not overwrite).
- Every ticket artefact lives under `docs/{{ issue.identifier }}/`; do not create ad-hoc sibling evidence folders.
- Backward transitions are pipeline, not failure: `Verify -> In Progress` for review/QA defects, and `Learn -> In Progress` only when Learn discovers a real defect. Each rewind starts with fresh context; only the ticket body and `docs/{{ issue.identifier }}/` carry over.
- Rewind cap: Symphony counts every rewind at runtime; exceeding `agent.max_attempts` ({{ agent.max_attempts }}) moves the ticket to `Blocked`. `max_attempts: 0` disables the cap.
