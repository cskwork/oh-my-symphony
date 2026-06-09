### LEARN  -- when state is `Learn`

**Allowed tools (advisory).** Read `docs/{{ issue.identifier }}/{explore,plan,work,qa}/` and prior ticket sections. Write `docs/llm-wiki/` (entries + INDEX.md) and tracker comments. Run `git merge-tree` and Merge Gate commands when `auto_merge_on_done` is true. Do NOT edit source — knowledge capture + merge, not implementation.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label — no new domain knowledge to file:
1. Post a one-line `## Learnings` ("chore — metadata-only change, no new invariants captured") and `## Wiki Updates` ("none — chore short-circuit") comment.
2. Skip the Decision-log row, the beginner / Technical Reference block, and the `INDEX.md` refresh.
3. Still **run whichever Merge Gate clause step 8 below renders**, exactly as written — the short-circuit cannot bypass it.
4. Transition per step 8/9 (or `Blocked` on a committed merge conflict, exactly as the standard flow specifies).
{% endif %}{% endfor %}
Distill what this ticket taught into `docs/llm-wiki/` for **both developers and non-developers**.

1. Read `docs/{{ issue.identifier }}/{explore,plan,work,qa}/` and prior Linear comments (Recommendation, Plan, Implementation, QA Evidence) end-to-end.
2. Compare brief vs reality: assumptions that held or broke, constraints/invariants that only surfaced now, prior wiki entries that were incomplete or misleading.
3. Update `docs/llm-wiki/`: either append `YYYY-MM-DD | <issue.identifier> | note` to an existing entry's Decision log and refresh **Last updated**, OR create `docs/llm-wiki/<topic-slug>.md` from the template below; then add/refresh its row in `INDEX.md` (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).

   Each entry stacks two layers in one file: a **beginner explainer** (PM, designer, junior dev — absorbed in two minutes), then a **technical reference** for the next engineer.

{% if language == 'ko' %}
   ```
   # <Topic Title>

   ## 감 잡기 (For Beginners)

   ### <주제>를 왜 쓰는지 감 잡기

   <주제가 왜 필요한지, 현실에서 어디에 쓰이는지 2-3문장. 전문용어 없이.>

   초보자는 처음에 이렇게 이해하면 된다.

   `핵심 흐름: A → B → C`

   이 단계에서 외워야 할 핵심 용어는 5개다.

   | 용어 | 초보자식 설명 |
   |---|---|
   | 용어 1 | 사전식 정의가 아니라 비유나 일상어로 풀어쓴 한 줄 |
   | 용어 2 | ... |
   | 용어 3 | ... |
   | 용어 4 | ... |
   | 용어 5 | ... |

   예를 들어 설명하면:

   <이 주제가 실제로 동작하는 현실적인 예시 한 토막. 코드 X, 시나리오 O.>

   이 단계에서 중요한 판단 기준은 이것이다.

   **이것만 기억하면 된다: <한 문장 핵심 정리>**

   나중에 더 깊게 들어가면 <다음에 배울 내용 / 관련 wiki 항목>을 보면 된다.

   ## Technical Reference

   **Summary:** 한 문단으로 정리한 기술 개요 (개발자 청중).

   **Invariants & Constraints:**
   - ...

   **Files of interest:**
   - `path/to/file.py:123` — what the line region does.

   **Observability hooks:**
   - log: `<event_name>` at `path:line` — 어떤 상황을 신호하는지
   - metric: `<metric_name>` at `path:line` — 무엇을 세는지
   - trace: `<span_name>` at `path:line` — 어디서 어디까지 감싸는지
   (관측 표면이 없는 순수 유틸리티면 `- none` 한 줄. QA/Review는 `none`을 강제하지 않는다.)

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   `## 감 잡기` 규칙: 사전식 정의 금지 ("X는 마치 ~처럼 동작한다" 식으로); 화살표 3-5단계, 용어 표 정확히 5개, takeaway 정확히 한 문장; 비즈니스 도메인 비유 우선, 엣지 케이스·내부 구현은 "나중에 배울 내용"으로.
   블록이 없으면 추가; 있으면 이번 티켓이 비유나 핵심 흐름을 무너뜨렸을 때만 수정 (사소한 wording은 Decision log row로 충분).
{% else %}
   ```
   # <Topic Title>

   ## Getting the Feel (For Beginners)

   ### Why <topic> exists

   <2-3 sentences on why this topic is needed and where it shows up in real life. No jargon.>

   The simplest way for a beginner to picture it:

   `Core flow: A → B → C`

   There are five terms you need to internalise at this stage.

   | Term | Plain-English meaning |
   |---|---|
   | Term 1 | One line in everyday language, not a dictionary definition |
   | Term 2 | ... |
   | Term 3 | ... |
   | Term 4 | ... |
   | Term 5 | ... |

   To make it concrete:

   <One realistic scenario showing this topic in action. No code — describe what happens.>

   The decision rule that matters at this stage:

   **Just remember this: <one-sentence takeaway>**

   When you're ready to go deeper, read <next topic / related wiki entry>.

   ## Technical Reference

   **Summary:** one-paragraph technical overview (developer audience).

   **Invariants & Constraints:**
   - ...

   **Files of interest:**
   - `path/to/file.py:123` — what the line region does.

   **Observability hooks:**
   - log: `<event_name>` at `path:line` — what it signals
   - metric: `<metric_name>` at `path:line` — what it counts
   - trace: `<span_name>` at `path:line` — what it spans
   (No observability surface — a pure utility module — write `- none` and stop. QA/Review do not enforce on `none`.)

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   `## Getting the Feel` rules: no dictionary definitions ("X behaves like ...", never "X is defined as ..."); arrow flow 3-5 steps, table exactly 5 terms, takeaway exactly one sentence; business-domain analogies first, edge cases / internals deferred to "ready to go deeper".
   Add the block if absent; if present, touch it only when this ticket invalidated the analogy or core flow — small wording tweaks belong in the Decision log.
{% endif %}

4. Wiki integrity (ticket-level only):
   - If this ticket invalidated an entry, update it now and log the prior wrong claim in the Decision log.
   - Cross-entry contradiction noticed in passing → post a `Wiki Conflict` comment pointing at both files (do not fix here).
   - Bulk dup/orphan/stale/missing-file sweeping belongs to `symphony wiki-sweep` (auto every `wiki.sweep_every_n` Done transitions; `symphony wiki-sweep --root docs/llm-wiki --dry-run` on demand). Do NOT re-do it by hand.
5. Commit wiki edits onto the ticket's PR (same branch, not a separate PR).
6. Post a Learn comment with `## Learnings` (3-4 bullets of new facts/constraints/surprises) and `## Wiki Updates` (paths created/modified/removed, one line each with a changelog tag: `merged`, `created`, `marked stale`, `dropped orphan row`, `updated invariant`, `added beginner block`, `refreshed beginner block`).
7. Post a `## Human Review` comment — succinct, for the human who confirms Done:
   - `### What Changed` — 2-3 bullets, user/system-visible change.
   - `### Why It Matters` — 1-2 bullets, value or risk reduced.
   - `### Evidence` — commands/proofs, each with pass/fail and top evidence path.
   - `### Risks` — residual risks, follow-ups, or `none`.
   - `### Human Checklist` — 3-5 quickly verifiable checkboxes.
   - `### Decision Needed` — exactly one line: `Confirm Done` or `Do not confirm; move back to <state> because <reason>`.
{% if agent.auto_merge_on_done %}
8. Merge Gate — after Learn and before setting state to `Human Review`, prove and merge this ticket's feature branch into the target branch:
   - Resolve target in order: `agent.auto_merge_target_branch`, `agent.feature_base_branch`, current host branch.
   - First run `git merge-tree --write-tree <target-branch> symphony/{{ issue.identifier }}` from the host repo — checks the committed merge without requiring a clean worktree.
   - Do not use `git status -uno --porcelain` as the merge proof. A dirty host worktree is a separate safety check, not proof of a committed target/branch merge conflict.
   - If `git merge-tree --write-tree` reports a committed target/branch merge conflict: move the issue to `Blocked`, post a `Merge Failure` comment with the exact command, target branch, and conflicted paths.
   - If the committed merge is clean: check whether host dirty tracked files overlap `git diff --name-only <target-branch>..symphony/{{ issue.identifier }}`. Block only on actual overlap or workspace-only path changes.
   - If safe: create the explicit merge commit on the target branch, record the merge SHA in a `Merge Status` comment.
9. Transition state to `Human Review`. If nothing new, say so in the Learn comment and still transition only after the Merge Gate succeeds. Do not set `Done`; a human must confirm.
{% else %}
8. Merge Gate is disabled (`agent.auto_merge_on_done` is false). Post a `Merge Status` comment noting this workflow intentionally leaves branch integration to the operator.
9. Transition state to `Human Review` after the Learn evidence is complete. Do not set `Done`; a human must confirm.
{% endif %}
