# Handoff — 웹 대시보드 Git 페이지 · Chat 페이지

- **브랜치**: `feat/add-git-repo-menu` (base: `dev` @ `78ed44b`)
- **작성일**: 2026-08-06 (2차 개정: 토큰 스트리밍·예산·멀티 세션·Git 액션 추가)
- **상태**: 구현·자동화 테스트 완료, 실 claude CLI로 스트리밍/예산 라이브 검증 완료. dev 머지/푸시는 미실행 (operator 결정 사항)

---

## 1. 추가된 기능

### Git 페이지 (`#/git`)
연결된 host repo의 git 상태를 대시보드에서 직접 관리한다.

| 기능 | 설명 |
|---|---|
| Task branches | `symphony/<ID>` 브랜치 ↔ kanban 티켓 매핑, merged/`↑ahead ↓behind`/running 배지 |
| History | repo 전체 또는 브랜치별 커밋 로그 (sha, ref 칩, 작성자·시간) |
| Compare | merge 미리보기 — target 대비 커밋 목록 + 파일별 diffstat |
| Changes 패널 (우측 고정) | Compare 로드/커밋 클릭 시 파일별 접이식 unified diff (+초록/−빨강) |
| **수동 merge** | task 브랜치를 target으로 merge. 자동 Verify 게이트와 동일 엔진(`auto_merge_on_done_best_effort`) 재사용 — exclude_paths·`--no-ff`·충돌 사전검사·dirty 가드 동일. Merge Gate 실패로 Blocked된 티켓 복구 용도 포함 |
| **Push** | task 브랜치 push. 타깃 브랜치는 브랜치명 재입력 확인을 거쳐야 하며, **force push는 어떤 경로로도 불가** |
| **PR** | `gh pr create` 위임. gh 미설치·브랜치 미푸시·비GitHub 리모트를 각각 다른 코드로 보고 |
| **Delete** | 미머지 브랜치는 force 없이는 거부, 체크아웃 중인 브랜치·러닝 워커 브랜치도 거부 |

### Chat 페이지 (`#/chat`)
WORKFLOW.md에 연결된 agent(claude/codex/...)와 host repo에 대해 실시간 대화.

| 기능 | 설명 |
|---|---|
| Q&A 모드 | 읽기 전용 (claude `--permission-mode plan`, codex read-only sandbox). repo 질의응답 |
| Edit 모드 | 같은 워킹트리에서 공동 작업 — 파일 수정, **kanban 이슈 등록** (턴 종료 시 `request_refresh()`로 즉시 픽업) |
| WebSocket 스트리밍 | 중간 메시지·툴 활동 실시간 표시, 재연결 시 seq 중복 제거 + 최근 100개 복원 |
| **토큰 단위 타이핑** | claude `--include-partial-messages` 델타를 파싱해 live 버블에 이어붙임. 델타는 seq 없음 · transcript/JSONL 미기록 · **포커스된 소켓에만** 전송 (배경 세션 홍수 방지). 완료 메시지 도착 시 마크다운으로 확정 |
| **멀티 세션** | 동시 최대 3개. 세션별 턴 락·seq·transcript로 한 세션의 긴 턴이 다른 세션을 막지 않음. 탭으로 전환 |
| **재접속** | 서버 재시작 후에도 `.symphony/chat/index.json` + JSONL로 복원. claude는 `--resume`으로 agent 컨텍스트까지, 그 외 백엔드는 프리앰블 재주입 |
| **예산 경고** | 세션 시작 시 턴/토큰 상한 지정(0=무제한). 초과 시 빨간 칩 + 1회 경고, **차단은 하지 않음** |
| 모드 전환 | claude는 `--resume`으로 대화 유지 + 다음 메시지에 모드 변경 공지 프리펜드, codex는 세션 재시작(경고 표시) |
| 글자 크기 | 기본 15px, A−/A+ 버튼 12–20px, localStorage 저장 |
| 기록 | 메모리 500개 + `<repo>/.symphony/chat/<세션>.jsonl` 영구 기록 (서버 재시작 시 라이브 세션은 종료, 파일은 잔존) |

### 안전 장치
- merge: `symphony/*` 접두사 화이트리스트 → 러닝 워커 409 → 단일 실행 락 → auto_merge 자체 가드(RC 41~53)
- chat: 워커 슬롯 미점유(티켓 굶김 없음), 턴 단일 실행, loopback Host 가드 + **WS cross-origin 403**, mutation은 REST(JSON 강제)만
- 티켓 상태 전이는 절대 자동으로 하지 않음 (merge 후 Blocked 해제는 기존 Recover 플로우)

---

## 2. 코드 맵

| 파일 | 역할 |
|---|---|
| `src/symphony/utils/git_inspect.py` | read-only git 조회 (log/branches/task-branches/compare/diff). 실패 시 빈 결과로 degrade |
| `src/symphony/utils/git_ops.py` | **변경계** git 작업 (delete/push/PR/remote). 실패를 삼키지 않고 `GitOpResult`로 보고. force push 불가가 모듈 불변식 |
| `src/symphony/chat.py` | ChatManager — 세션 수명주기, 모드 변형(`cfg_for_mode`), 이벤트 fan-out, JSONL writer |
| `src/symphony/webapi.py` | `_register_git_routes` (`/api/v1/git/*`), `_register_chat_routes` (`/api/v1/chat/*` + WS) |
| `src/symphony/errors.py` | `ChatBusyError` / `ChatSessionExistsError` / `ChatNoSessionError` |
| `src/symphony/workflow/constants.py` | `SYMPHONY_BRANCH_PREFIX` (하드코딩 추출) |
| `src/symphony/web/static/{index.html,app.js,style.css}` | 사이드바 2메뉴 + Git/Chat 페이지 + WS 클라이언트 |
| `tests/test_git_inspect.py` (11) | git 조회 단위 (temp repo) |
| `tests/test_git_ops.py` (6) | 변경계 git 단위 — force 금지·미머지 거부·리모트명 검증 |
| `tests/test_webapi.py` (+git 21) | git REST 계약 + 실 merge/push/delete E2E (로컬 bare 리모트) |
| `tests/test_chat.py` (23) | ChatManager 단위 (fake backend) — 델타·예산·멀티 세션·재접속 |
| `tests/test_webapi_chat.py` (9) | chat REST/WS 계약 (단수 alias + 복수형 + reattach + focus) |
| `tests/test_web_static_contract.py` (8) | SPA 문자열 계약 |

무수정: `backends/*`, `orchestrator/*`(상수 교체 3줄 제외), `server.py`, `cli/main.py`, `workflow/config.py`

---

## 3. 실행 방법

### A. 실서버 (이 repo)
```bash
symphony run ./WORKFLOW.md          # 또는 symphony service start
# 브라우저: http://127.0.0.1:9999/#/git , #/chat
```
- 사이드바 표시등이 **초록**이어야 정상.
- ⚠️ 실서버는 kanban의 Todo 티켓을 실제로 dispatch한다. QA만 하려면 아래 스텁 서버 권장.

### B. QA용 스텁 데모 서버 (dispatch 없음, board+git+chat 모두 동작)

아래 전문을 `/tmp/symphony_qa_demo.py`로 저장 후 실행한다.

```python
# /tmp/symphony_qa_demo.py — QA demo: full web UI over a scratch repo.
# Chat은 실제 agent CLI를 스폰한다 (토큰 소모).
import os, subprocess, sys, tempfile
from pathlib import Path
from typing import cast
from aiohttp import web

REPO = Path("/Users/chaeseong-gug/Documents/PARA/Project/Git/symphony-multi-agent")
sys.path.insert(0, str(REPO / "tests"))
from test_webapi import _StubOrchestrator, WORKFLOW_TEXT, TICKET  # 풀 스텁: board까지 동작

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


tmp = Path(tempfile.mkdtemp(prefix="symphony-qa-"))
(tmp / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
stages = tmp / "prompts" / "stages"; stages.mkdir(parents=True)
(stages / "todo.md").write_text("todo"); (stages / "doing.md").write_text("doing")
(tmp / "kanban").mkdir(); (tmp / "kanban" / "SEED-1.md").write_text(TICKET, encoding="utf-8")
(tmp / "README.md").write_text("# qa-demo\n"); (tmp / "calc.py").write_text("def add(a, b):\n    return a + b\n")
_git(tmp, "init", "-q", "-b", "main"); _git(tmp, "add", "-A"); _git(tmp, "commit", "-q", "-m", "init board")
_git(tmp, "checkout", "-q", "-b", "symphony/SEED-1"); (tmp / "feature.py").write_text("print('hi')\n")
_git(tmp, "add", "feature.py"); _git(tmp, "commit", "-q", "-m", "SEED-1: feature"); _git(tmp, "checkout", "-q", "main")

# Push / PR / remote-status를 오프라인으로 확인하기 위한 로컬 bare 리모트
bare = tmp.parent / (tmp.name + "-origin.git")
_git(tmp, "init", "-q", "--bare", str(bare)); _git(tmp, "remote", "add", "origin", str(bare))

state = WorkflowState(tmp / "WORKFLOW.md"); cfg, err = state.reload(); assert err is None, err
print(f"demo board: {tmp}", flush=True); print(f"demo remote: {bare}", flush=True)
web.run_app(
    build_app(cast(Orchestrator, _StubOrchestrator(state))),
    host="127.0.0.1",
    port=int(os.environ.get("SYMPHONY_QA_PORT", "9920")),
)
```
```bash
.venv/bin/python /tmp/symphony_qa_demo.py   # http://127.0.0.1:9920
```
- 이 스텁은 board 표면까지 구현하므로 표시등이 초록이다. **chat은 실 agent CLI를 스폰**하므로 토큰이 소모된다.
- 참고: 채팅 전용 최소 스텁(test_webapi_chat의 것)을 쓰면 board가 500이라 "Orchestrator unreachable"가 뜨는데, 이는 **스텁 한계이지 버그가 아니다** — 채팅 라우트는 오케스트레이터를 사용하지 않아 정상 동작한다.

---

## 4. 자동화 테스트

```bash
source .venv/bin/activate
python -m pytest -q                 # 전체
python -m pytest -q tests/test_git_inspect.py tests/test_webapi.py \
    tests/test_chat.py tests/test_webapi_chat.py tests/test_web_static_contract.py
python -m ruff check .
symphony-pyright
symphony doctor ./WORKFLOW.md
```

**기대 결과**: `1492 passed, 6 skipped, 1 failed` — 실패는
`test_continuous_improvement.py::test_run_continuous_improvement_real_git_target_worktree_e2e`
하나로, **base 커밋(78ed44b)에서도 동일하게 실패하는 이 머신의 기존 실패**다 (이번 변경과 무관, CI에서는 통과 이력 있음).

---

## 5. 수동 QA 체크리스트 — Git 페이지

스텁 데모 서버(§3-B) 기준. `DEMO=<demo board 출력 경로>`, `B=http://127.0.0.1:9920/api/v1`.

> 1차: G1·G2·G4·G5, §7 API 스모크, C11 보안 검사를 §3-B 스크립트로 실행해 확인했다
> (merge 커밋 생성 + `Manual Merge` 노트 기록까지).
> 2차: G8·G9·G10·G11(로컬 bare 리모트), C1·C12·C13·C14 일부(세션 생성/한도/정지/재접속),
> C15(예산 경고)를 실 claude CLI 1턴으로 라이브 확인했다 — 델타 프레임 수신, `seq: null`,
> JSONL 미기록, 한도 초과 후에도 턴이 실행되는 것까지.
> 나머지 UI 조작 항목(브라우저에서 눈으로 봐야 하는 것)은 인수자가 실행한다.

- [ ] **G1 조회**: `#/git` 진입 → Task branches에 `symphony/SEED-1`(티켓 칩 `SEED-1 · Todo`, `↑1 ↓0`), History에 커밋 2개(ref 칩 포함)
- [ ] **G2 Compare**: SEED-1 행의 [Compare] → Compare 카드에 `↑1 ↓0`, 커밋 1개, diffstat `feature.py +1` / 우측 Changes 패널에 `feature.py` unified diff(`+print('hi')` 초록)
- [ ] **G3 커밋 diff**: History의 커밋 행 클릭 → Changes 패널에 해당 커밋 patch (머지 커밋은 헤더만 — git show의 정상 동작)
- [ ] **G4 수동 merge**: SEED-1 행 [Merge] → 모달(target=main) → Merge → 성공 토스트, 행이 `merged` 배지 + 버튼 비활성, History에 `merge: SEED-1 ...` 커밋
  ```bash
  git -C "$DEMO" log --oneline main | head -2     # merge 커밋 확인
  grep -A2 "Manual Merge" "$DEMO/kanban/SEED-1.md" # 티켓 노트 확인
  ```
- [ ] **G5 merge 가드**: merged 상태에서 Merge 버튼 비활성 확인. curl로 직접 검증:
  ```bash
  curl -s -X POST http://127.0.0.1:9920/api/v1/git/merge \
    -H 'Content-Type: application/json' -d '{"branch":"main"}'          # 400 (task 브랜치만)
  curl -s -X POST http://127.0.0.1:9920/api/v1/git/merge \
    -H 'Content-Type: application/json' -d '{"branch":"symphony/../x"}' # 400
  ```
- [ ] **G6 degrade**: git repo가 아닌 보드에서 200 + `note: "not_a_git_repo"` (자동 테스트 커버, 수동 생략 가능)
- [ ] **G7 실서버 통합** (선택): 실서버에서 Merge Gate 실패로 Blocked된 티켓의 브랜치를 수동 merge → 티켓 노트 확인 → 보드에서 Recover
- [ ] **G8 Push**: SEED-1 행 [Push] → 성공 토스트. `git -C "$DEMO" ls-remote --heads origin`에 `symphony/SEED-1` 표시.
      리모트가 없으면 Push/PR 버튼이 비활성 + "No git remote configured" 안내
- [ ] **G9 타깃 push 확인 절차**: Merge target 줄의 [Push target] → 브랜치명을 정확히 입력해야 활성. 오타 시 400 `confirm_required`
  ```bash
  curl -s -X POST $B/git/push -H 'Content-Type: application/json' -d '{"branch":"main"}'                  # 400 confirm_required
  curl -s -X POST $B/git/push -H 'Content-Type: application/json' -d '{"branch":"main","confirm":"main"}' # 200
  ```
- [ ] **G10 Delete 가드**: 미머지 SEED-1 행 [Delete] → force 체크박스가 기본 ON + 경고 문구. 체크 해제 후 제출 시 409 `not_merged`.
      merge 후에는 force 없이 삭제 성공, 목록에서 사라짐
  ```bash
  curl -s -X POST $B/git/branch/delete -H 'Content-Type: application/json' -d '{"branch":"symphony/SEED-1"}' # 409 not_merged
  curl -s -X POST $B/git/branch/delete -H 'Content-Type: application/json' -d '{"branch":"main"}'            # 400 (task 브랜치만)
  ```
- [ ] **G11 PR**: gh 로그인된 실 GitHub 리모트에서만 유효. 데모(로컬 bare 리모트)에서는 409 `not_a_github_remote`가 정상.
      gh 미설치 시 버튼 비활성 + 400 `gh_unavailable`, 미푸시 브랜치는 409 `branch_not_pushed`

## 6. 수동 QA 체크리스트 — Chat 페이지

⚠️ 실 agent CLI 스폰 — 토큰 소모.

- [ ] **C1 세션 시작**: `#/chat` → [+ New] → 모달(모드 `Q&A`, 턴/토큰 상한) → Start session → 세션 탭 생성, `claude` 배지 + 예산 칩, Q&A/Edit 토글, 입력창 활성
- [ ] **C2 질의응답 스트리밍**: "이 repo에 어떤 파일이 있어?" 전송 → 사용자 말풍선(우측), "Agent is working…" 인디케이터, 툴 활동 줄(mono), 마크다운 답변 말풍선. 진행 중 입력창 비활성
- [ ] **C3 qa 읽기 전용**: qa 모드에서 "파일 만들어줘" → 에이전트가 거부하고 edit 모드 안내
- [ ] **C4 모드 전환**: [Edit] 클릭 → (claude) 대화 문맥 유지 확인 — 이전 대화 내용을 참조하는 후속 질문에 정상 답변
- [ ] **C5 공동 작업**: edit 모드에서 "calc.py에 subtract 함수 추가해줘" → `git -C "$DEMO" diff calc.py`로 실제 변경 확인
- [ ] **C6 이슈 등록**: "kanban 보드에 multiply 함수 추가 이슈 등록해줘 (priority 2, label demo)" → `ls "$DEMO/kanban/"`에 새 `.md`, front matter(`state: Todo`, priority, labels) 확인. Board 페이지에서 새 티켓 표시
- [ ] **C7 busy 가드**: 턴 진행 중 입력창·Send 비활성. curl 이중 전송 시 409 `chat_busy`
- [ ] **C8 WS 재연결**: 페이지 새로고침 → 최근 대화 복원(중복 없음). 서버 껐다 켜면 재연결 백오프 후 hello 수신
- [ ] **C9 폰트**: A+ 수회 → 말풍선·툴 줄·입력창 동반 확대 → 새로고침 후 유지 (localStorage `symphony.chatFontSize`)
- [ ] **C10 기록/정리**: `ls "$DEMO/.symphony/chat/"`에 세션 JSONL. [Stop] → 스냅샷 `{active:false}`. 서버 종료 시 agent 프로세스 잔존 없음(`pgrep -f "claude -p"`)
- [ ] **C12 토큰 타이핑**: 긴 답변을 요구하는 질문(예: "이 repo 구조를 5문단으로 설명해줘") → 글자가 한 번에 나타나지 않고 **깜빡이는 커서와 함께 흘러나오는지**, 완료 시 마크다운으로 바뀌는지 확인.
      기록에는 델타가 남지 않아야 한다: `grep -c agent_delta "$DEMO/.symphony/chat/"*.jsonl` → 0
- [ ] **C13 멀티 세션**: [+ New]로 2번째 세션 → 탭 2개. 한 세션에서 긴 질문 실행 중(점 애니메이션) 다른 탭으로 전환해 **동시에 질문 가능**한지 확인. 3개까지만 생성되고 4번째는 [+ New] 비활성
- [ ] **C14 재접속**: 세션 [Stop] → 서버 재시작 → `#/chat`의 `Resume…` 드롭다운에 이전 세션 표시 → 선택 → 대화 내용 복원.
      claude면 이전 맥락을 참조하는 후속 질문("방금 뭐라고 했지?")에 정상 답변
- [ ] **C15 예산 경고**: 턴 상한 1로 세션 생성 → 한 번 질문 → 칩이 빨갛게 바뀌고 "chat budget reached" 상태 줄 1회 표시.
      **그래도 다음 질문이 전송되는지**(차단 없음) 확인
- [ ] **C11 보안**:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: http://evil.example' \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Key: dGVzdA==' -H 'Sec-WebSocket-Version: 13' \
    http://127.0.0.1:9920/api/v1/chat/ws                        # 403
  curl -s -o /dev/null -w '%{http_code}\n' -X POST -d 'x' \
    http://127.0.0.1:9920/api/v1/chat/message                   # 415 (JSON 강제)
  ```

## 7. API 레퍼런스 스모크

```bash
B=http://127.0.0.1:9920/api/v1
curl -s $B/git/branches | jq .
curl -s "$B/git/log?limit=5" | jq '.commits[].subject'
curl -s $B/git/task-branches | jq '.branches[0]'
curl -s "$B/git/compare?branch=symphony/SEED-1" | jq '.stat.total'
curl -s "$B/git/diff?branch=symphony/SEED-1" | jq -r '.patch' | head
curl -s $B/git/remote-status | jq .
curl -s $B/chat/session | jq .           # 단수형 alias — 활성 세션
curl -s $B/chat/sessions | jq '{live: (.sessions|length), resumable: (.resumable|length), max: .max_sessions}'
# 세션 생성 → 메시지 → 재접속 → 종료 (복수형)
SID=$(curl -s -X POST $B/chat/sessions -H 'Content-Type: application/json' \
  -d '{"mode":"qa","max_turns":5,"max_tokens":200000}' | jq -r .session_id)
curl -s -X POST $B/chat/sessions/$SID/message -H 'Content-Type: application/json' \
  -d '{"text":"hi"}' -o /dev/null -w '%{http_code}\n'                       # 202
curl -s -X DELETE -H 'Content-Type: application/json' -d '{}' $B/chat/sessions/$SID | jq .
curl -s -X POST $B/chat/sessions/$SID/reattach -H 'Content-Type: application/json' -d '{}' \
  | jq '{active, turn_count, tail: (.transcript_tail|length)}'
curl -s -X DELETE -H 'Content-Type: application/json' -d '{}' "$B/chat/sessions/$SID?forget=true" | jq .                 # 인덱스에서 제거(JSONL은 보존)
# git 변경계
curl -s -X POST $B/git/push -H 'Content-Type: application/json' -d '{"branch":"symphony/SEED-1"}' | jq .
curl -s -X POST $B/git/branch/delete -H 'Content-Type: application/json' \
  -d '{"branch":"symphony/SEED-1","force":true}' | jq .
```

## 8. 알려진 제약 · 의도된 동작

- **스텁 데모의 "Orchestrator unreachable"**: 채팅 전용 최소 스텁은 `/api/v1/board`를 못 채워 표시등이 빨강 — 버그 아님. §3-B의 풀 스텁 또는 실서버에서는 초록
- 채팅 예산은 **경고 전용**이다 — 한도를 넘겨도 턴은 계속 실행된다(운영자 판단 사항). 컨텍스트 관리(auto-compact)는 여전히 agent CLI에 위임
- 서버 재시작 시 라이브 세션은 소멸하되 `Resume…`으로 재접속할 수 있다. claude 외 백엔드는 agent 컨텍스트를 복원할 수 없어 프리앰블을 다시 보낸다
- 토큰 델타는 claude 전용이다 — codex 등은 델타를 내보내지 않아 완료 메시지가 한 번에 표시된다(정상 degrade)
- 델타는 재연결로 복구되지 않는다(기록하지 않으므로). 턴 중간에 새로고침하면 타이핑 효과만 놓치고 완성된 답변은 받는다
- `gh pr create`는 gh의 인증 상태에 전적으로 의존한다. Symphony는 토큰을 보관하지 않는다
- gemini/agy/kiro/opencode/pi는 read-only 강제 불가 → `read-only not enforced` 배지 + 프리앰블 소프트 제어
- Linear/Jira 보드: git task-branches의 `ticket`이 null로 degrade, 채팅 이슈 등록 프리앰블 미주입 (file 보드 전용)
- `_apply_dispatch_env`의 전역 env와 채팅 스폰이 겹치면 정보성 `SYMPHONY_TOKEN_*`이 새어들 수 있음 (무해, chat.py docstring 문서화)
- merge는 자동 게이트와 동일하게 호스트 repo에서 `git checkout $TARGET`을 수행

## 9. 후속 아이디어 (미구현)

세션별 agent kind 선택 UI(API는 이미 `agent_kind`를 받는다) · 세션 이름 직접 수정 · 델타의 짧은 링버퍼 보관으로 재연결 시 타이핑 복원 · PR 템플릿 파일 연동 · `git fetch`/원격 브랜치 조회

## 10. 반출 절차

```bash
git push                            # 훅이 차단 시 operator가 직접 실행
# dev 머지는 PR 또는 로컬 --no-ff 머지로 operator가 결정
```
