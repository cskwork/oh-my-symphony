---
contract_profile: factory
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Ready, Build, Verify]
  terminal_states: [Done, Blocked]
  state_descriptions:
    Ready: "Waiting for dependencies"
    Build: "Supergoal delivery loop"
    Verify: "Independent exact proof"
    Done: "Verified complete"
    Blocked: "Needs authority or environment"

polling:
  interval_ms: 30000

workspace:
  root: ./.symphony/workspaces
  reuse_policy: preserve

hooks:
  after_create: |
    bash "$SYMPHONY_WORKFLOW_DIR/src/symphony/factory/templates/scripts/factory-setup-worktree.sh"
  before_remove: |
    git -C "$SYMPHONY_WORKFLOW_DIR" worktree remove --force "$PWD" 2>/dev/null || true

agent:
  kind: opencode
  max_concurrent_agents: 1
  max_turns: 8
  max_total_turns: 5
  max_state_turns_by_state:
    Build: 3
    Verify: 2
  max_attempts: 3
  auto_triage_actionable_todo: false
  budget_exhausted_state: Blocked
  auto_commit_on_done: true
  auto_merge_on_done: true
  auto_merge_target_branch: ""
  max_total_tokens: 1250000
  max_total_tokens_by_state:
    Build: 900000
    Verify: 350000
  max_concurrent_agents_by_state:
    Build: 1
    Verify: 1

opencode:
  command: opencode run --format json --auto
  resume_across_turns: true

prompts:
  base: ./src/symphony/factory/templates/docs/symphony-prompts/file/base.md
  stages:
    Ready: ./src/symphony/factory/templates/docs/symphony-prompts/file/stages/ready.md
    Build: ./src/symphony/factory/templates/docs/symphony-prompts/file/stages/build.md
    Verify: ./src/symphony/factory/templates/docs/symphony-prompts/file/stages/verify.md

server:
  port: 9999

system:
  keep_awake: true
---

# Default autonomous development factory

Use `symphony factory init` for a portable project-local copy. The former
production template remains at `examples/advanced/WORKFLOW.file.example.md`.
