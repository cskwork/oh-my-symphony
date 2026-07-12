You are working on `{{ issue.identifier }}`: {{ issue.title }}.

Symphony owns scheduling, dependency order, isolated workspaces, retries, and
the independent Verify turn. Supergoal owns ticket delivery through the
Build -> Improve full spec -> Improve edge cases -> Adversarial Review -> Exact
Verify delivery loop. Follow attached ticket skills; do not reproduce their
instructions in this prompt.

Work only the current state. Read the full ticket at
`{{ issue.full_ticket_path }}` when available. Never skip Verify. Missing
authority or an unavailable required environment goes to `Blocked`; a product
defect found in Verify rewinds to `Build`.
