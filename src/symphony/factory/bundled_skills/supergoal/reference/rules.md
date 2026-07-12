Project standing rules: user-authored constraints in `.supergoal/rules/RULES.md` in the project, read before
every run and honored across all modes. Like `domain-rules.md` but fixed and user-owned, not distilled per-run.

- **Read** at the router stage, before classifying the mode - and again before creating any run worktree
  (gitignored, so it may not follow in; capture it into the conductor). Absent: proceed, no extra
  constraints, never auto-create.
- **Inject** the role-relevant subset into each role subagent, as `domain-rules.md` does.
- **Precedence:** above distilled domain-rules on style/approach, but never weaken safety gates (real tests
  decide; destructive steps need consent).
- **Scaffold** only when the user explicitly asks: copy `templates/rules.md` to
  `<project>/.supergoal/rules/RULES.md` and add `.supergoal/` to `.gitignore`. That is the only repo write.
- **Conservative:** never edit `RULES.md` during a run; change it only when the user directs.
