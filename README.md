# Claude Usage Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![claude-code](https://img.shields.io/badge/claude--code-black?style=flat-square)](https://claude.ai/code)

**Pro and Max subscribers get a progress bar. This gives you the full picture.**

Claude writes detailed usage logs locally — token counts, models, sessions, projects — regardless of your plan. This dashboard reads those logs and turns them into charts and cost estimates. Works on API, Pro, and Max plans.

> **This is a fork of [phuryn/claude-usage](https://github.com/phuryn/claude-usage) with UI and usability improvements.**  
> Big thanks to [@phuryn](https://github.com/phuryn) and [The Product Compass Newsletter](https://www.productcompass.pm) for the original work. 🙌

![Claude Usage Dashboard](docs/screenshot.png)

---

## What's new in this fork

- **Compact model filter** — replaced flat pill buttons (which overflow on wide model lists) with a single dropdown multi-select. Shows "All Models" by default; displays `N / M models` when filtered.
- **Custom favicon** — clean icon so the browser tab is easy to spot.
- **macOS auto-start** — included LaunchAgent plist instructions so the dashboard starts automatically on login.
- **`week` command** — `python3 cli.py week` prints a 7-day summary (per-day + by-model) in the terminal.
- **Current models and accurate costs.** The fork prices:
  - Fable 5.1/5, Mythos, Opus 5.5/5/4.8 and Sonnet 5
  - 1-hour cache writes at 2x input
  - fast mode (`usage.speed`)
  - web search requests

  Each turn is priced with its own model. Sessions that mix models (subagents, `/model` switches) and the project/branch tables are no longer billed at the session's primary model.
- **Session titles.** A Title column shows the session name: your `/rename` first, then Claude's auto title, then the first prompt.
- **Month-to-date spend and budget.** A card shows spend so far and a projection for the month end. Click it to set a monthly budget, which is stored in your browser. You can also set a default with the `CLAUDE_USAGE_BUDGET` environment variable.
- **Daily cost line** on the token chart.
- **Non-destructive Rescan.** It re-reads every transcript without deleting the database. Usage from transcripts that Claude Code has since cleaned up is kept.
- **Worktrees fold into their repo.** Sessions in `.claude/worktrees/*` and other git worktrees count toward the parent project.
- **Local-time days.** Daily buckets and ranges such as "This Month" use your timezone, so they no longer shift by a day in UTC+ zones.

---

## What this tracks

Works on **API, Pro, and Max plans** — Claude writes local usage logs regardless of subscription type. This tool reads those logs and gives you visibility that Anthropic's UI doesn't provide.

Captures usage from:
- **Claude Code CLI** (`claude` command in terminal)
- **VS Code extension** (Claude Code sidebar)
- **Dispatched Code sessions** (sessions routed through Claude Code)
- **Claude.ai Cowork sessions** — captured from `~/Library/Application Support/Claude/local-agent-mode-sessions/` which the Claude desktop app writes locally

Usage is broken down **project by project**, so you can see which codebases or workflows consume the most tokens.

---

## Requirements

- Python 3.8+
- No third-party packages — uses only the standard library (`sqlite3`, `http.server`, `json`, `pathlib`)

> Anyone running Claude Code already has Python installed.

## Quick Start

No `pip install`, no virtual environment, no build step.

### Windows
```
git clone https://github.com/aeozturkmen/claude-usage
cd claude-usage
python cli.py dashboard
```

### macOS / Linux
```
git clone https://github.com/aeozturkmen/claude-usage
cd claude-usage
python3 cli.py dashboard
```

---

## Usage

> On macOS/Linux, use `python3` instead of `python` in all commands below.

```
# Scan JSONL files and populate the database (~/.claude/usage.db)
python cli.py scan

# Show today's usage summary by model (in terminal)
python cli.py today

# Show the last 7 days (per-day breakdown + by-model totals)
python cli.py week

# Show all-time statistics (in terminal)
python cli.py stats

# Scan + open browser dashboard at http://localhost:8080
python cli.py dashboard

# Custom host and port via environment variables
HOST=0.0.0.0 PORT=9000 python cli.py dashboard

# Scan a custom projects directory
python cli.py scan --projects-dir /path/to/transcripts
```

The scanner is incremental — it tracks each file's path and modification time, so re-running `scan` is fast and only processes new or changed files.

By default, the scanner checks both `~/.claude/projects/` and the Xcode Claude integration directory (`~/Library/Developer/Xcode/CodingAssistant/ClaudeAgentConfig/projects/`), skipping any that don't exist. Use `--projects-dir` to scan a custom location instead.

---

## How it works

Claude Code writes one JSONL file per session to `~/.claude/projects/`. Each line is a JSON record; `assistant`-type records contain:
- `message.usage.input_tokens` — raw prompt tokens
- `message.usage.output_tokens` — generated tokens
- `message.usage.cache_creation_input_tokens` — tokens written to prompt cache (`cache_creation.ephemeral_1h_input_tokens` is the 1-hour-TTL part)
- `message.usage.cache_read_input_tokens` — tokens served from prompt cache
- `message.usage.speed` — `fast` for fast-mode requests
- `message.usage.server_tool_use.web_search_requests` — billed web searches
- `message.model` — the model used (e.g. `claude-sonnet-4-6`)

`scanner.py` parses those files and stores the data in a SQLite database at `~/.claude/usage.db`, including a USD cost per turn. Stored costs are recomputed automatically when `pricing.py` changes, and schema upgrades backfill new fields from the transcripts that still exist.

`dashboard.py` serves a single-page dashboard on `localhost:8080` with Chart.js charts (loaded from CDN). It auto-refreshes every 30 seconds and supports model filtering with bookmarkable URLs. The bind address and port can be overridden with `HOST` and `PORT` environment variables (defaults: `localhost`, `8080`).

---

## Cost estimates

Costs are calculated using **Anthropic API pricing as of September 2026** ([claude.com/pricing#api](https://claude.com/pricing#api)). The table lives in [`pricing.py`](pricing.py) and is shared by the CLI and the dashboard.

**Only Claude models (names containing `fable`, `mythos`, `opus`, `sonnet`, or `haiku`) are included in cost calculations.** Local models, unknown models, and any other model names are excluded (shown as `n/a`). Context-window suffixes such as `[1m]` and dated snapshot IDs are matched to their base model; unknown future versions fall back to the newest model of their family.

Claude Code writes most prompt-cache entries with the **1-hour TTL**, which costs 2x input (vs 1.25x for the 5-minute TTL). The scanner records both from `usage.cache_creation` and prices them separately.

Prices are USD per million tokens:

| Model | Input | Output | Cache Write (5m) | Cache Write (1h) | Cache Read |
|-------|-------|--------|------------------|------------------|-----------|
| claude-fable-5-1 | $10.00 | $50.00 | $12.50 | $20.00 | $0.25 |
| claude-fable-5 | $10.00 | $50.00 | $12.50 | $20.00 | $1.00 |
| claude-mythos-5-1 | $10.00 | $50.00 | $12.50 | $20.00 | $1.00 |
| claude-mythos-5 | $10.00 | $50.00 | $12.50 | $20.00 | $1.00 |
| claude-opus-5-5 | $4.00 | $20.00 | $5.00 | $8.00 | $0.20 |
| claude-opus-5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| claude-opus-4-8 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| claude-opus-4-7 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| claude-opus-4-6 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| claude-opus-4-5 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| claude-opus-4-1 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| claude-opus-4 | $15.00 | $75.00 | $18.75 | $30.00 | $1.50 |
| claude-sonnet-5 | $2.00 | $10.00 | $2.50 | $4.00 | $0.20 |
| claude-sonnet-4-6 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| claude-sonnet-4-5 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| claude-sonnet-4 | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| claude-3-7-sonnet | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| claude-haiku-4-5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| claude-3-5-haiku | $0.80 | $4.00 | $1.00 | $1.60 | $0.08 |
| claude-3-haiku | $0.25 | $1.25 | $0.31 | $0.50 | $0.03 |

> **Note:** These are API prices. If you use Claude Code via a Max or Pro subscription, your actual cost structure is different (subscription-based, not per-token).

---

## macOS — Auto-start on login

Create `~/Library/LaunchAgents/com.claudeusage.dashboard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claudeusage.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/claude-usage/cli.py</string>
        <string>dashboard</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/claude-usage</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/claudeusage-dashboard.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/claudeusage-dashboard.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/YOUR_USERNAME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
```

Replace `/path/to/claude-usage` and `YOUR_USERNAME` with your actual values, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.claudeusage.dashboard.plist
```

---

## Files

| File | Purpose |
|------|---------|
| `pricing.py` | Model price table and cost formula (single source of truth) |
| `scanner.py` | Parses JSONL transcripts, writes to `~/.claude/usage.db` |
| `dashboard.py` | HTTP server + single-page HTML/JS dashboard |
| `cli.py` | `scan`, `today`, `week`, `stats`, `dashboard` commands |
