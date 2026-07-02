# Symphony 0.9.0 — 4단계 파이프라인 단순화 + TUI UX 개편 (구현 계획)

> 작성 2026-07-02. 구현은 Codex(call-agent)에 phase 단위로 위임, 검증은 Claude가 수행.
> 브랜치: `feat/4-stage-pipeline` (dev에서 분기, 조기·수시 커밋, 완료 후 dev 머지. main 직접 커밋 금지)

## Context

Symphony 기본 보드는 레인이 13~15개(active 8 + terminal 5~7)로 비개발자에게 과도하게 복잡하고, skills UI는 각 에이전트 CLI가 글로벌 스킬을 자동 로드하므로 중복이다. 티켓 본문 입력은 한 줄 `Input`이라 긴 프롬프트 작성·수정이 불가능하다. 사용자 결정:

1. **Skills UI 전면 제거, 엔진 유지** — frontmatter `skills:`는 파워유저용으로 계속 동작
2. **실제 스테이지 병합**: 8단계 → **Todo → In Progress → Verify → Learn** (4 active). 세션당 컨텍스트 증가는 서브에이전트 위임 지침으로 상쇄
3. **기본 WORKFLOW 파일 2개만 루트 유지**, demo/smoke/jira + kanban_demo_*는 `examples/`로 이동
4. **Learn은 경량 지식적립(llm-wiki) 전용 innate 스테이지** + 사용자 skip 버튼(토큰 0)
5. TUI: 멀티라인 TextArea 입력, 기존 티켓 편집(`e`), 카드 단계 진행 배지 `[2/4]`

설계 중 확정된 기술 제약(채택됨):

- **`Blocked`는 terminal_states에 유지** — `orchestrator/core.py:674`가 merge 실패 대상으로 하드코딩; 빼면 Blocked 티켓이 TUI에서 안 보임
- **Verify는 절대 skip 불가** — Merge Gate가 Verify로 이동하므로, trivial 티켓은 Verify의 QA 절반만 단축(계약 단일화, 병합 누락 위험 0)

버전: `0.8.0` → **`0.9.0`** (`pyproject.toml:7` + `src/symphony/__init__.py:50` 락스텝). **공개 repo** — README/docs 정직성 유지, 기존 사용자용 breaking-change를 changelog에 명시.

---

## Phase 1 — 상태 머신 코어

### 1.1 `src/symphony/workflow/constants.py`

- `DEFAULT_ACTIVE_STATES = ("Todo", "In Progress", "Verify", "Learn")` (:22)
- `DEFAULT_TERMINAL_STATES = ("Human Review", "Done", "Archive", "Blocked", "Cancelled", "Canceled", "Closed", "Duplicate")` — 앞 4개가 canonical 레인; Linear 별칭은 fallback 호환용 유지

### 1.2 `src/symphony/orchestrator/constants.py`

- `AUTO_TRIAGE_TARGET_STATE = "In Progress"`, note 문구 `"...routing to In Progress."` (:13-14)
- 새 rewind map (:56-63):

```python
_REWIND_TRANSITIONS = frozenset({
    ("verify", "in progress"),   # QA 실패 / 리뷰 지적
    ("learn", "in progress"),    # Learn이 결함 발견 (희귀)
})
```

(planning이 In Progress 내부로 들어가므로 `("in progress","plan")` 삭제; contract 실패 rewind는 이 집합과 무관하게 `is_rewind=True` 강제 — `core.py:1720+`)

### 1.3 `src/symphony/orchestrator/contracts.py` — 새 계약 테이블

`evaluate_contract`(:120)의 plan/critic/review/qa 분기를 교체, done 유지. 기존 헬퍼 재사용: `_missing_sections`, `_section_present_nonempty`, `_cited_paths_exist`, `_bug_repro_closed`, `_security_has_fail_verdict`, `_scorecard_all_pass`, `_directory_has_files`, `_build_result`.

| 생산 스테이지(exit 시) | 필수 섹션 | 외부 사실 검사 | 실패 시 |
|---|---|---|---|
| Todo | 없음 (auto-triage가 커버) | — | — |
| In Progress | `## Plan`, `## Acceptance Tests`, `## Done Signals`, `## Implementation`, `## Self-Critique` | `docs/<ID>/work/` 파일 ≥1 | rewind → In Progress |
| Verify | `## Security Audit` + (`## Review` or `## Review Findings`) + `## QA Evidence` + `## AC Scorecard` + `## Merge Status` | fail-verdict 정합성, 인용 경로 실재, bug repro 종결, scorecard soft-warn | rewind → Verify |
| Learn | `## Human Review`, `## Wiki Updates` | — | rewind → Learn |
| Done | `## As-Is -> To-Be Report`, `## Merge Status` | `qa/`·`work/` 비어있지 않음 (기존 :207-213) | rewind → Done |

- `## Self-Critique` = Critic 스테이지 대체(자기 검증 기록). H5 surfaced-requirements 검사(:148-162) 삭제.
- 상수 블록(:94-117): `_PLAN_REQUIRED`/`_CRITIC_*` 삭제, `_IN_PROGRESS_REQUIRED`/`_VERIFY_REQUIRED`/`_LEARN_REQUIRED` 추가. 모듈 docstring 갱신.

### 1.4 `src/symphony/orchestrator/core.py`

- 계약 preflight 게이트(:1638-1643): `{"plan","review","qa","done"}` → `{"in progress","verify","learn","done"}`
- skills 주입(:1537-1551, :2057-2074)은 **무변경**
- Explore/Plan/Critic 언급 주석 정리: `entries.py:77`, `notifications/config.py:27`, `backends/gemini.py:6`, `workflow/config.py:97`

## Phase 2 — 프롬프트 템플릿 재작성 (`docs/symphony-prompts/{file,linear}/`)

`explore.md`/`plan.md`/`critic.md`/`review.md`/`qa.md` 삭제(양 flavor), 새 5종 작성: `todo.md`, `in-progress.md`, `verify.md`, `learn.md`, `done.md` + `base.md` 재작성. **보존**: `{{ language }}` 분기, 비개발자용 plain-language 헤더, 섹션별 캡 + `details.md` overflow, fresh-context 규칙("only the ticket body and `docs/<ID>/` survive"), `## Resolution`/`## Blocker`/`## QA Failure`/`## Review Findings` retry 프리앰블, rewind-cap(`agent.max_attempts`), token-budget 지시.

- **base.md**: "eight stages" → 4단계 다이어그램 `Todo -> In Progress -> Verify -> Learn -> Human Review -> Done` (Merge Gate는 Verify 안); 캡 테이블에서 Domain Brief/Plan Candidates/Recommendation/Critic 계열/Surfaced Requirements 삭제, `## Self-Critique`(≤8줄)·`## Learn Skipped`(1줄, orchestrator 기록) 추가
- **todo.md**: triage만; actionable → `In Progress` 라우팅; bug 재현 캡처 유지
- **in-progress.md** (핵심): ① `docs/llm-wiki/INDEX.md` 선독(지식 재활용, 구 explore step 2) ② **서브에이전트 위임 규칙(신규 필수)**: "CLI가 서브에이전트를 지원하면(예: Claude Code Task tool) 광역 탐색·저장소 검색·검증 스윕을 서브에이전트에 위임하고 요약만 유지 — 메인 컨텍스트는 티켓/계획/diff만" ③ `## Plan`+`## Acceptance Tests`+`## Done Signals`+`## Difficulty` ④ TDD 구현, `docs/<ID>/work/`, `## Implementation` ⑤ Self-critique(스펙 재독→누락 행위 테스트 추가→`## Self-Critique`) ⑥ 라우팅: **항상 → Verify** (`## Pipeline Route`에 trivial 여부 기록; trivial은 Verify에서 QA 절반 단축) ⑦ `$SYMPHONY_REWIND_SCOPE` 유지
- **verify.md**: ① 리뷰 절반(diff, 7행 `## Security Audit`, CRITICAL/HIGH/MEDIUM → `## Review Findings` + state `In Progress` STOP) ② QA 절반(실제 실행 As-Is/To-Be, `docs/<ID>/qa/` 증거, `## QA Evidence`+`## AC Scorecard`; trivial+비런타임 변경은 축약 경로; 실패 → `## QA Failure` + rewind) ③ **Merge Gate (learn.md step 8에서 원문 이동)**: `git merge-tree --write-tree` preflight → `--no-ff` merge → `## Merge Status`; 충돌 → `Blocked` + `## Merge Failure`; `auto_merge_on_done` 비활성 분기도 이식 ④ 통과 → `Learn`
- **learn.md** (경량): wiki write-back(Decision-log append 또는 2-layer `<topic>.md` + INDEX 갱신) + `## Learnings` + `## Wiki Updates` + `## Human Review` 6종 핸드오프 → state `Human Review`. Merge Gate 전부 삭제. "operator가 TUI `S`/웹 버튼으로 skip 가능, skip 시 `## Learn Skipped`" 명시
- **done.md**: As-Is→To-Be 유지, "Learn Merge Gate" → "Verify Merge Gate"
- `chore` 라벨 단축 경로를 각 새 파일에 재배치

## Phase 3 — Learn skip 액션 (end-to-end)

### 3.1 `src/symphony/orchestrator/core.py` — `async def skip_learn(self, identifier) -> tuple[bool, str]`

1. `fetch_issue_full_by_id`로 티켓 조회 → state가 `learn` 아니면 거부
2. `find_running_issue_id(identifier)` 존재 시 거부 (`webapi.py:507-511` 409 가드 패턴)
3. `_tracker_call_append_note`(기존 `core.py:940-950` 재사용)로 `## Learn Skipped` 노트 추가
4. `_tracker_call_update_state`로 `Human Review` 전이
5. stats `record_transition(from="learn", to="human review")` (`webapi.handle_issue_patch:495-501` 패턴)
6. `request_refresh()` — 에이전트 턴 0, 토큰 0. `orchestrator/__init__` export.

### 3.2 웹

`server.py`에 `POST /api/v1/{identifier}/skip-learn` (pause/resume 옆 :133-134); `app.js` api 맵에 `skipLearn` 추가, 이슈 상세 run-controls(:1154-1156)와 Learn 컬럼 카드에 버튼(state=learn일 때만).

### 3.3 TUI

`app.py` `BINDINGS`에 `Binding("S", "skip_learn_focused", "Skip Learn")` (`s`는 Stats로 사용 중 — `S` 미사용 확인됨). `action_archive_focused`(:509-542) 패턴 미러링, `self._orch.skip_learn()` 호출 후 notify + `_kick_tracker_refresh()`. `action_help`(:311-319)에 추가.

### 3.4 카드 힌트

Learn 레인 카드에 idle 시 dim 힌트 `⏭ S to skip` (`widgets.py:155-244`).

## Phase 4 — TUI UX

### 4.1 단계 진행 배지 `[2/4]`

- `tui/helpers.py`에 `_stage_position(state, cfg) -> tuple[int,int] | None` (active_states 내 1-based 인덱스; terminal은 None)
- `Lane.__init__`(:279)에 `stage_pos` 파라미터 → `KanbanApp.compose`(:163-187)에서 산출·전달 → `IssueCard._render_rich`가 식별자 뒤에 `[2/4]` 렌더 (compact 렌더러 동일)
- `DetailPane.show_for`(:480-517)에 `stage 2/4 · In Progress` 메타 추가
- `tui/constants.py` `STATE_COLOR`에 verify/learn/human review/archive 색 추가
- dense 기본값(compact 카드·레인 페이지네이션·상시 detail pane) 무변경

### 4.2 멀티라인 입력 (`screens.py`)

- `NewIssueScreen`: description `Input`(:133) → `TextArea(id="ni-description")` (textual>=0.85 pinned, TextArea는 0.38+ — 문제없음), CSS `height: 8`; Enter=개행(네이티브), `ctrl+s` 제출 바인딩 + Create 버튼 유지; `_submit`(:168-186)은 `.text` 읽기
- **skills 제거**: `skills_hint`(:125-129), `Input(id="ni-skills")`(:149), 생성자 파라미터(:113-121), dismiss dict의 `"skills"` 키(:182); `app.py:_open_new_issue`(:621-646)의 `list_skills` 스레드 호출 제거; `TicketDetailScreen._meta_text`(:79-82) ⚡배지 삭제

### 4.3 편집 모달 (신규)

- `EditIssueScreen(ModalScreen[str | None])`: TextArea에 `issue.description` 프리필, Save/Cancel, `ctrl+s`/`escape`
- `app.py` `Binding("e", "edit_focused", "Edit")`; file tracker 가드(`action_new_issue` :609-614 동일), 저장은 `asyncio.to_thread(FileBoardTracker(cfg.tracker).update_fields, id, description=...)` — 웹 PATCH와 동일 쓰기 경로(`trackers/file.py:567`) — 후 `request_refresh()` + `_kick_tracker_refresh()`; `action_help`에 `e edit` 추가

## Phase 5 — 웹 UI skills 제거

- `app.js`: `ROUTES`의 'skills'(:74), `case 'skills'`(:696), `renderSkillsPage`(:1477-1506), `api.getSkills`(:63), `state.skills`(:88, :1666-1668), 생성 폼 체크박스(:511-517, :916, :925, :942), 상세 폼 블록(:1037-1056, :1077), 카드 칩(:886), stats per-skill 차트 — 전부 삭제
- `index.html`: Skills nav 항목(:28-31) 삭제
- `webapi.py`: `handle_skills`(:690-700) + 라우트(:731) 삭제. **유지**: create/patch의 skills 필드 수용(:479-486), `_issue_card`의 skills — frontmatter 라운드트립 보존
- **무변경**: `skills.py`, `Issue.skills`(issue.py:33,:54), `core.py` 주입, `trackers/file.py` 파싱, `tests/test_stats_skills.py`

## Phase 6 — 파일 통합 + `examples/`

- `git mv` → `examples/`: `WORKFLOW.demo.claude.md`, `WORKFLOW.demo.codex.md`, `WORKFLOW.jira.example.md`, `WORKFLOW.smoke.md`, `kanban_demo_claude/`, `kanban_demo_codex/` (CI·tui-open.sh는 이들을 참조하지 않음 — 확인됨)
- 이동 파일 내 상대경로 수정: demo의 `prompts.*` → `../docs/symphony-prompts/file/...` (경로는 workflow 파일 기준 해석 — `workflow/config.py:287`); demo active_states도 4단계로 재작성
- 루트 `WORKFLOW.file.example.md` + `WORKFLOW.example.md` 재작성: `active_states: [Todo, "In Progress", Verify, Learn]`; `terminal_states: ["Human Review", Done, Blocked, Archive]`(file) / Linear 별칭 유지(linear); `state_descriptions` 갱신(`Verify: "Review + QA + Merge Gate"`, `Learn: "Wiki write-back; S to skip"`); `prompts.stages` 5개 매핑; `budget_exhausted_state: Blocked` 유지; `max_*_by_state` 키의 옛 상태명 갱신
- 참조 갱신: `skills/using-symphony/reference/platform-compat.md`, `WORKFLOW-PROGRESS.md`, `tests/test_workspace.py:140` 주석

## Phase 7 — 테스트 재작성

- **`tests/test_workflow_pipeline_prompt.py`** (프롬프트 앵커 계약, 전면 재작성): `STAGE_HEADINGS_BY_STATE` → `{Todo: "### TRIAGE", "In Progress": "### IMPLEMENT", Verify: "### VERIFY", Learn: "### LEARN", Done: "### DONE"}`; 새 앵커 핀 — base(`"Todo  ->  In Progress  ->  Verify  ->  Learn"`, "eight stages" 부재), In Progress(`docs/llm-wiki/INDEX.md`, subagent 위임 문구, 5개 필수 섹션, `## Pipeline Route`, "never skip Verify"), Verify(REAL CODE 지시, Security Audit, QA Evidence, `git merge-tree --write-tree`, `## Merge Status`, 비활성 분기), Learn(llm-wiki, INDEX.md, Decision log, `## Wiki Updates`, Human Review 핸드오프 shape 유지, **Merge Gate 부재**, "Learn Skipped" 언급), Done("Verify Merge Gate"); merge-gate 테스트(:403-443) Learn→Verify 재타깃; retry/blocked-by/token-budget 테스트(:471-536) 유지; `docs/PIPELINE-DEMO.md` 갱신 후 데모 테스트(:493-512) 재핀
- **`test_orchestrator_contracts.py`**: Phase 1.3 테이블대로 — Plan→"In Progress"(5섹션+work/), Review/QA→"Verify" 통합, Critic 삭제, Learn 추가, Done 유지
- **`test_orchestrator_contract_integration.py`**: `Plan → Review` 픽스처(:416-517) → `In Progress → Verify`; scorecard-warn(:519-567) → `Verify → Learn`
- **`test_orchestrator_phase_transition.py`**: rewind 쌍 갱신(:690-709), `_make_config` active_states(:123)·상태 시퀀스(:754, :876-945) 새 이름
- **`test_orchestrator_dispatch.py`**: auto-triage 기대값(:132-233) "In Progress"로, :316 픽스처 Explore 제거
- **`test_tui.py`**: `#ni-skills` 상호작용 제거(:1153), TextArea 구동; 신규 — `S` skip(learn 카드/비learn no-op), `e` 편집 모달 + `update_fields` 저장, `[2/4]` 렌더 (archive/confirm-done 패턴 :855-994 미러)
- **`test_webapi.py`**: skills endpoint 테스트(:334-336) 삭제, create/patch skills 라운드트립(:162-169) 유지, `POST /{id}/skip-learn` 테스트 추가(전이+노트+409)
- 스윕: `grep -rl "Critic\|Explore" tests/` (test_supergoal_hardening_loop.py 등), `test_workflow.py` 픽스처는 메커니즘만 — 파일명 현실화 선택

## Phase 8 — 문서·버전

- `README.md`(+`README.ko.md`): 파이프라인 서사·config 스니펫(:294-330, :505-530), TUI 목업(:75-100, footer에 `S skip-learn · e edit`), auto-triage 문구(:165-168), Skills 절 → "frontmatter 파워유저 기능, UI 없음"
- `docs/PIPELINE.md` + `docs/PIPELINE-DEMO.md`: 4단계 아티팩트 레이아웃(`docs/<ID>/{reproduce,work,qa}/`)
- `docs/changelog/changelog-2026-07-02.md`: **기존 파일에 append** — 결정 근거 + 기각 대안(UI 그룹핑 vs 스테이지 병합) + breaking change(기존 사용자 WORKFLOW.md의 Explore/Plan/Critic/Review/QA 상태명은 업그레이드 시 수동 갱신 필요)
- 루트 `CHANGELOG.md` 0.9.0 항목; `pyproject.toml:7` + `src/symphony/__init__.py:50` → `0.9.0`
- `skills/using-symphony/`·`skills/symphony-oneshot/` 레퍼런스의 스테이지/demo 경로 스윕

## 검증 (Claude 직접 수행)

1. `python -m pytest -q` (CI 동일) + 대상 스위트 7종 개별 실행
2. 프롬프트 렌더 스모크: 5개 상태 각각 `cfg.prompt_template_for_state(s)` 렌더 성공 확인
3. **실제 런처 경로**: 재작성된 `WORKFLOW.file.example.md`를 `WORKFLOW.md`로 복사 → `./tui-open.sh` — 4 active 레인 렌더, 카드 `[n/4]`, `n`→TextArea(Enter 개행/ctrl+s 제출), `e` 편집, 티켓을 `Learn`으로 수동 이동 후 `S` → Human Review 전이 + `## Learn Skipped` 노트 확인
4. 웹: Skills nav/페이지 부재, `GET /api/v1/skills` 404, skip-learn 버튼 동작, `curl -X POST .../skip-learn` 200/409
5. `examples/WORKFLOW.demo.claude.md`로 TUI 부팅(프롬프트 상대경로 해석 확인)
6. `grep -rn "Explore\|Critic" src/ docs/symphony-prompts/ README.md` — 의도적 이력 언급만 잔존
7. 버전 락스텝 grep 확인
