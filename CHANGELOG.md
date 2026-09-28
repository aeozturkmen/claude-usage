# Changelog

## 2026-09-28

- Add pricing for Fable 5.1 / 5, Mythos 5.1 / 5, Opus 5.5 / 5 / 4.8, Sonnet 5, and legacy Opus 4 / 4.1, Sonnet 4 / 3.7, Haiku 3.5 / 3 (Fable models were previously costed at $0)
- Price 1-hour TTL cache writes at 2x input: the scanner now stores `ephemeral_1h_input_tokens` separately (one-time backfill for existing DBs via `PRAGMA user_version`)
- Move pricing into `pricing.py`, shared by CLI and dashboard (dashboard JS table is injected at render time)
- Strip `[1m]` context suffixes and use longest-prefix matching (opus-5-5 no longer priced as opus-5; claude-opus-4 no longer swallows future 4.x)
- Unknown models fall back to the newest model of their family (Fable/Mythos/Opus/Sonnet/Haiku)
- Fix tests reading the developer's real Cowork sessions and real DB (`/api/data` default arg was frozen at import)
- Store a USD cost per turn (`turns.cost_usd`, `sessions.total_cost`); CLI and dashboard sum it, and it is recomputed when pricing changes
- Price sessions, projects and branches per turn model instead of the session's primary-model label
- Add fast-mode multiplier (`usage.speed == "fast"`) and web search cost ($10 / 1K requests)
- Keep final token tallies when a scan lands mid-stream (a stored message keeps the larger counts)
- Rescan no longer deletes the DB; it re-reads every transcript and keeps history from deleted ones
- Session titles (custom title > AI title > first prompt), worktree sessions folded into their repo
- Month-to-date card with projection and optional budget (`CLAUDE_USAGE_BUDGET` or click to set), daily cost line
- Local-time day buckets and range bounds (fixes a one-day shift in UTC+ timezones); `ThreadingHTTPServer`
- Fix message_id lookups bypassing the partial index (full rescan / backfill went from ~3 min to ~5 s)

## 2026-04-09

- Fix token counts inflated ~2x by deduplicating streaming events that share the same message ID
- Fix session cost totals that were inflated when sessions spanned multiple JSONL files
- Fix pricing to match current Anthropic API rates (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5)
- Add CI test suite (84 tests) and GitHub Actions workflow running on every PR
- Add sortable columns to Sessions, Cost by Model, and new Cost by Project tables
- Add CSV export for Sessions and Projects (all filtered data, not just top 20)
- Add Rescan button to dashboard for full database rebuild
- Add Xcode project directory support and `--projects-dir` CLI option
- Non-Anthropic models (gemma, glm, etc.) no longer incorrectly charged at Sonnet rates
- CLI and dashboard now both compute costs per-turn for consistent results
