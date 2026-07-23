# Admin Dashboard — Ticket / Brain Dump

## Status
**PAUSED** — web dashboard built and working locally (via `fly proxy`),
but Fly.io edge proxy routing to the public internet not resolved.

Current code committed and deployed. Access via:
```
fly proxy 8080:8080 -a slack-daily-trivia
# then http://localhost:8080/admin/dashboard
```

---

## What Was Built

### Files

| File | Purpose |
|------|---------|
| `src/admin_server.py` | FastAPI app: `GET /admin/dashboard` (HTML), 7 JSON API endpoints |
| `src/templates/dashboard.html` | Dashboard with Chart.js line chart, overview cards, tables |
| `src/stats_store.py` | 8 new query methods: daily counts, user summaries, category/difficulty stats |

### Methods added to `stats_store.py`

- `get_daily_answer_counts(days)` — time series: date, total, correct, unique_users
- `get_all_user_summaries(page, per_page, sort)` — paginated user list
- `get_total_answer_count()` — total answers
- `get_total_user_count()` — distinct users
- `get_global_accuracy()` — overall %
- `get_category_stats()` — accuracy per category
- `get_difficulty_stats()` — accuracy per difficulty
- `get_workspace_count()` — configured workspaces

### Dashboard sections
- 4 overview cards: total answers, total users, accuracy, workspaces
- Daily answer line chart (last 30 days, 3 series: answers, correct, unique users)
- Category accuracy table
- Difficulty accuracy table
- Top 10 users by accuracy with visual bars
- Workspace configs table

### Auth
Removed — no authentication on admin routes (read-only, URL not publicized).

---

## Fly.io Routing Issue

### The problem
Fly proxy (WireGuard tunnel) connects fine: `fly proxy 8080:8080` → dashboard works.
But public `https://slack-daily-trivia.fly.dev/admin/dashboard` times out / resets.

### What was tried
- [[services]] block with port 8080 external → edge didn't route
- [[services]] with port 443 + 80 external → connection reset
- Dedicated IPv4 → swapped to shared IPv4
- [http_service] shorthand (current) → TLS handshake succeeds, HTTP/2 request sent, no response
- Uvicorn host `0.0.0.0` → proxy logs: "instance refused connection" (timing issue?)
- Uvicorn host `[::]` → proxy refused connection (Fly proxy connects via IPv4)

### Current fly.toml
```toml
[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
```

### Current admin_server.py
Uvicorn in daemon thread, host=`0.0.0.0`, port=`8080`.

### Key diagnostic: `curl --resolve` works (TLS + HTTP/2 handshake succeeds) but
response times out. Suggests edge proxy can reach machine but machine doesn't
respond in time, or health check / warm-up issue.

---

## Future Options

### Option A — Scrap web server, use Slack slash command
- Add `/admin` command, restrict to ADMIN_USERS env var (comma-separated user IDs)
- Respond with ephemeral Slack blocks: overview, category/difficulty breakdowns,
  top users, workspace configs
- Pro: no Fly.io routing issues, no extra deps, instant
- Con: no chart, no pagination, no user search — Slack blocks only
- Implementation: remove admin_server.py, templates/, fastapi/uvicorn/jinja2 deps

### Option B — Debug Fly.io routing further
- Check if health check needs to be configured for the service
- Try adding health check endpoint (`GET /health` → 200)
- Try `force_instance_key` in service config
- Try creating a fresh machine from scratch (delete + redeploy)
- Try custom domain instead of fly.dev

### Option C — Static site with data fetched from JSON API
- Keep JSON API on admin_server.py
- Host dashboard HTML as static files on Fly.io or elsewhere
- JSON API called from browser JS
- Still needs the Fly.io routing solved

### Option D — Admin as separate Fly app
- Create second Fly app (`slack-trivia-admin`) with its own `[http_service]`
- Shares the same volume/data
- Separate deploy, separate URL. Could work even if main app has no HTTP service

---

## Decision (2026-07-22)

**Tabled** — will revisit later. Likely path is Option A (Slack slash command
with user restriction) for simplicity, or Option D (separate app) for web UI.
