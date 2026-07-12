# SIGNAL - live market & customer signal (voice of customer)

Cross-cutting evidence source, not a domain. Gets RESEARCH / DISCOVER / GTM *real* recent market
and consumer evidence instead of asserting it. Rank by **engagement, not SEO**. **Read-only,
keyless by default**: no API key, login, or cookie; never posts, contacts, or scrapes behind auth.
Separate evidence (quote, count) from inference; label unvalidated claims hypotheses.

Method from [last30days](https://github.com/mvanhorn/last30days-skill) (MIT); keyless read
techniques from [Agent-Reach](https://github.com/Panniantong/Agent-Reach) (MIT).

## When to reach here

Before a draft asserts what the market or a customer wants:
- DISCOVER - real pains, competitor gaps, recurring requests, OST opportunities in the customer's words.
- RESEARCH - persona pains / triggers, sentiment themes + volume, competitor positioning, market-sizing demand inputs.
- GTM / GROWTH - ICP trigger events, positioning language, battlecard claims.
- Direct asks - "what are people saying about X", demand for Y, "X vs Y", trends.

## Method (self-contained, no keys)

Run with `WebSearch` / `WebFetch`:
1. **Parse** topic + query type (news / recommendations / comparison / general).
2. **Resolve where first** - `WebSearch` for the relevant subreddits, forums, review sites, RSS feeds, repos.
3. **Search recent** - multi-query, last ~30 days.
4. **Score by engagement** - upvotes/points, comment volume, GitHub stars/PR velocity, Polymarket odds, x relevance x freshness.
5. **Pull the real voice** - top comments and verbatim quotes, not headlines. Heavy pages: `https://r.jina.ai/<url>` -> keyless markdown.
6. **Cluster** - merge the same story across platforms.
7. **Synthesize** - cited brief, inline `[name](url)`, ranked by engagement, each signal with count + date.

## Sources (keyless, read-only - live-tested 2026-06)

Public endpoints, no key/login/cookie. Patterns are examples; if one fails, fall back to `WebSearch` / `WebFetch`.
- **Hacker News** - dev / tech consensus. `hn.algolia.com/api/v1/search_by_date?query=<q>&tags=story&numericFilters=created_at_i>` + unix(now-30d) -> `points`, `num_comments`. Direct, reliable.
- **GitHub** - demand signal. `api.github.com/search/repositories?q=<q>&sort=stars` (60/hr unauth) -> stars, releases, issue volume.
- **Polymarket** - real-money forward odds. `gamma-api.polymarket.com/markets?active=true&closed=false`.
- **Jina Reader** - `https://r.jina.ai/<url>`: any non-blocked page as markdown (rate-limited without a key). Read reviews, forums, articles.
- **Public RSS** - blog / news / media feeds (e.g. `hnrss.org`, a site's `/rss`). Note: subreddit RSS is blocked - see Reddit.
- **WebSearch / WebFetch** - the "where" step, general recency, AND the keyless path to Reddit/social: search engines index those threads, so `WebSearch "site:reddit.com <topic>"` surfaces real consumer voice (pros/cons, upvote-weighted sentiment) without hitting the blocked API.
- **Reddit / X / YouTube / TikTok (direct) - NOT keyless.** Reddit's anonymous API, RSS, and reader-proxy all 403 ("log in or use developer token"); the rest need keys. For depth (full threads, vote-counted top comments, transcripts) use the `last30days` accelerator on the user's own access; for the keyless tier, reach this content via `WebSearch` above.

## Safety boundary (hard stops)

- **No writes** - no posting, commenting, voting, liking, following, DMing. Outward/irreversible = explicit-consent hard stop, out of scope.
- **No credentials** - no login, cookie, or session scraping; no stored secrets. If a source needs auth, skip it.
- **Respect ToS / rate limits.** Do not route around blocks.
- **No contact** with real people or accounts. Research, not reach-out.

## Accelerate: delegate to last30days if installed

No external dependency by default. If the richer `last30days` skill is installed, hand it the deeper keyed work.
1. **Detect** `~/.claude/skills/last30days/SKILL.md` (or `/last30days`).
2. **Present -> delegate.** `/last30days <topic>` adds YouTube transcripts, vote-counted comments, X / TikTok / Bluesky, parallel multi-query - on **the user's own keys/sessions**. Use its cited brief; do not re-implement the engine.
3. **Absent -> bundle (default).** Run the keyless method above.

superpm never configures keys, logs in, or performs outreach. Same artifact shape either way; only depth differs.

## Feeding the frameworks

Signal is an input, not the deliverable:
- `research.md` - `user-personas`, `sentiment-analysis`, `competitor-analysis`, demand side of `market-sizing`.
- `discover.md` - `brainstorm-ideas`, `analyze-feature-requests`, `opportunity-solution-tree`.
- `gtm.md` / `growth.md` - `ideal-customer-profile`, `positioning`, `competitive-battlecard`.

## Critic completeness anchors

Every market/customer claim cites a real recent source (link) or is labeled a hypothesis; engagement
numbers carry source + count + date; recency window (~30d, or stated) named; report volume +
distribution, never one viral post as representative; evidence separated from inference; every
source obtained read-only and keyless - none via outreach or credentialed scraping.
