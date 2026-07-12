# Scenario YAML schema

Location: `~/.superqa/scenarios/<site>/<name>.yaml`. Everything is plain YAML a
non-developer can edit.

```yaml
name: 로그인-정상            # shown in TUI and reports
site: myshop                 # var-store scope + grouping
base_url: https://myshop.example.com
language: ko                 # report language (ko|en)
tags: [smoke, login]
policy:
  dialogs: accept            # accept | dismiss | fail
  popups: follow             # follow (switch to new tab) | ignore | fail
  fail_on_console_error: false
  fail_on_http_error: false
  ignore_effects:            # noise substrings -> effects still recorded, but
    - "[Analytics]"          # split into the report's "ignored" section

steps:
  - action: goto
    url: "{{entry_url}}"     # any value can come from the var store
    description: 사이트 접속
  - action: fill
    selector: "#username"
    value: "{{username}}"
    description: 아이디 입력
  - action: fill
    selector: "input[type='password']"
    value: "{{password}}"    # secrets are masked in reports automatically
    description: 비밀번호 입력
  - action: click
    selector: {role: button, name: 로그인}
    description: 로그인 버튼 클릭
  - action: expect_text
    selector: "#welcome"
    value: "{{username}}"
    description: 환영 문구에 아이디 표시 확인
```

## Actions

| action | fields | meaning |
|---|---|---|
| goto | url | navigate |
| click / dblclick | selector, expect_popup | click; `expect_popup: true` waits for and switches to the new tab |
| fill | selector, value | clear + type |
| press | selector, value | key (Enter, ArrowDown...) |
| select | selector, value | select option |
| check / uncheck | selector | checkbox |
| hover | selector | mouse over |
| wait | value | seconds (last resort; prefer expect_*) |
| expect_visible | selector | element visible |
| expect_text | selector, value | value substring in element text |
| expect_url | url | substring in current URL |
| switch_tab | value | `latest` or tab index |
| close_tab | - | close current, fall back to last tab |
| scroll | value | wheel pixels down |
| screenshot | - | extra evidence shot |
| login | - | macro: fills username/password vars into common login forms |

Common fields: `description` (user language, required in practice), `timeout_ms`
(default 10000), `optional: true` (failure recorded but does not fail the run),
`retry: N` (re-attempt a flaky step up to N extra times, 1s apart - e.g. viewers
that swallow the first click while still initializing).

## Selectors

- String: CSS (`#id`, `.cls button`) or `text=문구`.
- Dict with fallback chain, tried in order until one matches:
  `{testid: ..., css: ..., role: ..., name: ..., text: ...}`.

## Variables

`{{key}}` anywhere in `url`/`value` resolves from the SQLite store: site scope first,
then global scope `*`. Set them with `superqa vars set <site> <key> <value>`; keys
matching pass/pw/secret/token/pin are flagged secret and masked in reports.

## Popups / dialogs

- Dialogs (alert/confirm/prompt/beforeunload) are auto-handled per `policy.dialogs`
  and always recorded as side effects.
- New tabs: `policy.popups: follow` (default) makes a click that opens a tab continue
  there automatically; use `expect_popup: true` on the click when the tab is required,
  so its absence fails the step.
