# Site rules - local per-site knowledge (NEVER commit)

SuperQA is site-agnostic. Everything specific to one site lives OUTSIDE the repo, under
`~/.superqa/`:

```
~/.superqa/
├── superqa.db                  # accounts/vars (SQLite), run history
├── scenarios/<site>/*.yaml     # test cases per site
├── reports/                    # run evidence
└── sites/<site>/rules.md       # the site's playbook (this file)
```

Committing an entry URL, tenant id, account, or internal hostname into the skill repo
or any pushed scenario is a hard failure. Before any commit: verify staged files
contain no `~/.superqa` content and no site hostnames.

## rules.md structure (create on first exploration, update every run)

```markdown
# <site> QA rules
updated: 2026-07-10

## Entry
- entry URL: <url>            # or {{entry_url}} in the var store
- login: 버튼 클릭만 / id+pw 폼 / SSO (타입 선택 필요)
- accounts: var store keys (username, password, ...)

## Navigation map
| screen | how to reach | URL pattern | notes |
|---|---|---|---|

## Popups / new tabs
| trigger (click) | result | handling |
|---|---|---|
| 입장 버튼 | 새 탭으로 홈 열림 | expect_popup: true |

## Dialogs
| when | text | policy |

## Quirks
- selectors that change per deploy, iframes, slow screens, timing notes

## Case inventory
- scenario files that exist and what they cover; gaps to fill next
```

## Why a separate file per site

- The agent reads it FIRST next time - no rediscovery of auth gates and popup mazes.
- Drift self-heals: when a selector/route stops matching, fix that row after the run.
- It is the natural place to paste developer handoff notes ("feature X shipped,
  screens Y/Z changed").
