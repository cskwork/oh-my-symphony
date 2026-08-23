# oh-my-symphony

**[English](README.md) | 한국어**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB.svg)](https://www.python.org/)
[![Tests](https://github.com/cskwork/oh-my-symphony/actions/workflows/tests.yml/badge.svg)](https://github.com/cskwork/oh-my-symphony/actions/workflows/tests.yml)
[![GitHub stars](https://img.shields.io/github/stars/cskwork/oh-my-symphony?style=social)](https://github.com/cskwork/oh-my-symphony/stargazers)

> 하나의 컨트롤 플레인, 하나의 터미널, 여러 프로젝트 보드, 여덟 개의 AI 코딩 에이전트
> (**Codex**, **Claude Code**, **Gemini**, **AGY/Antigravity**, **Kiro**,
> **OpenCode**, **Pi**, **Prime Agent**) — 티켓마다 골라 쓰고, 병렬로 실행하며,
> Git 변경을 검토하고 빌드를 미리 보면서 실시간으로 지켜본다.

![Symphony 9999 admin UI screenshot](docs/admin-ui-screenshot.png)

<sub>`symphony service start ./WORKFLOW.md --port 9999` — `http://127.0.0.1:9999/`에서 열리는 내장 관리자 UI. 프로젝트 전환, 이슈·워크플로 관리, 실시간 실행·통계·Git 변경 확인, 병합·푸시·PR 생성, 운영자 채팅, 상태 확인이 포함된 제품 미리보기를 한곳에서 다룬다. 스크린샷은 정리된 데모 데이터다.</sub>

![symphony tui screenshot](docs/tui-screenshot.svg)

<sub>`symphony tui ./WORKFLOW.md` — 컬럼은 트래커의 상태이고, 카드는 현재 에이전트, 턴 수, 마지막 이벤트, 누적 토큰을 보여준다. 실시간 표시: ● 실행 중, ↻ 재시도 대기, ✓ 완료.</sub>

**AI 코딩 CLI를 더 이상 저글링하지 말자.** Symphony는 각 칸반 티켓을
원하는 에이전트에 넘기고, 격리된 `git worktree` 워크스페이스에서 동시에 실행하며,
실시간 진행 상황 — 턴 수, 토큰 사용량, 선택한 CLI가 제공할 때의 레이트 리밋
여유 — 을 9999 브라우저 관리자 UI 또는 터미널을 벗어날 필요 없는 Jira 스타일 TUI로 보여준다.

[**AI CLI 없이 60초 만에 체험하기 →**](#try-it-in-60-seconds-no-agent-cli-required)

## 목차

- [Symphony를 쓰는 이유](#why-symphony)
- [작동 방식](#how-it-works)
- [에이전트 선택](#pick-an-agent)
- [설치](#install)
- [60초 체험](#try-it-in-60-seconds-no-agent-cli-required)
- [첫 작업 Quickstart](#quickstart--your-first-task-end-to-end)
- [레인 프리셋](#lane-presets)
- [채팅 인테이크](#chat-intake--채팅에-요청하면-보드가-배달한다)
- [지속적 개선](#continuous-improvement--실험적-자율-유지보수)
- [실행](#run)
- [구조](#layout)
- [테스트](#tests)
- [설계 메모](#design-notes)
- [아직 구현하지 않은 것](#what-is-not-implemented)

## Why Symphony?

- **벤더 종속 없음.** Codex ↔ Claude Code ↔ Gemini ↔ AGY ↔ Kiro ↔ OpenCode ↔ Pi ↔ Prime Agent를 YAML 한 줄로
  바꾸거나, 티켓마다 백엔드를 섞어 쓴다. 새 에이전트(Ollama, 로컬 모델,
  CLI가 있는 무엇이든)는 오케스트레이터를 바꾸지 않고 얇은
  `AgentBackend` Protocol 뒤에 끼워 넣으면 된다.
- **에이전트가 실제로 무엇을 하는지 본다.** 실시간 칸반은 실행 중 카드의
  턴 수, 마지막 이벤트, 누적 토큰, 그리고 제공되는 경우 공급자가 보고한
  레이트 리밋 여유를 보여준다. "멈춘 건가, 생각 중인 건가?" 더 이상 헷갈릴
  일이 없고 — 로그인할 SaaS 대시보드도 없다.
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
  포크했다. 이 포크는 파일 우선 오케스트레이션 모델을 유지하면서 여덟 개의
  백엔드, TUI / 웹 운영 화면, SQLite 실행 lease, 재시작에도 보존되는 이슈
  플래그, 잠금 기반 Markdown 티켓 쓰기를 더한다.
- **뷰어가 아니라 진짜 웹 앱.** 오케스트레이터 포트가 멀티 프로젝트 컨트롤
  플레인을 직접 서빙한다: 이슈 CRUD, 드래그 앤 드롭 컬럼, 컬럼별 스테이지
  프롬프트, 브랜치 정책, Pause / Resume, 레인 프리셋, 운영자 채팅, 통계,
  Git 검토·배포, 상태 확인이 포함된 제품 미리보기까지 제공한다. 워크플로 편집은
  주석을 보존한 채 `WORKFLOW.md`로 왕복 저장된다.
- **운영자급 도구가 기본 제공.** `symphony doctor`는 첫 실행에서 가장 흔한
  다섯 가지 실패(포트 충돌, CLI 누락, 자리표시자 URL, 쓰기 불가 워크스페이스)를
  한 번에 잡아낸다. `symphony service start/stop/restart/logs`는 오케스트레이터를
  관리형 백그라운드 서비스로 실행한다.

## Who is this for?

- 자는 동안 수십 개 티켓에 걸쳐 무인 야간 리팩터링을 돌리는 **1인 개발자**.
- 버그 수정, 문서 갱신, 마이그레이션 티켓을 여러 코딩 에이전트에 걸쳐 동시에
  병렬화하는 **팀**.
- 동일한 프롬프트와 워크스페이스로 Codex, Claude Code, Gemini,
  AGY/Antigravity, Kiro, OpenCode, Pi, Prime Agent가 같은 작업을 어떻게 처리하는지 나란히
  비교하는 **연구자와 리뷰어**.
- "에이전트당 채팅 창 하나"의 한계에 부딪혀, 한눈에 읽히는 칸반을 갖춘 진짜
  오케스트레이터를 원하는 **누구든**.

## How it works

<details>
<summary>Plain-text version of the TUI (for terminals viewing raw README)</summary>

```text
  agent=codex  tracker=linear  workflow=WORKFLOW.md  lang=en   running=2  retrying=1   │  tokens in=84,200 out=27,640 total=111,840
                                                                                       │  rate-limits=requests_remaining=4823, tokens_remaining=1.2M

╭── Todo [1/4] (3) ╮ ╭── In Progress [2/4] ╮ ╭── Verify [3/4] ╮ ╭── Document [4/4] ╮ ╭── Done (2) ──╮ ╭── detail ───────────────────────╮
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

q quit · r refresh · enter details · n new · e edit · s stats · S skip Document · P pause/resume · / filter · ?
```

</details>

[OpenAI Symphony 레퍼런스 구현](https://github.com/openai/symphony)의 멀티 에이전트 포크.
업스트림은 트래커(Linear 또는 로컬 Markdown 칸반)를 폴링해 이슈별 워크스페이스
안에서 Codex 세션을 실행한다. 이 포크는 그 오케스트레이터를 유지하면서 다음을
더한다:

1. 여덟 개의 구체 어댑터를 가진 플러그형 **AgentBackend** 레이어:
   - **Codex** — `codex app-server` (JSON-RPC stdio, 멀티턴) — 원본
   - **Claude Code** — `claude -p --output-format stream-json --verbose`
     (NDJSON 이벤트, `--resume`를 쓰는 턴별 서브프로세스)
   - **Gemini** — `gemini -p ""` (턴당 1회 호출, stdin 프롬프트 → stdout 결과)
   - **AGY / Antigravity** — `agy --print "$(cat)"` (턴당 1회 호출, stdin 프롬프트
     -> stdout 결과; `agent.kind: antigravity`는 `agy`로 처리)
   - **Kiro** — `kiro-cli chat --no-interactive --trust-all-tools ...`
     (headless chat 모드; 프롬프트를 chat 입력 인자로 전달,
     `KIRO_API_KEY` 또는 `kiro-cli login` 사용)
   - **OpenCode** — `opencode run --format json --auto` (턴당 1회 호출,
     문서화된 `message` 인자로 프롬프트 전달, 세션 ID 확인 후 `--session` 재개)
   - **Pi** — `pi --mode json -p ""` (JSONL 이벤트, `--session` 재개를 쓰는
     턴별 서브프로세스; 하나의 CLI 아래에서 Anthropic / OpenAI / Gemini / Bedrock
     백엔드를 지원 — [pi.dev](https://pi.dev) 참고)
   - **Prime Agent** — `prime-agent -p --mode json` (Pi와 같은 JSONL 이벤트,
     `--resume` 재개를 쓰는 턴별 서브프로세스; `/login` 또는 provider API 키 사용,
     자격 증명은 `~/.prime/agent/auth.json`에 저장)
2. [Textual](https://textual.textualize.io) 기반 **Jira 스타일 CLI 칸반 TUI**.
   컬럼은 트래커 상태이고, 카드는 현재 에이전트, 턴 수, 마지막 이벤트, 누적
   토큰을 보여준다. 카드는 포커스할 수 있고, 마우스 휠로 각 레인을 스크롤하며,
   카드에서 `enter`를 누르면 전체 상세 모달이, `n`으로 멀티라인 새 티켓 등록,
   `e`로 포커스 티켓 편집, `S`로 Document 스킵, `s`로 통계 화면이 열린다.
3. 오케스트레이터 포트에 내장된 **웹 칸반 앱** — 이슈 CRUD, Document 스킵,
   드래그 앤 드롭 상태 이동, 컬럼 추가/삭제/이름변경, 컬럼별 프롬프트
   편집, 브랜치 정책, 전용 통계 페이지.
4. `.symphony/state.db`의 **단일 노드 신뢰성 ledger** — 활성 실행 lease가
   재시작 뒤 중복 디스패치를 막고, 죽은 소유자의 프로세스를 펜싱·종료한 뒤
   다음 Run attempt가 마지막 완료 턴 체크포인트에서 이어갈 수 있다. retry /
   pause / budget-exhausted 플래그도 프로세스 종료 뒤에 보존된다.

아키텍처는 의도적으로 로컬 / 파일 우선이다. Markdown 티켓은 사람이 읽고 고치는
진실의 원천이고, SQLite는 손으로 편집하지 않아야 하는 런타임 조정 상태를 저장한다.

## Pick an agent

`WORKFLOW.md`에서 `agent.kind`를 설정한다:

```yaml
agent:
  kind: claude          # codex | claude | gemini | agy | kiro | opencode | pi | prime-agent

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

pi:
  command: pi --mode json -p ""
  resume_across_turns: true
  turn_timeout_ms: 3600000

prime_agent:
  command: prime-agent -p --mode json
  resume_across_turns: true
  turn_timeout_ms: 3600000
```

각 백엔드는 자기 블록(`codex`, `claude`, `gemini`, `agy`, `kiro`,
`opencode`, `pi`, `prime_agent`)을 읽으며, 런타임에는
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
`codex:`, `claude:`, `gemini:`, `agy:`, `kiro:`, `opencode:`, `pi:`,
`prime_agent:` 블록에서 가져온다.
CLI에서 파일 보드 티켓을 만들 때는
`symphony board new TASK-2 "title" --agent-kind codex`를 쓴다.

티켓 고정값과 전역 기본값 사이에는 선택적 상태별 라우팅이 있다:
`agent.stage_kinds`는 보드 상태를 에이전트 kind에 매핑해서, 가벼운 레인은
저렴하고 빠른 에이전트가(예: `Todo: gemini`, `Document: gemini`), Plan/Build/Review는
강한 기본 에이전트가 맡게 한다. 디스패치별 우선순위: 티켓 `agent_kind` 고정 >
`agent.stage_kinds[state]` > `agent.kind`. 백엔드는 상태가 바뀔 때마다 다시
결정된다 — 한 번의 디스패치 안에서 일어나는 레인 전환도 포함이라, In Progress →
Verify → Document를 한 디스패치로 걷는 티켓도 레인마다 설정된 백엔드를 받는다.

파일 보드 워크플로에서 `agent.auto_triage_actionable_todo`는 기본값이
`true`다: 본문과 `Acceptance Criteria` 섹션이 있는 Todo 티켓은 모델 턴을 쓰지 않고
한 줄짜리 `## Triage` 노트와 함께 In Progress로 이동한다. 버그 티켓, 블록된 티켓,
모호한 티켓, 그리고 Linear 트래커는 여전히 Todo 프롬프트를 사용한다.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

해당 CLI를 `$PATH`에서 사용할 수 있게 한다:

| `agent.kind` | required CLI on `$PATH` |
|--------------|------------------------|
| `codex`      | `codex` (with `app-server` subcommand) |
| `claude`     | `claude` (Claude Code) |
| `gemini`     | `gemini` (Gemini CLI)  |
| `agy`        | `agy` (Antigravity CLI — Google Antigravity에서 설치; Symphony가 `--dangerously-skip-permissions`를 붙임) |
| `kiro`       | `kiro-cli` (Kiro CLI — `https://cli.kiro.dev/install`에서 설치; headless 실행에는 `kiro-cli login` 또는 `KIRO_API_KEY` 필요) |
| `opencode`   | `opencode` (OpenCode CLI — `npm install -g opencode-ai`로 설치, `opencode auth login`으로 provider 인증) |
| `pi`         | `pi` (Pi coding-agent — `npm i -g @earendil-works/pi-coding-agent` or `curl -fsSL https://pi.dev/install.sh \| sh`; sign in once via `pi` → `/login` (OAuth, credentials cached at `~/.pi/agent/auth.json`) — no env var needed) |
| `prime-agent` | `prime-agent` (Prime Agent — install from the Prime Agent installer; sign in via `prime-agent` → `/login`, or provide a provider API key; credentials cached at `~/.prime/agent/auth.json`) |

## Try it in 60 seconds (no agent CLI required)

실제 에이전트 CLI를 설치하기 전에 TUI가 카드를 옮기는 모습을 먼저 보고
싶은가? 번들로 제공되는 **목(mock) 백엔드**를 쓰면 된다 — Codex와 동일한 JSON-RPC
프로토콜을 말하지만 실제 작업은 하지 않고, 턴을 시뮬레이션하며 토큰 사용량 틱을
내보낼 뿐이다.

```bash
git clone https://github.com/cskwork/oh-my-symphony.git
cd oh-my-symphony
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"

# 목 백엔드를 가리키는 WORKFLOW.md
cat > WORKFLOW.md <<'YAML'
---
tracker: { kind: file, board_root: ./kanban,
           active_states: [Todo, "In Progress", Verify, Document],
           terminal_states: ["Human Review", Done, Blocked, Archive] }
polling: { interval_ms: 5000 }
workspace: { root: ~/symphony_workspaces }
hooks:
  after_create: ": noop"
  before_run:   ": noop"
  after_run:    "echo done"
agent:  { kind: codex, max_concurrent_agents: 1, max_turns: 4, max_total_turns: 60 }
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
  active_states: [Todo, "In Progress", Verify, Document]
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

# 구조화 생성: 의존성, request 그룹, 본문을 파일/표준입력으로.
symphony board new TASK-2 "Add regression test" \
  --blocked-by TASK-1 \
  --request REQ-1 \
  --label test --label ci \
  --description-file ./spec.md      # 또는 `-`로 stdin에서 읽기
```

`new`는 쓰기 전에 검증한다: 고유 id, `tracker.active_states`/`terminal_states`에
있는 상태, 모든 `--blocked-by` 대상이 보드에 존재할 것, 그리고 추가된 간선이
의존성 그래프를 비순환(acyclic)으로 유지할 것(위반 시 사이클 경로를 출력하고
0이 아닌 코드로 종료). 웹 API의 이슈 생성/수정 엔드포인트도 같은 규칙을
적용한다.

확인:

```bash
symphony board ls                    # all tickets
symphony board ls --state Todo       # filter by state
symphony board show TASK-1           # full body
symphony board graph                 # 의존성 DAG (토폴로지 순, 들여쓰기)
symphony board graph --request REQ-1 # 한 request 그룹만
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

### Board deliverables

위의 `docs/` 산출물은 커밋되는 증거다. 리뷰어가 그냥 *열어보기만* 하면 되는
파일 — 스크린샷, 리포트, PDF — 은 다른 곳에 둔다. 에이전트가 워크스페이스
루트의 `.symphony-artifacts/`에 저장하면, Symphony가 매 턴이 끝날 때 새 파일을
호스트의 `.symphony/artifacts/<TICKET-ID>/`로 복사한다. 그러면 웹 보드의 티켓
서랍에 나타나고(이미지는 인라인 미리보기), 티켓 본문에도 `## Artifacts` 목록이
생기며, Done에서 워크스페이스가 지워져도 남는다. 이 디렉터리는 Git에서 제외되므로
산출물이 머지에 섞이지 않는다.

`.symphony-artifacts/manifest.json`은 선택 사항이며 제목을 붙인다:

```json
{ "artifacts": [{ "file": "login.png", "title": "로그인 화면", "summary": "수정 후" }] }
```

`WORKFLOW.md`에서 모두 선택 사항인 기본값:

```yaml
artifacts:
  enabled: true                 # false면 수집을 완전히 끈다
  dir: .symphony-artifacts      # 에이전트가 쓰는 워크스페이스 디렉터리
  max_file_mb: 25
  max_ticket_mb: 200
  ttl_days: 30                  # 티켓이 아카이브된 뒤 30일이 지나면 정리, 0이면 끔
  require_for_done: false       # true면 산출물이 하나도 없을 때 Done을 막는다
```

## Custom prompts

`WORKFLOW.md`는 Quickstart에 나온 `prompts.base` + `prompts.stages` 맵으로
`docs/` 아래의 편집 가능한 프롬프트 파일을 가리킨다. Symphony는 `base`와
티켓의 현재 상태에 해당하는 프롬프트 파일만 보내, 각 턴을 작게 유지한다.
`prompts` 블록이 없으면 `WORKFLOW.md`의 인라인 본문이 여전히 레거시 폴백으로
동작한다. 프롬프트는 웹 앱의 **Workflow** 페이지에서도 그 자리에서 편집할 수
있다 — 같은 파일이며, 재시작이 필요 없다.

## Lane presets

보드는 프리셋에서 시작하고, 이후에도 완전히 커스터마이즈할 수 있다:

- **default** — 간결한 4레인 보드 `Todo → In Progress → Verify → Document`.
  짧은 스테이지 프롬프트를 쓰고, `orchestrator/contracts.py`의 스테이지
  계약이 기계적 게이트다. 복잡한 작업은 레인을 늘리는 대신 티켓 DAG
  (`--blocked-by` / `--request`)로 표현한다.
- **deep** — 복잡한 딜리버리를 위한 선택적 8레인 파이프라인
  `Intake → Research → Plan → Review → Build → QA → Verify → Document`.
  레인마다 자체 경량 게이트를 갖고(Verify/Document는 리터럴
  `grep 'verdict: GREEN'` 검사를 실행), Plan 레인이 `symphony board new
  --blocked-by --request`로 Build/QA/Verify/Document 티켓 DAG를 만든다.

프리셋 전환은 웹 앱의 **Settings** 페이지에서 하거나
`GET /api/v1/workflow/presets` + `POST /api/v1/workflow/presets/apply`로
한다. 프리셋 적용은 레인 CRUD와 같은 주석 보존 `WORKFLOW.md` 왕복 저장을
거치므로 사용자의 주석과 커스터마이징이 살아남고, 제거된 레인의 티켓은
폴백 상태로 마이그레이션된다. 프리셋은 시작점이지 감옥이 아니다 — 이후에도
레인 추가/삭제/이름변경과 컬럼별 프롬프트 편집은 그대로 동작한다.

## Chat intake — 채팅에 요청하면, 보드가 배달한다

어드민 UI에는 같은 에이전트 CLI가 뒷받침하는 **Chat** 페이지가 있다. 새
세션마다 Claude Code, Codex, Gemini CLI, AGY, Kiro, OpenCode, Pi, Prime Agent 중
하나를 선택할 수 있으며, 기본값은 워크플로에 설정된 에이전트다. 채팅은 단순
Q&A가 아니다: edit 모드에서 채팅 에이전트는 보드 인테이크 프로토콜을
따른다. 요청을 입력하면 에이전트가 (요청이 모호할 때만, 최대 두 턴으로)
범위를 확인한 뒤, 검증된 보드 도구를 통해 티켓을 등록한다 — 자유 형식
티켓 markdown은 쓰지 않는다:

- **단순 요청** → 첫 active 상태에 티켓 한 장;
- **복잡한 요청** → research → plan → plan-review → build → qa → document
  스테이지 티켓 DAG를 `--blocked-by`로 연결해 하나의 `--request REQ-<n>`
  그룹 아래 등록;
- **deep 프리셋 보드** (`Intake` 레인이 있으면) → Intake 티켓 한 장;
  분해는 파이프라인이 알아서 한다.

모든 티켓은 `symphony board new` 검증(고유 id, 유효한 상태, 존재하는
blocker, 비순환 DAG)을 통과한다. Q&A 모드에서는 에이전트가 등록할 티켓을
설명만 하고, 세션을 edit 모드로 바꿀 때까지 등록을 미룬다. 채팅은 대화하고,
보드가 배달한다.

---
## Continuous improvement — 실험적 자율 유지보수

**실험 기능이며 전부 opt-in이다.** `WORKFLOW.md`에 `continuous_improvement:`
블록이 없으면 아무것도 실행되지 않는다.

하트비트는 제품 코드를 건드리지 않고 주기적으로 저장소를 점검한 뒤, 발견한
것을 **일반 보드 티켓**으로 등록하는 스케줄러다. 등록된 티켓은 다른 요청과
똑같이 평소 파이프라인을 탄다. 각 기능은 독립된 모드다:

```yaml
continuous_improvement:
  enabled: true
  interval_ms: 1800000            # 하트비트 주기
  modes: [readiness, blocked_fixes, security, market_research,
          feature_improvements]
  mode_interval_hours:            # 모드별 최소 간격 (선택)
    market_research: 168          # 주 1회
  max_improvement_tickets_per_run: 3
```

| 모드 | 하는 일 |
| --- | --- |
| `readiness` | 검증된 baseline에서 테스트/린트/타입체크를 돌리고 실패를 버그 티켓으로 만든다. 기존 동작. |
| `blocked_fixes` | `Blocked` / `Human Review` 티켓을 분류해 근본 원인 메모가 담긴 수정 티켓을 만들고, 원본 티켓에 `blocked_by`로 연결한다. |
| `security` | 선택적 의존성/취약점 스캔(`pip-audit`, `npm audit`)을 패치 티켓으로 만든다. 스캐너가 없으면 실패가 아니라 not available이다. |
| `market_research` | 에이전트 턴 한 번으로 **이 앱**에 맞는 최신 트렌드·경쟁 제품 기능을 조사해(README/docs/wiki 기반) 근거 링크와 함께 개선안을 제안한다. |
| `feature_improvements` | 에이전트 턴 한 번으로 UX와 코드 건강도를 검토해 개선안을 제안한다. |

`modes:` 없이 `enabled: true`만 두면 readiness만 실행된다 — 모드 도입 전과
동일하다. 제안 티켓은 실행당 개수 상한이 있고, 열린 티켓과 중복 제거되며,
`ci` 라벨이 붙고, 하나의 `REQ-CI-<날짜>-<n>` 요청 그룹으로 묶인다. 에이전트
모드는 간결한 프롬프트(`docs/symphony-prompts/ci/`에서 교체 가능)를 받고,
JSON 제안 파일 외에는 아무것도 쓰지 않는다 — 티켓 등록은 하트비트가 한다.
모드와 주기는 웹 **Settings** 페이지에서도 편집할 수 있다.

---
## Run

### Web app + JSON API

```bash
symphony ./WORKFLOW.md --port 9999
# 브라우저에서 http://127.0.0.1:9999/ 열기
```

`/`는 내장 웹 칸반 앱을 서빙한다(빌드 단계 없음, 가입 없음, 루프백 전용):

- **Projects** — 독립된 프로젝트 보드 사이를 전환하고, 각 저장소·워크플로·
  이슈 저장소 경로를 확인하며, 프로젝트를 만들거나 연다.
- **Board** — 이슈 생성/수정/삭제, 드래그로 컬럼 이동, 실행 중 배지(턴 수,
  토큰), 워커 Pause / Resume, Document 스킵. 기본 화면은 네 개의 active agent
  lane만 보여주며, `Human Review`, `Done`, `Blocked`, `Archive`는 `All`로
  펼치기 전까지 **Review and parked** 그룹에 작게 표시된다. **레인**에서
  **요청** 보기로 전환하면 스케줄러가 실제로 사용한 판단을 기반으로 의존성
  실행 순서, 대기열 순위, 웨이브, 용량 대기, 재시도 소유권, 최종 거부 사유를
  읽기 전용으로 확인할 수 있다. 전체 그래프는 파일 보드에서만 지원된다.
- **Workflow** — 칸반 컬럼 추가/삭제/이름변경/순서변경, 컬럼별 스테이지
  프롬프트 편집. 변경은 주석을 보존한 채 `WORKFLOW.md` frontmatter로
  저장되고, 이름이 바뀌거나 삭제된 컬럼의 티켓은 자동 마이그레이션된다.
- **Git** — 히스토리, 작업 브랜치, 비교와 diff를 확인하고, 브랜치를 삭제하며,
  검증된 작업을 병합·푸시하거나 PR을 연다.
- **Chat** — 보드 인테이크 프로토콜을 따르는 운영자 채팅 세션
  ([Chat intake](#chat-intake--채팅에-요청하면-보드가-배달한다) 참고).
- **Preview** — 분리된 대상 브랜치 체크아웃에서 루프백 전용 제품 미리보기를
  시작·재시작·중지하고, 상태 확인·URL·제한된 로그를 본다.
- **Stats** — 일별 토큰, 처리량, 컬럼별 체류 시간, 에이전트별 합계, 평균
  사이클 타임 (`.symphony/stats.jsonl` 기반).
- **Settings** — 실제 로컬 브랜치 드롭다운으로 브랜치 정책을 설정하고,
  레인 프리셋과 지속적 개선 설정을 관리한다.

JSON API 엔드포인트:

| Method | Path                              | Purpose                                      |
|--------|-----------------------------------|----------------------------------------------|
| GET    | `/api/v1/health`                  | tick loop / tracker / run registry 상태       |
| GET    | `/api/v1/state`                   | Snapshot — running, retrying, totals, limits |
| GET    | `/api/v1/board`                   | 컬럼 + 이슈 + 실행 중 정보                    |
| GET    | `/api/v1/requests`                | 요청 그룹 + 스케줄러 요약 (파일 보드)         |
| GET    | `/api/v1/requests/{id}/schedule` | 의존성 그래프 + 실제 스케줄러 판단       |
| GET    | `/api/v1/runs?issue=&limit=`      | registry의 최근 실행 시도                     |
| POST/PATCH/DELETE | `/api/v1/issues[...]`  | 이슈 CRUD (file tracker)                     |
| PUT    | `/api/v1/workflow/states`         | 컬럼 추가 / 삭제 / 이름변경 / 순서변경        |
| GET/PUT| `/api/v1/workflow/prompts/<state>`| 컬럼 스테이지 프롬프트 조회 / 편집            |
| PUT    | `/api/v1/workflow/branch-policy`  | feature base / merge target 브랜치 갱신       |
| GET/POST | `/api/v1/workflow/presets[...]` | 레인 프리셋 목록 / 적용 (`/apply`)            |
| *      | `/api/v1/chat/...`                | 운영자 채팅 세션 + WebSocket 스트림           |
| GET/POST | `/api/v1/projects[...]`         | 프로젝트 목록·생성·조회·열기                  |
| GET/POST | `/api/v1/git/...`               | 브랜치·히스토리·비교/diff·병합·푸시·PR        |
| GET/POST | `/api/v1/preview[...]`          | 미리보기 상태와 시작·재시작·중지              |
| GET    | `/api/v1/stats?days=N`            | 집계된 실행 통계                              |
| POST   | `/api/v1/refresh`                 | poll + reconcile 즉시 트리거                  |
| POST   | `/api/v1/{id}/pause` `/resume`    | 실행 중 워커 보류 / 재개                      |
| POST   | `/api/v1/issues/{id}/skip-document` | idle Document 티켓을 Human Review로 이동 (구 별칭: `/skip-learn`) |

#### 리버스 프록시·터널 보안

cloudflared, ngrok, nginx 같은 프록시로 보드를 공개할 때는 공개 origin을
허용하고 모든 운영자 `/api/` 요청에 bearer token을 요구하는 구성이 안전하다:

```bash
export SYMPHONY_TRUSTED_ORIGINS=https://symphony.example.com
export SYMPHONY_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
symphony ./WORKFLOW.md --host 0.0.0.0 --port 9999

curl -H "Authorization: Bearer $SYMPHONY_API_TOKEN" \
  http://127.0.0.1:9999/api/v1/state
```

토큰이 설정되면 내장 웹 앱은 첫 `401` 응답 뒤 토큰을 요청하고, 현재 탭의
`sessionStorage`에만 저장한 뒤 API 요청과 Chat WebSocket에 자동으로 붙인다.
값이 없거나 공백뿐이면 기존 루프백 기본 동작을 유지한다. 공백 없는 임의의 강한
값을 사용하고 `WORKFLOW.md`, 셸 기록, 스크린샷, 로그에는 남기지 않는다.
`symphony doctor`에서 토큰 게이트 상태와 일치할 수 없는 값을 확인할 수 있다.
여러 origin은 쉼표로 구분한다. 호스트 이름만 쓰면 모든 scheme/port와 일치하고,
`*`는 모든 origin을 신뢰하므로 피하는 편이 안전하다. 브라우저는 WebSocket
handshake에 Authorization 헤더를 넣을 수 없어 Chat 연결에서 토큰을 한 번
`?token=`으로 전달한다. 프록시·접근 로그에서 query string을 제외하거나
마스킹하고, 가능하면 Cloudflare Access 같은 상위 인증 계층도 함께 사용한다.
관리형 서비스 제어기는 `/api/v1/health`에만 유효한 별도의 실행별 임의 capability를
사용하며 운영자 API token을 저장하거나 재사용하지 않는다.
관리형 시작은 이 capability를 256비트 임의 값으로 생성한다. health capability
헤더는 URL-safe 32–128자만 허용하므로 짧거나 형식이 잘못된 수동 service ID는
bearer 인증을 우회할 수 없다.

### CLI Kanban TUI (primary UI)

```bash
symphony tui ./WORKFLOW.md
# equivalent
symphony ./WORKFLOW.md --tui
```

#### Recommended default: TUI + JSON API together

TUI는 기본 운영자 뷰이고 JSON API는 프로그래밍 / curl 친화적 뷰다. `WORKFLOW.md`에
`server.port`를 고정하고 `--tui`로 실행하면 둘을 한 프로세스에서 함께 돌릴 수
있다(내장 웹 어드민 UI도 같은 포트에서 서빙된다):

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
symphony service start ./WORKFLOW.md --port 9999
symphony service status ./WORKFLOW.md
symphony service restart ./WORKFLOW.md
symphony service stop ./WORKFLOW.md
symphony service logs ./WORKFLOW.md
```

`service start`는 스폰 전에 `symphony doctor`를 실행하고, Python 모듈 러너로
오케스트레이터를 시작한다. 내장 웹 어드민 UI는 오케스트레이터 포트에서
서빙된다. 명령은 셸 없이 실행되므로, 같은 경로가 macOS, Linux, Windows에서
동일하게 동작한다.

어드민 UI는 읽기 전용이 아니다: 실행 중인 카드에 **Pause / Resume** 버튼이
나타나고 헤더의 refresh 버튼이 오케스트레이터 `poll + reconcile`을 트리거한다.
헤더는 또한 `agent.feature_base_branch`와 `agent.auto_merge_target_branch`를
위한 실제 로컬 git 브랜치 드롭다운을 보여주므로, 운영자가 YAML을 손으로
편집하지 않고도 새 기능 브랜치가 어디서 시작하고 Document 머지가 어디로 떨어질지
고를 수 있다.

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
  backends/          AgentBackend Protocol + factory + normalized events;
                     codex.py, claude_code.py, gemini.py, agy.py, kiro.py,
                     opencode.py, pi.py, prime_agent.py adapters
  trackers/          TrackerClient Protocol + factory; file.py (locked
                     Markdown ticket mutations), jira.py, linear.py, _retry.py
  workflow/
    parser.py        WORKFLOW.md frontmatter/body parser
    config.py        frozen config dataclasses (incl. agent.stage_kinds)
    builder.py       ServiceConfig construction + validation
    mutate.py        comment-preserving workflow edits for the web UI
    presets.py       lane presets (4-lane default, 8-lane deep)
    preflight.py     dispatch-time validation
  orchestrator/
    core.py          scheduler/state machine (blocked_by-aware dispatch)
    run_registry.py  SQLite WAL run leases + issue flags
    contracts.py     stage-contract validation helpers
  cli/
    main.py          root dispatch + `symphony [WORKFLOW]`
    board.py         `symphony board ...` validated ticket tool + `graph`
    doctor.py        `symphony doctor` WORKFLOW.md preflight checks
  utils/             auto_merge.py, git_inspect.py, git_ops.py, git_sandbox.py,
                     archive.py, keep_awake.py, wiki_sweep.py
  notifications/     Slack state-transition notifications
  tui/               Textual Kanban TUI package
  web/static/        built-in browser app assets (projects / board / workflow /
                     git / chat / preview / stats / settings)
  webapi.py          web app REST routes + static SPA serving
  server.py          aiohttp server, health/state/refresh routes
  projects.py        multi-project registry + managed service metadata
  product_preview.py detached target-branch preview lifecycle
  chat.py            operator chat sessions + board-intake protocol
  continuous_improvement.py  idle-time improvement-proposal loop
  i18n.py            TUI/doc language switching
  stats.py           .symphony/stats.jsonl aggregation
  skills.py          SKILL.md discovery + prompt injection
  service.py         `symphony service` background lifecycle
  progress_md.py     WORKFLOW-PROGRESS.md live mirror
  mock_codex.py      demo backend via `python -m symphony.mock_codex`
  agent.py           back-compat shim re-exporting backends.* symbols
tui-open.sh            launcher (macOS / Linux): doctor preflight + open TUI in a new terminal window
tui-open.bat           Windows equivalent
```

## Tests

개발 의존성을 설치한 뒤 다음 명령을 실행한다. `symphony-pyright`는
실행 중인 인터프리터를 Pyright에 전달한다.

```bash
python -m pytest -q
python -m ruff check src tests
symphony-pyright
```

테스트 스위트(1614 passed, 7 skipped)는 업스트림 적합성 스위트, 백엔드 단위
테스트(팩토리, 이벤트 정규화, CLI별 명령/세션 처리), 보드 도구 DAG 검증,
run registry 영속성, file tracker locking, 웹 API contract, 채팅 인테이크,
레인 프리셋, 그리고 TUI 앱에 대한 Textual `Pilot` 구동 스모크 테스트를
포함한다. 실제 CLI를 상대로 한 서브프로세스 구동 통합 테스트는 의도적으로
CI에 포함하지 않았다 — 로컬에서 실행한다.

## Design notes

### Why eight different lifecycles behind one Protocol?

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
- **AGY / Antigravity CLI**는 호출당 1회로 실행된다. Symphony는 렌더링된
  프롬프트를 `agy --print "$(cat)"`의 stdin으로 보내고
  `--dangerously-skip-permissions`를 붙이며,
  `resume_across_turns`가 true이면 continuation 턴에 `--continue`를 붙인다.
- **Kiro CLI**는 headless chat 모드로 실행된다. Kiro는 piped stdin을 첫
  메시지로 읽지 않으므로 Symphony는 `"$(cat)"`으로 stdin을 위치 chat 입력
  인자로 전달하고, `resume_across_turns`가 true이면 그 입력 앞에
  `--resume`을 넣는다. `symphony doctor`는 `KIRO_API_KEY` 또는 성공한
  `kiro-cli whoami` 로그인 확인을 허용한다.
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
- **Prime Agent**는 Pi에서 파생된 독립 CLI지만 `--resume <id>`를 사용하는
  턴별 JSONL 서브프로세스다. `/login` OAuth 또는 provider API 키를 CLI에 맡기고,
  Symphony는 `~/.prime/agent/auth.json`을 검사만 하며 자격 증명을 읽지 않는다.
  파일 보드에서는 티켓이 terminal 상태에 도달한 뒤 Prime Agent가 `agent_end`를
  flush하지 않고 정상 종료해도 완료로 인정하며, non-zero 종료는 계속 실패로 처리한다.

`AgentBackend` Protocol이 이런 차이를 감춘다. 오케스트레이터는 정규화된
이벤트(`session_started`, `turn_completed`, `turn_failed` 등)와 최신 사용량 /
레이트 리밋 스냅샷만 본다.

### What the TUI and web app do and do not do

웹 앱은 파일 보드를 위한 전체 브라우저 편집기다. 같은 tracker / workflow 모듈을
통해 이슈 생성/수정/삭제, 드래그 상태 이동, 컬럼/프롬프트 편집, 브랜치 정책
갱신을 수행한다. TUI는 키보드 운영에 최적화되어 있으며 터미널을 벗어나지 않고
티켓 생성/수정, archive, Done gate confirm, Pause / Resume, Document skip, filter,
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
- Gemini, AGY, Kiro 토큰 사용량은 CLI가 안정적인 형태로 보고하지 않으므로,
  그 백엔드들의 합계는 0에 머문다.
- Gemini의 멀티턴 연속성은 지원하지 않는다(CLI에 세션 프로토콜이 없다). 각
  `run_turn`은 독립적이다. AGY와 Kiro는 CLI continuation 플래그를 쓰지만
  토큰 사용량은 노출하지 않는다.

## Contributing

PR을 환영한다. 외부 기여는 기본적으로 `dev`를 대상으로 한다 — 전체 리뷰
체크리스트는 [CONTRIBUTING.md](CONTRIBUTING.md)와 PR 템플릿을 참고한다. PR을 열기
전에:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q          # must stay green
python -m ruff check src tests
symphony-pyright
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
