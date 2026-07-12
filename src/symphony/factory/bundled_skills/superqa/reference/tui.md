# TUI + web admin + recorder + scheduler (the non-developer lane)

## Web admin (most clickable, for anyone)

```bash
superqa serve            # http://127.0.0.1:8760
superqa serve --port 9000 --open
```

A browser dashboard, synced with the TUI/CLI (same scenario folder + SQLite store):

- Left: every scenario grouped by site - recorded (`기록-*`) and agent-authored alike -
  each with a Run button. Toggle headless on/off at the top.
- Right: live run progress (per-step dots), run history with pass/fail badges, and
  inline report viewing (click 리포트).
- Broken scenario files show a warning banner instead of vanishing.

Use this when a non-developer wants to point-and-click; use the terminal TUI below when
you prefer keyboard-driven work in a shell.

## Launch (terminal TUI)

```bash
bash scripts/superqa.sh          # from the skill root
# or, after `pip install -e .`:
superqa
```

Left pane: scenario list. Right: live run log + recent results. Footer shows every key.

| key | does |
|---|---|
| r | run selected scenario (watch the browser do it) |
| a | run all scenarios (regression sweep) |
| n | record a new scenario by clicking in a real browser |
| u | auto QA - give a URL, get a smoke report (opens page, checks links, collects errors) |
| s | schedule selected scenario every N minutes |
| v | accounts/variables (stored in SQLite, passwords masked) |
| o | open the latest HTML report |
| q | quit |

## Recording (browser click -> QA step)

`n` asks for URL / site / name, then opens a real Chrome window with a floating
SuperQA panel (bottom-right):

- Click through the site normally - every click/input becomes a step, described in
  Korean ("'로그인' 클릭", "'아이디'에 'myid' 입력").
- Typed passwords are saved as `{{password}}`, never plain text. Set the real value
  once under `v` (or `superqa vars set <site> password <value>`).
- **검증 추가** then click an element = "assert this is visible" step.
- **일시정지 / 기록 재개** to browse without recording.
- **저장 후 종료** writes the YAML and closes the browser. New tabs opened while
  recording keep recording - the panel follows.

## Scheduling / automation

- In the TUI: `s` on a scenario, enter minutes. Runs headless in the background while
  the TUI stays open; results appear in the log and recent-runs pane.
- Without the TUI: `superqa schedule add <scenario> --every 30` then
  `superqa schedule daemon` (wrap with nohup/launchd/cron for always-on automation).
- One-off automation from scripts/CI: `superqa run --all --site <site> --headless`;
  exit code 0 = green.

## CLI equivalents (same engine, no TUI)

```bash
superqa record <url> --site myshop --name 로그인-정상
superqa run 로그인-정상
superqa run --all --site myshop --headless
superqa auto https://myshop.example.com --site myshop
superqa vars set myshop username myid
superqa vars set myshop password s3cret        # auto-masked
superqa report open                            # latest report
superqa report list                            # run history (+ reports/index.html)
superqa baseline 로그인-정상                    # accept screenshots as visual baseline
superqa run --all --site myshop --headless --junit results.xml   # CI output
superqa serve                                  # web admin (click to run)
superqa doctor                                 # environment check, plain-language fixes
```

Every run automatically compares against the previous run of the same scenario
("지난 실행과 비교" in output and report): new failures and new side-effect types
are flagged, identical runs get "회귀 없음". Known noise can be silenced per site
via `~/.superqa/sites/<site>/ignore.yaml` (see reference/side-effects.md).
