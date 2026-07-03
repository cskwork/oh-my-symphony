# oh-my-symphony

**[English](README.md) | 한국어**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg)](https://www.python.org/)
[![Tests](https://github.com/cskwork/oh-my-symphony/actions/workflows/tests.yml/badge.svg)](https://github.com/cskwork/oh-my-symphony/actions/workflows/tests.yml)
[![GitHub stars](https://img.shields.io/github/stars/cskwork/oh-my-symphony?style=social)](https://github.com/cskwork/oh-my-symphony/stargazers)

> 하나의 터미널, 하나의 칸반 보드, 다섯 개의 AI 코딩 에이전트
> (**Codex**, **Claude Code**, **Gemini**, **OpenCode**, **Pi**) — 티켓마다 골라 쓰고,
> 병렬로 실행하며, 실시간으로 지켜본다.

![symphony tui screenshot](docs/tui-screenshot.svg)

<sub>`symphony tui ./WORKFLOW.md` — 컬럼은 트래커의 상태이고, 카드는 현재 에이전트, 턴 수, 마지막 이벤트, 누적 토큰을 보여준다. 실시간 표시: ● 실행 중, ↻ 재시도 대기, ✓ 완료.</sub>

**AI 코딩 CLI를 더 이상 저글링하지 말자.** Symphony는 각 칸반 티켓을
원하는 에이전트에 넘기고, 격리된 `git worktree` 워크스페이스에서 동시에 실행하며,
실시간 진행 상황 — 턴 수, 토큰 사용량, 레이트 리밋 여유 — 을
터미널을 벗어날 필요 없는 Jira 스타일 TUI로 보여준다.

[**AI CLI 없이 60초 만에 체험하기 →**](#try-it-in-60-seconds-no-agent-cli-required)

## 목차

- [Symphony를 쓰는 이유](#why-symphony)
- [작동 방식](#how-it-works)
- [에이전트 선택](#pick-an-agent)
- [설치](#install)
- [60초 체험](#try-it-in-60-seconds-no-agent-cli-required)
- [첫 작업 Quickstart](#quickstart--your-first-task-end-to-end)
- [실행](#run)
- [구조](#layout)
- [테스트](#tests)
- [설계 메모](#design-notes)
- [아직 구현하지 않은 것](#what-is-not-implemented)

## Why Symphony?

- **벤더 종속 없음.** Codex ↔ Claude Code ↔ Gemini ↔ OpenCode ↔ Pi를 YAML 한 줄로
  바꾸거나, 티켓마다 백엔드를 섞어 쓴다. 새 에이전트(Ollama, 로컬 모델,
  CLI가 있는 무엇이든)는 오케스트레이터를 바꾸지 않고 얇은
  `AgentBackend` Protocol 뒤에 끼워 넣으면 된다.
- **에이전트가 실제로 무엇을 하는지 본다.** 실시간 칸반은 모든 실행 중 카드의
  턴 수, 마지막 이벤트, 누적 토큰, 레이트 리밋 여유를 보여준다.
  "멈춘 건가, 생각 중인 건가?" 더 이상 헷갈릴 일이 없고 — 로그인할 SaaS
  대시보드도 없다.
- **수십 개의 티켓을 병렬로, 무인으로 돌린다.** 동시성은 기본 내장 — 모든
  티켓이 자체 `git worktree` 워크스페이스를 가져서 에이전트끼리 충돌하지 않는다.
  Headless 모드는 진행 상황을 어떤 에디터에서든 `tail -F`할 수 있는 Markdown
  파일로 미러링하고, macOS 절전 방지는 잠금 화면이 야간 파이프라인을
  중단시키는 것을 막는다.
- **체험에 SaaS도, API 키도, 가입도 필요 없다.** 파일 기반 Markdown 칸반이므로
  티켓이 코드 옆 `git`에 함께 산다. Linear와 Jira는 외부 트래커로 지원되지만,
  Symphony를 체험하는 데는 둘 다 필요하지 않다.
- **검증된 기반 위에 로컬 운영 안정성을 더했다.**
  [OpenAI의 공식 Symphony 레퍼런스 구현](https://github.com/openai/symphony)에서
  포크했다. 이 포크는 파일 우선 오케스트레이션 모델을 유지하면서 다섯 개의
  백엔드, TUI / 웹 운영 화면, SQLite 실행 lease, 재시작에도 보존되는 이슈
  플래그, 잠금 기반 Markdown 티켓 쓰기를 더한다.
- **뷰어가 아니라 진짜 웹 앱.** 오케스트레이터 포트가 Linear 스타일 보드를
  직접 서빙한다: 이슈 등록, 드래그로 컬럼 이동, 컬럼 추가/삭제/이름변경,
  컬럼별 스테이지 프롬프트 편집, 브랜치 정책 선택, 워커 Pause / Resume,
  Learn 스킵, 그리고 전용 통계 페이지(일별 토큰, 컬럼별 체류 시간,
  에이전트별 합계)까지. 모든 편집은 주석을 보존한 채 `WORKFLOW.md`로
  왕복 저장된다.
- **운영자급 도구가 기본 제공.** `symphony doctor`는 첫 실행에서 가장 흔한
  다섯 가지 실패(포트 충돌, CLI 누락, 자리표시자 URL, 쓰기 불가 워크스페이스)를
  한 번에 잡아낸다. `symphony service start/stop/restart/logs`는 오케스트레이터를
  관리형 백그라운드 서비스로 실행한다.

## Who is this for?

- 자는 동안 수십 개 티켓에 걸쳐 무인 야간 리팩터링을 돌리는 **1인 개발자**.
- 버그 수정, 문서 갱신, 마이그레이션 티켓을 여러 코딩 에이전트에 걸쳐 동시에
  병렬화하는 **팀**.
- 동일한 프롬프트와 워크스페이스로 Codex, Claude Code, Gemini, OpenCode, Pi가
  같은 작업을 어떻게 처리하는지 나란히 비교하는 **연구자와 리뷰어**.
- "에이전트당 채팅 창 하나"의 한계에 부딪혀, 한눈에 읽히는 칸반을 갖춘 진짜
  오케스트레이터를 원하는 **누구든**.

## How it works

<details>
<summary>Plain-text version of the TUI (for terminals viewing raw README)</summary>

```text
  agent=codex  tracker=linear  workflow=WORKFLOW.md  lang=en   running=2  retrying=1   │  tokens in=84,200 out=27,640 total=111,840
                                                                                       │  rate-limits=requests_remaining=4823, tokens_remaining=1.2M

╭── Todo [1/4] (3) ╮ ╭── In Progress [2/4] ╮ ╭── Verify [3/4] ╮ ╭── Learn [4/4] ╮ ╭── Done (2) ──╮ ╭── detail ───────────────────────╮
│  DEMO-120 [1/4]  │ │  DEMO-104 [2/4] ●   │ │  DEMO-122 [3/4]│ │  DEMO-123     │ │  DEMO-088    │ │  DEMO-104 [2/4]                 │
│  Migrate auth …  │ │  Fix race condi…    │ │  Review + QA   │ │  S skip       │ │  Drop dead-… │ │  Fix race condition in pagina…  │
│  #backend …      │ │  turn 4  20,180t    │ │  #docs         │ │  Wiki notes   │ │  DEMO-091    │ │                                 │
│                  │ │  Patched cursor…    │ ╰────────────────╯ ╰───────────────╯ │  Bump deps…  │ │  state=In Progress              │
│  DEMO-111  ↻ P2  │ │                     │                    ╰──────────────╯                     │  runtime=running                │
│  Refactor cach…  │ │  DEMO-098  ●  P2    │                                                         │  turn=4                         │
│  retry #2  tur…  │ │  Add /api/sear…     │                                                         │  in=14,200  out=5,980           │
│                  │ │  turn 2  11,310t    │                                                         │  total=20,180                   │
│  DEMO-121  P2    │ │  Added token-bu…    │                                                         │  Patched cursor advance;        │
│  Wire feature …  │ ╰─────────────────────╯                                                         │  running test suite...          │
│  blocked by D…   │                                                                                 ╰─────────────────────────────────╯
╰──────────────────╯

q quit · r refresh · enter details · n new · e edit · s stats · S skip Learn · P pause/resume · / filter · ?
```

</details>

[OpenAI Symphony 레퍼런스 구현](https://github.com/openai/symphony)의 멀티 에이전트 포크.
업스트림은 트래커(Linear 또는 로컬 Markdown 칸반)를 폴링해 이슈별 워크스페이스
안에서 Codex 세션을 실행한다. 이 포크는 그 오케스트레이터를 유지하면서 다음을
더한다:

1. 다섯 개의 구체 어댑터를 가진 플러그형 **AgentBackend** 레이어:
   - **Codex** — `codex app-server` (JSON-RPC stdio, 멀티턴) — 원본
   - **Claude Code** — `claude -p --output-format stream-json --verbose`
     (NDJSON 이벤트, `--resume`를 쓰는 턴별 서브프로세스)
   - **Gemini** — `gemini -p ""` (턴당 1회 호출, stdin 프롬프트 → stdout 결과)
   - **OpenCode** — `opencode run --format json --auto` (턴당 1회 호출,
     문서화된 `message` 인자로 프롬프트 전달, 세션 ID 확인 후 `--session` 재개)
   - **Pi** — `pi --mode json -p ""` (JSONL 이벤트, `--session` 재개를 쓰는
     턴별 서브프로세스; 하나의 CLI 아래에서 Anthropic / OpenAI / Gemini / Bedrock
     백엔드를 지원 — [pi.dev](https://pi.dev) 참고)
2. [Textual](https://textual.textualize.io) 기반 **Jira 스타일 CLI 칸반 TUI**.
   컬럼은 트래커 상태이고, 카드는 현재 에이전트, 턴 수, 마지막 이벤트, 누적
   토큰을 보여준다. 카드는 포커스할 수 있고, 마우스 휠로 각 레인을 스크롤하며,
   카드에서 `enter`를 누르면 전체 상세 모달이, `n`으로 멀티라인 새 티켓 등록,
   `e`로 포커스 티켓 편집, `S`로 Learn 스킵, `s`로 통계 화면이 열린다.
3. 오케스트레이터 포트에 내장된 **웹 칸반 앱** — 이슈 CRUD, Learn 스킵,
   드래그 앤 드롭 상태 이동, 컬럼 추가/삭제/이름변경, 컬럼별 프롬프트
   편집, 브랜치 정책, 전용 통계 페이지.
4. `.symphony/state.db`의 **단일 노드 신뢰성 ledger** — 활성 실행 lease가
   재시작 뒤 중복 디스패치를 막고, 죽은 프로세스 소유 lease를 회수하며,
   retry / pause / budget-exhausted 플래그를 프로세스 종료 뒤에도 보존한다.

아키텍처는 의도적으로 로컬 / 파일 우선이다. Markdown 티켓은 사람이 읽고 고치는
진실의 원천이고, SQLite는 손으로 편집하지 않아야 하는 런타임 조정 상태를 저장한다.

## Pick an agent

`WORKFLOW.md`에서 `agent.kind`를 설정한다:

```yaml
agent:
  kind: claude          # codex | claude | gemini | opencode | pi

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

pi:
  command: pi --mode json -p ""
  resume_across_turns: true
  turn_timeout_ms: 3600000
```

각 백엔드는 자기 블록(`codex`, `claude`, `gemini`, `opencode`, `pi`)을 읽으며, 런타임에는
`agent.kind`에 맞는 것만 사용된다. Codex `linear_graphql` 클라이언트 도구는
`agent.kind=codex`일 때만 노출된다.

`agent.kind`는 전역 기본값이다. 파일 보드 티켓은 티켓 frontmatter를 추가해 다른
백엔드를 선택할 수 있다:

```yaml
agent:
  kind: codex
```

손으로 편집한 카드에는 플랫 별칭 `agent_kind: codex`도 허용된다.
모든 백엔드 명령과 타임아웃 설정은 여전히 `WORKFLOW.md`의 해당 전역
`codex:`, `claude:`, `gemini:`, `opencode:`, `pi:` 블록에서 가져온다.
CLI에서 파일 보드 티켓을 만들 때는
`symphony board new TASK-2 "title" --agent-kind codex`를 쓴다.

파일 보드 워크플로에서 `agent.auto_triage_actionable_todo`는 기본값이
`true`다: 본문과 `Acceptance Criteria` 섹션이 있는 Todo 티켓은 모델 턴을 쓰지 않고
한 줄짜리 `## Triage` 노트와 함께 In Progress로 이동한다. 버그 티켓, 블록된 티켓,
모호한 티켓, 그리고 Linear 트래커는 여전히 Todo 프롬프트를 사용한다.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

해당 CLI를 `$PATH`에서 사용할 수 있게 한다:

| `agent.kind` | required CLI on `$PATH` |
|--------------|------------------------|
| `codex`      | `codex` (with `app-server` subcommand) |
| `claude`     | `claude` (Claude Code) |
| `gemini`     | `gemini` (Gemini CLI)  |
| `opencode`   | `opencode` (OpenCode CLI — `npm install -g opencode-ai`로 설치, `opencode auth login`으로 provider 인증) |
| `pi`         | `pi` (Pi coding-agent — `npm i -g @earendil-works/pi-coding-agent` or `curl -fsSL https://pi.dev/install.sh \| sh`; sign in once via `pi` → `/login` (OAuth, credentials cached at `~/.pi/agent/auth.json`) — no env var needed) |

## Try it in 60 seconds (no agent CLI required)

실제 에이전트 CLI를 설치하기 전에 TUI가 카드를 옮기는 모습을 먼저 보고
싶은가? 번들로 제공되는 **목(mock) 백엔드**를 쓰면 된다 — Codex와 동일한 JSON-RPC
프로토콜을 말하지만 실제 작업은 하지 않고, 턴을 시뮬레이션하며 토큰 사용량 틱을
내보낼 뿐이다.

```bash
git clone https://github.com/cskwork/oh-my-symphony.git
cd oh-my-symphony
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 목 백엔드를 가리키는 WORKFLOW.md
cat > WORKFLOW.md <<'YAML'
---
tracker: { kind: file, board_root: ./kanban,
           active_states: [Todo, "In Progress", Verify, Learn],
           terminal_states: ["Human Review", Done, Blocked, Archive] }
polling: { interval_ms: 5000 }
workspace: { root: ~/symphony_workspaces }
hooks:
  after_create: ": noop"
  before_run:   ": noop"
  after_run:    "echo done"
agent:  { kind: codex, max_concurrent_agents: 1, max_turns: 3, max_total_turns: 60 }
codex:  { command: python -m symphony.mock_codex }
server: { port: 9999 }
---
You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
YAML

symphony board init ./kanban
symphony board new TASK-1 "smoke test"
symphony tui ./WORKFLOW.md
```

약 5초 안에 TASK-1이 **Todo** 컬럼에서 초록색 ● 표시와 함께 턴 카운터와 토큰
합계가 올라가며 자라난다. 충분히 봤으면 `Ctrl-C`로 종료하고, 아래의 실제
워크스루로 넘어간다.

> 목 환경에서는 카드가 원래 컬럼에 머문다 — 카드를 **Done**으로 옮기려면 실제
> 에이전트가 `kanban/TASK-1.md`를 다시 써야 한다. 목은 LLM 호출 없이도
> 오케스트레이터 → 백엔드 → 워크스페이스 → hooks 파이프라인이 end-to-end로
> 동작함을 증명하기 위해 존재한다.

> 목의 튜닝 옵션: `SYMPHONY_MOCK_TURN_SECONDS=12`,
> `SYMPHONY_MOCK_FAIL_EVERY_N_TURNS=3` 등 — `src/symphony/mock_codex.py` 참고.

---

## Preflight — `symphony doctor`

실행하기 전에 설정을 점검한다:

```bash
symphony doctor ./WORKFLOW.md
```

출력(점검 항목당 한 줄):

```
PASS  server.port=9999              127.0.0.1:9999 is free
PASS  agent.kind=claude             claude → /usr/local/bin/claude
FAIL  hooks.after_create            contains placeholder 'my-org/my-repo' — every dispatch will fail with rc=128. Switch to the worktree default or replace with a real clone / `: noop`.
PASS  workspace.root=~/symphony_workspaces  exists and is writable
PASS  tracker.board_root            ./kanban (3 tickets)
```

모든 점검을 통과하면 종료 코드는 `0`, 하나라도 FAIL이면 `1`, `WORKFLOW.md`
자체를 로드할 수 없으면 `2`다. doctor는 첫 실행에서 가장 흔한 실패를 한 번에
잡아낸다: 포트 충돌, `$PATH`의 CLI 누락, 기본 제공되는 자리표시자 클론 URL,
쓰기 불가 워크스페이스, 보드 디렉터리 누락.

## Prove It Works

`doctor`가 통과하면 같은 워크플로를 런타임 표면으로 증명한다:

```bash
symphony ./WORKFLOW.md --port 9999
curl -s http://127.0.0.1:9999/api/v1/health
symphony runs ./WORKFLOW.md --limit 5
python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999
```

`/api/v1/health`는 `starting`, `ok`, `degraded` 중 하나를 보고한다.
`symphony runs`는 최근 registry 실행 시도를 출력하고, smoke 스크립트는 health,
state, board, static asset, 이슈 CRUD, refresh, workflow, stats를 확인한다.

---

## Quickstart — your first task end-to-end

깨끗한 클론에서 실행 중인 티켓까지, 파일 기반 트래커와 Claude Code를 에이전트로
사용해 따라간다.

### 1. Initialize the board

```bash
symphony board init ./kanban
# → initialized board at ./kanban, sample ticket DEMO-001.md
```

각 티켓은 `kanban/<ID>.md`에 YAML frontmatter를 가진 하나의 Markdown 파일이다.
오케스트레이터는 티켓 파일을 **읽기만** 하고, 에이전트가 상태를 전환할 때 그것을
**쓴다**.

### 2. Author `WORKFLOW.md`

**파일 트래커** 예제를 사용한다(다른 하나인 `WORKFLOW.example.md`는 Linear를
가리키며 API 키가 필요하다):

```bash
cp WORKFLOW.file.example.md WORKFLOW.md
```

첫 실행 점검에서 중요한 네 개의 블록:

```yaml
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Blocked, Archive]

workspace:
  root: ~/symphony_workspaces

hooks:
  # 각 티켓은 workspace.root/<ID>에 자체 워크스페이스를 갖는다.
  # 기본 제공 설정은 이를 호스트 레포의 `git worktree`로 `symphony/<ID>`
  # 브랜치에 붙인다 — 호스트 작업 트리는 그대로 둔다.
  # 호스트 레포 없이 실험할 때는 대신 `: noop`을 쓴다.
  after_create: |
    : noop                       # ← swap for the worktree default in WORKFLOW.file.example.md
  before_run: |
    : noop                       # runs before every agent turn
  after_run: |
    echo "run finished at $(date)"

prompts:
  # Symphony는 base와 티켓의 현재 상태에 해당하는 파일만 보낸다.
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
```

> ⚠ 기본 제공되는 `WORKFLOW.example.md` / `WORKFLOW.file.example.md`는 티켓별
> 워크스페이스를 호스트 레포(`WORKFLOW.md`가 있는 디렉터리)의 **git worktree**로
> `symphony/<ID>` 브랜치에 붙이는 것을 기본값으로 한다. 호스트 작업 트리는
> 절대 건드리지 않으며, 만족스러우면 `git -C <host> merge symphony/<ID>`로
> (또는 그 브랜치에서 PR을 열어) 결과를 다시 머지한다 — 명시적 운영자 동작이며,
> 절대 자동이 아니다.
>
> 코드가 WORKFLOW.md 레포와 *다른* 원격에 있다면, hook을
> `git clone <remote> .`로 바꾼다. 레포 없이 실험할 때는 `: noop`을 쓴다.

### 3. Add a ticket

```bash
symphony board new TASK-1 "Fix flaky pagination test" \
  --priority 2 \
  --labels backend,test \
  --description "tests/test_pagination.py::test_cursor_advance is flaky on CI."
# → created kanban/TASK-1.md
```

확인:

```bash
symphony board ls                    # all tickets
symphony board ls --state Todo       # filter by state
symphony board show TASK-1           # full body
```

### 4. Launch the TUI

```bash
symphony tui ./WORKFLOW.md
```

한 번의 폴 틱(`polling.interval_ms`, 기본 30초) 안에 오케스트레이터가 워커를
디스패치하고, 카드에 초록색 ● 표시(턴 카운터와 토큰 합계 포함)가 생기며,
에이전트가 실행된다. 성공하면 에이전트가 `kanban/TASK-1.md`를 다시 써서
`state: Done`을 설정하고 `## Resolution` 섹션을 덧붙인다 — 그 파일 수정이
카드를 **Todo** 컬럼에서 **Done**으로 옮기는 것이다. `Ctrl-C`로 종료한다.

> 카드는 티켓 파일의 `state` 필드를 기준으로 컬럼에 배치된다(`tui.py`가 매 틱
> 그것을 읽는다). 초록색 ● 표시는 카드 위에 겹쳐지며, 카드가 어느 컬럼에
> 있는지를 **바꾸지 않는다**. 따라서 실행 중인 티켓은 에이전트가 직접 파일을
> 다시 쓸 때까지 **Todo**에 머문다 — 이는 설계된 동작이다(오케스트레이터는 티켓
> 파일을 읽기만 하고, 쓰기는 에이전트가 담당한다).

> TUI는 실제 터미널(TTY)이 필요하다. 스크립트 / 백그라운드 프로세스 / 비대화형
> 셸에서 실행하면 프로세스가 조용히 종료된다 — 항상 포그라운드 터미널에서
> 실행한다.

### 4b. Headless mode + `WORKFLOW-PROGRESS.md`

칸반 UI를 열지 않고 오케스트레이터를 실행하려면 `tui`를 뺀다:

```bash
symphony ./WORKFLOW.md                  # headless; progress mirror auto-on
symphony ./WORKFLOW.md --no-progress-md # headless; no progress file
```

실시간 `WORKFLOW-PROGRESS.md`가 매 틱(기본 약 30초)과 그 사이의 모든 상태 변화
시점에 워크플로 파일 옆에 다시 쓰인다. TTY 없이 따라가려면 에디터에서 그 파일을
열면 된다:

```markdown
# Symphony Progress
_Updated: 2026-05-16 14:22:31 UTC_

## Kanban
| State        | Tickets |
|--------------|---------|
| Todo         | OLV-005, OLV-006 |
| In Progress  | OLV-002 (8m12s · 12k tok) |
| Verify       | OLV-001 |
| Done         | OLV-003, OLV-004 |

## Recent transitions
- `2026-05-16 14:22:31Z`  **OLV-002**  Todo → In Progress
- `2026-05-16 14:18:04Z`  **OLV-001**  In Progress → Verify
```

위치나 한도는 `WORKFLOW.md` frontmatter(또는 `--progress-md-path`)로 재정의한다:

```yaml
progress:
  enabled: true                     # default true; CLI --no-progress-md wins
  path: docs/STATUS.md              # default: WORKFLOW-PROGRESS.md beside WORKFLOW.md
  max_transitions: 20               # how many recent transitions to keep
```

이 미러는 읽기 전용 출력이다 — Symphony가 파일을 원자적으로 다시 쓰므로 손으로
편집하지 않는다.

#### macOS keep-awake

실행이 진행되는 동안 Symphony는 macOS에서 화면 깨우기 잠금을 유지해 화면 보호기 /
잠금 화면이 길게 도는 무인 파이프라인을 중단하지 못하게 한다(프로세스 자체는
어느 쪽이든 괜찮지만, 잠긴 디스플레이는 운영자의 주의를 막고 많은 자동 일시 중단
정책을 작동시킨다). 실행마다 `--no-keep-awake`로 끄거나, `WORKFLOW.md`에
영속시킨다:

```yaml
system:
  keep_awake: false   # default true; CLI --no-keep-awake also wins
```

macOS가 아닌 호스트는 `keep_awake_skipped`를 로깅하고 화면 깨우기 잠금 없이
계속 진행한다.

#### Slack notifications (optional)

Slack 인커밍 웹훅 URL을 설정해 옵트인한다. 아래 블록을 `WORKFLOW.md`에 넣으면
Symphony가 트래커 상태 전환마다 메시지를 하나씩 게시한다. 블록을 생략하면 아무것도
전송되지 않는다 — 기능은 기본적으로 완전히 꺼져 있다.

```yaml
notifications:
  slack:
    webhook_url: $SLACK_WEBHOOK_URL    # required; $VAR resolved at load time
    enabled: true                       # default true when webhook is set
    notify_on_states: []                # empty = every transition; or e.g. [Done, Blocked]
    templates:                          # optional per-state overrides
      Done: "✅ ${identifier} ${title} (${workflow})"
      Blocked: "🚧 ${identifier} blocked — ${title}"
    username: Symphony
    icon_emoji: ":robot_face:"
    timeout_ms: 5000
```

템플릿 자리표시자: `${identifier}` `${title}` `${prev_state}`
`${next_state}` `${workflow}` `${reason}`. 잘못된 템플릿은 알 수 없는 키를
문자 그대로 렌더링한다 — 절대 예외를 던지지 않는다. 네트워크 오류는 잡혀서
로깅되므로(`slack_notify_network_error`) Slack 장애가 오케스트레이터의 전환
경로를 막을 수 없다.

### 5. Inspect the result

```bash
symphony board show TASK-1               # the agent's ## Resolution lives in the body
ls ~/symphony_workspaces/TASK-1          # workspace it operated in
```

Symphony는 구조화된 로그를 **stderr로만** 쓴다. 보존하려면 실행 시
리다이렉트한다:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
# or, while running headless:
symphony ./WORKFLOW.md --port 9999 2>&1 | tee -a log/symphony.log
```

그러면 `tail -F log/symphony.log`가 동작한다.

### 6. Move tickets manually (rare)

```bash
symphony board mv TASK-1 Blocked         # forces a state transition
```

오케스트레이터는 다음 폴 틱에 재평가한다. 수동 전환은 막힌 것을 푸는 용도다 —
보통은 `WORKFLOW.md`로 설정된 단계별 프롬프트 파일에 따라 에이전트가 티켓을 직접
전환한다.

### How dispatch works in one diagram

```
┌────────────┐    poll      ┌──────────────┐    matches active_states
│  kanban/   │  ─────────▶  │ Orchestrator │  ─────────────────────────┐
│  *.md      │   30s tick   │ (scheduler)  │                            │
└────────────┘              └──────────────┘                            ▼
      ▲                            │                          ┌──────────────────┐
      │                            │ creates workspace        │  Workspace       │
      │ agent writes               ▼                          │  ~/sym…/TASK-1   │
      │ ## Resolution     ┌──────────────────┐                │  + after_create  │
      │ + state: Done     │  AgentBackend    │  ◀────────────│    hook ran      │
      └───────────────────│  (codex/claude/  │                └──────────────────┘
                          │   gemini/open-   │                          │
                          │   code/pi)       │                          │
                          │  per-turn loop   │  before_run hook ──▶ turn(s)
                          └──────────────────┘                          │
                                                                        ▼
                                                                  after_run hook
```

## Per-ticket artefacts

티켓이 만들어내는 모든 산출물은 `docs/<TICKET-ID>/<stage>/` 아래에 산다. 레이아웃,
무엇을 커밋할지, 그리고 `${LLM_WIKI_PATH:-./docs/llm-wiki}/` 예외에 대해서는
[`docs/PIPELINE.md`](docs/PIPELINE.md#per-ticket-artefact-root)를 참고한다.

## Custom prompts

`WORKFLOW.md`는 `docs/` 아래의 편집 가능한 프롬프트 파일을 가리킬 수 있다:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Verify: ./docs/symphony-prompts/file/stages/verify.md
    Learn: ./docs/symphony-prompts/file/stages/learn.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

Symphony는 `base`와 티켓의 현재 상태에 해당하는 프롬프트 파일만 보내, 각 턴을
예전의 전 단계 프롬프트보다 작게 유지한다. `prompts` 블록이 없으면 `WORKFLOW.md`의
인라인 본문이 여전히 레거시 폴백으로 동작한다.

---

## Run

### Web app + JSON API

```bash
symphony ./WORKFLOW.md --port 9999
# 브라우저에서 http://127.0.0.1:9999/ 열기
```

`/`는 내장 웹 칸반 앱을 서빙한다(빌드 단계 없음, 가입 없음, 루프백 전용):

- **Board** — 이슈 생성/수정/삭제, 드래그로 컬럼 이동, 실행 중 배지(턴 수,
  토큰), 워커 Pause / Resume, Learn 스킵. 기본 화면은 네 개의 active agent
  lane만 보여주며, `Human Review`, `Done`, `Blocked`, `Archive`는 `All`로
  펼치기 전까지 **Review and parked** 그룹에 작게 표시된다.
- **Workflow** — 칸반 컬럼 추가/삭제/이름변경/순서변경, 컬럼별 스테이지
  프롬프트 편집. 변경은 주석을 보존한 채 `WORKFLOW.md` frontmatter로
  저장되고, 이름이 바뀌거나 삭제된 컬럼의 티켓은 자동 마이그레이션된다.
- **Stats** — 일별 토큰, 처리량, 컬럼별 체류 시간, 에이전트별 합계, 평균
  사이클 타임 (`.symphony/stats.jsonl` 기반).
- **Settings** — 실제 로컬 브랜치 드롭다운으로 브랜치 정책 설정.

JSON API 엔드포인트:

| Method | Path                              | Purpose                                      |
|--------|-----------------------------------|----------------------------------------------|
| GET    | `/api/v1/health`                  | tick loop / tracker / run registry 상태       |
| GET    | `/api/v1/state`                   | Snapshot — running, retrying, totals, limits |
| GET    | `/api/v1/board`                   | 컬럼 + 이슈 + 실행 중 정보                    |
| GET    | `/api/v1/runs?issue=&limit=`      | registry의 최근 실행 시도                     |
| POST/PATCH/DELETE | `/api/v1/issues[...]`  | 이슈 CRUD (file tracker)                     |
| PUT    | `/api/v1/workflow/states`         | 컬럼 추가 / 삭제 / 이름변경 / 순서변경        |
| GET/PUT| `/api/v1/workflow/prompts/<state>`| 컬럼 스테이지 프롬프트 조회 / 편집            |
| PUT    | `/api/v1/workflow/branch-policy`  | feature base / merge target 브랜치 갱신       |
| GET    | `/api/v1/git/branches`            | 브랜치 정책 UI용 로컬 브랜치 목록             |
| GET    | `/api/v1/stats?days=N`            | 집계된 실행 통계                              |
| POST   | `/api/v1/refresh`                 | poll + reconcile 즉시 트리거                  |
| POST   | `/api/v1/{id}/pause` `/resume`    | 실행 중 워커 보류 / 재개                      |
| POST   | `/api/v1/issues/{id}/skip-learn`  | idle Learn 티켓을 Human Review로 이동         |

### CLI Kanban TUI (primary UI)

```bash
symphony tui ./WORKFLOW.md
# equivalent
symphony ./WORKFLOW.md --tui
```

#### Recommended default: TUI + JSON API together

TUI는 기본 운영자 뷰이고 JSON API는 프로그래밍 / curl 친화적 뷰다. `WORKFLOW.md`에
`server.port`를 고정하고 `--tui`로 실행하면 둘을 한 프로세스에서 함께 돌릴 수 있다
(`tools/board-viewer/`는 아래에서 설명하는 선택적 브라우저 칸반으로 계속 사용
가능):

```yaml
# WORKFLOW.md
server: { port: 8765 }
```

```bash
symphony --tui ./WORKFLOW.md
# kanban renders in the terminal, JSON API listens on 127.0.0.1:8765
curl -s http://127.0.0.1:8765/api/v1/state | jq
```

CLI에서 `--port N`으로 워크플로 값을 재정의하거나, `server` 블록을 빼서 HTTP API를
완전히 비활성화한다.

컬럼은 트래커 상태다(`active_states` 먼저, 그다음 `terminal_states`).
카드는 이슈 식별자 + 제목, 우선순위, 라벨(또는 블로커), 그리고 런타임 표시를
보여준다:

- **● green** — 현재 실행 중, `turn N`, 마지막 이벤트, 누적 토큰을 표시
- **↻ yellow** — 재시도 큐에 있음, `retry #N`과 마지막 오류를 표시
- **✓ green** — 이번 세션에서 완료됨

키 바인딩(푸터에도 자동으로 나열됨):

| Key                | Action                                       |
|--------------------|----------------------------------------------|
| `q`                | Quit (drains active workers cleanly)         |
| `r`                | Force a refresh + re-poll the tracker        |
| `?`                | Show all key bindings as a notification      |
| `tab` / `shift+tab`| Move focus to next / previous card or lane   |
| `j` / `↓`          | Scroll focused lane down one row             |
| `k` / `↑`          | Scroll focused lane up one row               |
| `space` / `pgdn`   | Page down                                    |
| `b` / `pgup`       | Page up                                      |
| `g` / `home`       | Jump to top                                  |
| `G` / `end`        | Jump to bottom                               |
| `enter`            | Open the focused card's full-detail modal    |
| `esc` / `q`        | Close the modal (when one is open)           |

마우스: 카드를 클릭하면 포커스되고, 휠로 해당 레인을 스크롤한다.

#### Managed background service

일상 운영에는 임시 셸 작업보다 내장 service 명령을 권장한다. 시작한 워크플로를
`.symphony/run/<workflow-hash>.json`에 기록하므로, 같은 `WORKFLOW.md`를 실수로 두 번째
포트에서 다시 시작할 수 없다:

```bash
symphony service start ./WORKFLOW.md --port 9999 --viewer-port 8765
symphony service status ./WORKFLOW.md
symphony service restart ./WORKFLOW.md
symphony service stop ./WORKFLOW.md
symphony service logs ./WORKFLOW.md
```

`service start`는 스폰 전에 `symphony doctor`를 실행하고, Python 모듈 러너로
오케스트레이터를 시작하며, 해당 폴더가 있으면 `tools/board-viewer/`도 시작한다.
명령은 셸 없이 실행되므로, 같은 경로가 macOS, Linux, Windows에서 동일하게
동작한다.

v0.4.7부터 보드 뷰어(기본 `--viewer-port 8765`)는 더 이상 읽기 전용이 아니다:
실행 중인 카드에 **Pause / Resume** 버튼이 나타나고 헤더의 refresh 버튼이
오케스트레이터 `poll + reconcile`을 트리거한다. 헤더는 또한
`agent.feature_base_branch`와 `agent.auto_merge_target_branch`를 위한 실제 로컬 git
브랜치 드롭다운을 보여주므로, 운영자가 YAML을 손으로 편집하지 않고도 새 기능
브랜치가 어디서 시작하고 Learn 머지가 어디로 떨어질지 고를 수 있다.

#### One-shot launchers

전체 `symphony tui` 호출을 외우고 싶지 않은 개발자를 위해, 레포는 `.venv/bin/symphony`를
`PATH`보다 우선하고, 먼저 `symphony doctor`를 실행한 다음, 새 터미널 창에서 TUI를
여는 두 개의 런처 스크립트를 제공한다:

```bash
./tui-open.sh                     # macOS / Linux — uses iTerm or Terminal.app
./tui-open.sh path/to/WORKFLOW.md # explicit workflow path
tui-open.bat                      # Windows — uses cmd /k
```

두 스크립트 모두 `doctor`가 FAIL을 보고하면 실행을 중단하므로, 읽을 수 없는 사전
점검 출력 위에 alt-screen을 그리지 않는다.

### File-based Kanban tracker

Linear가 없다면 로컬 Markdown 파일 트래커를 쓴다(업스트림에서 변경 없음):

```yaml
tracker:
  kind: file
  board_root: ./kanban
```

```bash
symphony board init ./kanban
symphony board new DEV-1 "Title" --priority 2
symphony tui ./WORKFLOW.md
```

## Layout

```
src/symphony/
  backends/
    __init__.py        AgentBackend Protocol + factory + normalized events
    codex.py           Codex JSON-RPC stdio backend (was upstream agent.py)
    claude_code.py     Claude Code stream-json backend
    gemini.py          Gemini one-shot backend
    opencode.py        OpenCode run/json backend (per-turn subprocess, --session resume)
    pi.py              Pi --mode json backend (per-turn subprocess, --session resume)
  trackers/
    __init__.py        TrackerClient Protocol + factory
    _retry.py          retry/backoff wrapper for network trackers
    file.py            FileBoardTracker (locked Markdown ticket mutations)
    jira.py            Jira REST tracker
    linear.py          LinearClient (Linear GraphQL)
  workflow/
    parser.py          WORKFLOW.md frontmatter/body parser
    config.py          frozen config dataclasses
    builder.py         ServiceConfig construction + validation
    mutate.py          comment-preserving workflow edits for the web UI
    preflight.py       dispatch-time validation
  orchestrator/
    core.py            scheduler/state machine
    run_registry.py    SQLite WAL run leases + issue flags
    contracts.py       stage-contract validation helpers
  cli/
    __init__.py        re-exports `main` for the `symphony` console_script
    __main__.py        keeps `python -m symphony.cli ...` working for service.py
    main.py            root dispatch + `symphony [WORKFLOW]`
    board.py           `symphony board ...` file-tracker helper
    doctor.py          `symphony doctor` WORKFLOW.md preflight checks
  utils/
    archive.py         auto-archive selector
    auto_merge.py      symphony/<ID> branch → host repo merge
    keep_awake.py      macOS caffeinate wrapper (no-op on other platforms)
    wiki_sweep.py      Learn-prompt wiki integrity sweep
  agent.py             back-compat shim re-exporting backends.* symbols
  server.py            aiohttp server, health/state/refresh routes
  webapi.py            web app REST routes + static SPA serving
  stats.py             .symphony/stats.jsonl aggregation
  skills.py            SKILL.md discovery + prompt injection
  tui/                 Textual Kanban TUI package
  service.py           `symphony service` background lifecycle
  mock_codex.py        runnable via `python -m symphony.mock_codex` for demos/tests
  web/static/          built-in browser app assets
tui-open.sh            cross-platform launcher (macOS / Linux): doctor preflight + open TUI in a new terminal window
tui-open.bat           Windows equivalent
```

## Tests

```bash
pytest -q
```

테스트 스위트는 업스트림 적합성 스위트, 팩토리에 대한 백엔드 단위 테스트, 이벤트
정규화, Claude / Pi 사용량 누적, Gemini 세션 합성, OpenCode 명령/세션 파싱,
Pi 실패 사유 탐지, run registry 영속성, file tracker locking, 웹 API contract,
그리고 TUI 앱에 대한 Textual `Pilot` 구동 스모크 테스트를 포함한다. 실제 CLI를
상대로 한 서브프로세스 구동 통합 테스트는 의도적으로 CI에 포함하지 않았다 —
로컬에서 실행한다.

## Design notes

### Why five different lifecycles behind one Protocol?

- **Codex**는 이슈당 하나의 `app-server` 서브프로세스를 열고 현재의
  `codex app-server` JSON-RPC 프로토콜(`initialize` + `thread/start`
  + `turn/start` + 스트리밍되는 `turn/completed` 및 `item/completed`
  알림)을 말한다. 한 프로세스 안에서 멀티턴이다. 오래된 `v2/initialize` 방식의
  릴리스는 지원하지 않는다 — `codex-cli ≥ 0.39`(현재 업스트림)로 고정한다.
- **Claude Code**는 영속 서버가 없고, 세션은 ID로 추적된다. 각
  `run_turn`은 새 `claude -p`를 스폰하고 턴 2부터 `--resume <session-id>`를
  사용한다.
- **Gemini CLI**는 호출당 1회로, 네이티브 세션 모델이 없다.
  각 턴은 독립적이며, 오케스트레이터의 기록이 일관되게 유지되도록
  `gemini-<uuid>` 세션 id를 합성한다.
- **OpenCode**는 문서화된 자동화 경로인
  `opencode run --format json --auto [message..]`로 실행한다. Symphony는
  프롬프트를 `message` 인자로 전달하고, JSON 이벤트가 있으면 읽으며, OpenCode가
  실제 세션 id를 보고한 뒤부터 continuation 턴에 `--session <id>`를 붙인다.
- **Pi**는 영속 서버가 없지만 세션을 `~/.pi/agent/sessions/`에 자동 저장한다.
  각 `run_turn`은 새 `pi --mode json`을 스폰하고 턴 2부터 `--session <id>`를
  넘긴다. 세션 id는 첫 `{"type":"session"}` JSONL 줄에서 읽고, 메시지별 `usage`는
  `message_end` 이벤트에서 누적되며, `agent_end`를 종료 이벤트로 취급한다.
  인증은 Pi에 위임된다: `/login`으로 채워진 `~/.pi/agent/auth.json`의
  OAuth/API 키 저장소를 서브프로세스가 상속하므로, Symphony 자체는 자격 증명을
  절대 다루지 않는다.

`AgentBackend` Protocol이 이런 차이를 감춘다. 오케스트레이터는 정규화된
이벤트(`session_started`, `turn_completed`, `turn_failed` 등)와 최신 사용량 /
레이트 리밋 스냅샷만 본다.

### What the TUI and web app do and do not do

웹 앱은 파일 보드를 위한 전체 브라우저 편집기다. 같은 tracker / workflow 모듈을
통해 이슈 생성/수정/삭제, 드래그 상태 이동, 컬럼/프롬프트 편집, 브랜치 정책
갱신을 수행한다. TUI는 키보드 운영에 최적화되어 있으며 터미널을 벗어나지 않고
티켓 생성/수정, archive, Done gate confirm, Pause / Resume, Learn skip, filter,
detail 확인을 할 수 있다.

대화형으로 *할 수 있는* 것:

- `tab` / `shift+tab` 또는 클릭으로 어떤 카드든 포커스한다.
- 마우스 휠, `j` / `k`, 또는 페이지 키로 레인을 스크롤한다.
- `enter`로 포커스된 카드의 전체 설명을 모달로 연다.
- `n`, `e`, `a`, `c`, `P`, `S`, `/`로 주요 TUI 쓰기 동작을 실행한다.

의도적으로 범위 밖인 것:

- **터미널 TUI 안에서는 드래그앤드롭 없음.** 포인터 기반 상태 이동이 필요하면
  웹 보드, `symphony board mv ID State`, 또는 트래커 UI를 쓴다.
- **전체 에이전트 출력 로그 창 없음.** 에이전트 stdout/stderr는 구조화된 로그로
  가며, 옆 터미널에서 `tail -F log/symphony.log`로 따라간다.
- **웹 보드에서 Linear/Jira 직접 수정 없음.** 브라우저 이슈 CRUD는 file tracker
  전용이고, Linear/Jira 보드는 read-only live status로 내려간다.

## What is *not* implemented

업스트림에서 상속:

- SSH 워커 확장 — 단일 호스트 전용.
- Linear, Jira, 파일 기반 칸반 외의 트래커 어댑터.

포크 고유의 한계:

- Run lease와 이슈 safety flag는 SQLite에 저장되지만, hard crash 뒤 실행 중이던
  in-process worker에 다시 붙지는 않는다. Markdown 티켓 상태가 recovery checkpoint다.
- Retry attempt는 보존되지만, 과거 attempt를 운영자가 훑어볼 first-class run
  history CLI/API는 아직 없다.
- Claude Code의 턴 중간 스트리밍 사용량 이벤트는 읽지만 노출하지 않는다 —
  토큰 합계의 진실의 원천은 종료 `result` 이벤트다.
- OpenCode 토큰 사용량은 JSON 이벤트에서 best-effort로 파싱한다. 알 수 없는
  이벤트 형태는 완료된 턴을 실패시키지 않고 합계를 0으로 둔다.
- Gemini 토큰 사용량은 CLI가 안정적인 형태로 보고하지 않으므로, 그 백엔드의
  합계는 0에 머문다.
- Gemini의 멀티턴 연속성은 지원하지 않는다(CLI에 세션 프로토콜이 없다). 각
  `run_turn`은 독립적이다.

## Contributing

PR을 환영한다. 외부 기여는 기본적으로 `dev`를 대상으로 한다 — 전체 리뷰
체크리스트는 [CONTRIBUTING.md](CONTRIBUTING.md)와 PR 템플릿을 참고한다. PR을 열기
전에:

```bash
pip install -e ".[dev]"
pytest -q          # must stay green
```

백엔드 어댑터는 `src/symphony/backends/` 아래에 산다. 새 에이전트(예: Ollama 구동
로컬 모델)를 추가하려면:

1. 새 모듈에서 `AgentBackend` Protocol을 구현하고,
2. `build_backend()`(`src/symphony/backends/__init__.py`)에 등록하고,
3. `workflow.py`에 `<kind>Config` 데이터클래스를 추가해 `build_service_config` +
   `validate_for_dispatch`로 엮고,
4. `SUPPORTED_AGENT_KINDS`를 확장한다.

백엔드를 업스트림에 올리는 기준은 다음과 같다: 기존 팩토리 + 이벤트 정규화
테스트를 통과하고, 프로토콜 고유 타입을 오케스트레이터로 새어 나가게 하지 않으며,
`WORKFLOW.example.md`에 기본 `<kind>` 블록을 함께 제공한다.

## Acknowledgements

이 프로젝트는 OpenAI의
[Symphony](https://github.com/openai/symphony) 레퍼런스 구현 위에 세워졌다.
업스트림의 Apache-2.0 라이선스 작업이 이 포크를 가능하게 하는 오케스트레이터,
스케줄러, 워크스페이스 수명 주기를 제공한다. 출처 표기 세부 사항은 `NOTICE`를
참고한다.

TUI는 Will McGugan의 [Textual](https://textual.textualize.io) 프레임워크 위에
세워졌으며, 카드 안 텍스트 스타일링에는 [rich](https://github.com/Textualize/rich)를
직접 사용한다.

파이프라인 단계 규칙은 [cskwork/backend-dev-skills](https://github.com/cskwork/backend-dev-skills)(MIT)의
증거 우선(evidence-first) 아이디어를 차용했다.

## License

[Apache 2.0](LICENSE).
