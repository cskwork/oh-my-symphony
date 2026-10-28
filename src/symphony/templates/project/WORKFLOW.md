---
defaults: standard
tracker:
  kind: file
  board_root: ./kanban
workspace:
  root: ~/symphony_workspaces/__PROJECT_ID__
  reuse_policy: refresh
agent:
  kind: codex
  auto_merge_push_target: false
server:
  host: __HOST__
  port: __PORT__
---

You are working on {{ issue.identifier }}: {{ issue.title }}.
