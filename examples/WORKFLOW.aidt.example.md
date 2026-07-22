---
# Default-off operator reference for the AIDT Jira resolver. Copy this file to
# WORKFLOW.md, replace the documented paths/catalog, run `symphony doctor`, and
# enable each feature only after its external gates are satisfied.
tracker:
  kind: file
  board_root: ./kanban-aidt
  active_states: [Todo, Coordinating, Ready]
  terminal_states: ["Human Review", Done, Blocked]

polling:
  interval_ms: 30000

workspace:
  # Export an absolute path; the dedicated AIDT provisioner owns each child
  # workspace below this root. `preserve` is required for safe resume.
  root: $AIDT_SYMPHONY_WORKSPACES
  reuse_policy: preserve

agent:
  kind: codex
  max_concurrent_agents: 1
  # The AIDT profile must not use Symphony's generic commit/merge paths.
  auto_commit_on_done: false
  auto_merge_on_done: false

prompts:
  base: ../docs/symphony-prompts/file/base.md
  stages:
    Todo: ../docs/symphony-prompts/file/stages/todo.md
    # Routing owns this coordinator-only lane and blocks it from worker
    # dispatch. The Todo alias is a manifest fallback, not execution authority.
    Coordinating: ../docs/symphony-prompts/file/stages/todo.md
    # Ready is the AIDT child implementation handoff, so it deliberately reuses
    # the existing file-tracker implementation prompt. Later gates remain pending.
    Ready: ../docs/symphony-prompts/file/stages/in-progress.md

server:
  # Declares the dedicated dashboard/API port; loading this file does not
  # start a service. A later managed-surface delivery owns loopback startup.
  port: 9918

jira_intake:
  enabled: false
  endpoint: https://your-domain.atlassian.net
  # Credentials and operator identity stay outside git. The read-only intake
  # calls Jira currentUser(), then requires every returned assignee accountId
  # to equal /myself. It never writes Jira; the local file board owns state.
  email: $JIRA_INTAKE_EMAIL
  api_key: $JIRA_INTAKE_TOKEN
  project: A20
  # Exact, case- and whitespace-sensitive actionable-status allowlist. This
  # reference includes only the observed A20 inbox state; add another literal
  # only after confirming it through the read-only Jira preflight.
  statuses: ["백로그"]
  new_card_state: Todo

aidt_routing:
  enabled: false
  # Intake and routing share one poll snapshot; stale Jira context fails closed.
  source_mode: same_tick_jira
  # Must be an absolute operator-supplied path and treated as immutable after
  # activation. The complete service catalog is fingerprinted into routing
  # evidence.
  aidt_root: /absolute/path/to/AIDT
  minimum_confidence: 90
  states:
    ready: Ready
    review: "Human Review"
    coordinator: Coordinating
  services:
    # Repeat this exact closed shape for every reviewed AIDT service. Routing
    # observes the configured checkout at refs/remotes/origin/aidt-prd; neither
    # cards nor this interface can select a different provisioning base.
    - id: lms-api
      checkout: aidt-lms-api
      kind: backend
      enabled: true
      markers: [gradlew]
      component_aliases: [lms-api, "LMS API"]
      context_anchors: []
      route_anchors: []
      domain_anchors: []

aidt_worktree:
  # This closed block currently accepts only `enabled`. When enabled together
  # with routing, the AIDT provisioner creates isolated service worktrees from
  # the immutable origin/aidt-prd observation. Do not add generic hooks.
  enabled: false
---

This is a default-off operator reference, not an activation record.

The current worktree boundary is deny-all for cleanup: generic terminal-state
cleanup, generic Done, and workers cannot remove an AIDT-owned worktree. It is
preserved pending a later, freshness-bound delivery authorization.

Delivery-stage, local-QA, merge-promotion, Jenkins deploy, dev-QA, completion,
and managed-service fields are intentionally absent. Their contracts remain
pending; do not invent configuration keys or treat port 9918 as a running
service until those frontiers ship and their external gates are proven.
