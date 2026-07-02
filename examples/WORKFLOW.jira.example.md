---
tracker:
  kind: jira
  # Atlassian site base URL — DO NOT include /rest/api/3, the adapter
  # appends it. For self-managed Data Center, point this at the equivalent
  # base; the adapter still calls /rest/api/3/* paths.
  endpoint: https://your-domain.atlassian.net
  # The Jira project key (the short prefix on every issue key, e.g.,
  # "PROJ" for "PROJ-123"). Used to scope the JQL: project = "PROJ".
  project_slug: PROJ
  # Atlassian Cloud uses Basic Auth = (account email, API token).
  # Mint the token at id.atlassian.com → Security → "Create and manage
  # API tokens". `$VAR` indirection resolves at load time, so the token
  # never lives in this file.
  email: $JIRA_EMAIL
  api_key: $JIRA_API_TOKEN
  # Jira's stock workflow uses "To Do" / "In Progress" / "Done". Tune
  # these to match your project's actual workflow statuses — the names
  # are inserted verbatim into the JQL `status in (...)` clause and
  # matched case-insensitively when resolving transitions.
  active_states: ["To Do", "In Progress", "Verify", "Learn"]
  terminal_states: ["Human Review", "Done", "Cancelled", "Blocked"]
  # Auto-archive sweep — terminal-state issues older than archive_after_days
  # transition to archive_state. Set to 0 to disable. The target state must
  # exist as a reachable transition in the issue's workflow.
  archive_state: Done
  archive_after_days: 30

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

agent:
  kind: claude
  max_concurrent_agents: 1
  max_turns: 100

claude:
  command: claude -p --output-format stream-json --include-partial-messages --verbose
---

You are Symphony, working an issue from Jira.

Issue: {{ identifier }} — {{ title }}
State: {{ state }}
URL:   {{ url }}

{{ description }}

When the work for the current state is complete, transition the issue
to the next status in the workflow using the Jira UI or the API. The
orchestrator polls the board on the next tick and routes the issue to
the appropriate Symphony stage.
