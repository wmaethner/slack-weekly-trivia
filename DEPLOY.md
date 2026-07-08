# Fly.io Deployment Guide

## Architecture

```
┌─────────────┐     WebSocket      ┌──────────┐
│   Slack     │ ◄────────────────► │ Fly.io   │
│   API       │   (Socket Mode)    │   VM     │
└─────────────┘                    │          │
                                   │ python   │
                                   │ src/     │
                                   │ app.py   │
                                   │          │
                                   │ /data/   │ ← Fly Volume
                                   │ trivia_  │   (persistent)
                                   │ stats.db │
                                   └──────────┘
```

## Pre-flight Checklist

- [ ] Credit card on file at [fly.io](https://fly.io)
- [ ] `flyctl` CLI installed (`brew install flyctl`)
- [ ] `fly auth signup` / `fly auth login` complete
- [ ] Slack app tokens ready (Bot Token, App Token, Signing Secret)
- [ ] Daily volume snapshots enabled (auto, no setup needed)

---

## Phase 0: Create the Slack App

### 0a. Create App & Enable Socket Mode

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it "Daily Trivia", pick your workspace
3. **Socket Mode** → toggle **ON**
4. It prompts you to create an **App-Level Token**. Name it, scope
   `connections:write` → generate.
5. **Copy the App Token now** — it starts with `xapp-` and is only shown once.

### 0b. Bot Token Scopes

**OAuth & Permissions** → Bot Token Scopes → add:

| Scope | Why |
|-------|-----|
| `app_mentions:read` | Detect @mentions |
| `chat:write` | Post messages, ephemeral answers |
| `commands` | Handle slash commands |

### 0c. Slash Commands

**Slash Commands** → Create New Command for each:

| Command | Description | Notes |
|---------|-------------|-------|
| `/post-trivia` | Post today's question | Manual trigger |
| `/stats` | Show your trivia stats | Personal accuracy, breakdowns |
| `/leaderboard` | Top 3 + filters | Interactive dropdowns |
| `/set-trivia-channel` | Set channel for daily posts | One per workspace |
| `/set-trivia-time` | Set daily post time (ET) | Format: `HH:MM` (e.g. `12:00`) |

### 0d. Interactivity

**Interactivity & Shortcuts** → toggle **ON**.

Even with Socket Mode, interactivity must be enabled for buttons to work.
No Request URL needed — Socket Mode handles payload delivery.

### 0e. Event Subscriptions

**Event Subscriptions** → toggle **ON** → subscribe to `app_mention`.

Event delivery is handled by Socket Mode — no Request URL needed here either.

### 0f. Install & Get Tokens

1. **Install to Workspace** → allow all scopes
2. Copy the **Bot User OAuth Token** (`xoxb-...`)
3. Copy the **Signing Secret** (from Basic Information page)
4. You now have all three tokens:
   - `SLACK_BOT_TOKEN` = `xoxb-...` (from step 0f.2)
   - `SLACK_APP_TOKEN` = `xapp-...` (from step 0a.5)
   - `SLACK_SIGNING_SECRET` = `...` (from step 0f.3)

### 0g. Invite Bot to Channels

In any channel you want trivia to run in:

```
/invite @Daily Trivia
```

The bot must be a channel member to post daily questions. Without this, `/post-trivia` and the scheduler will fail with `not_in_channel`.

---

## Phase 1: Initial Setup

### 1a. New Files

Create these in the project root:

**`Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

ENV TRIVIA_DB_DIR=/data

CMD ["python", "src/app.py"]
```

**`fly.toml`**

```toml
app = "slack-daily-trivia"
primary_region = "iad"

[build]
  dockerfile = "Dockerfile"

[mounts]
  source = "trivia_data"
  destination = "/data"

[[vm]]
  memory = "256mb"
  cpu_kind = "shared"
  cpus = 1

[experimental]
  auto_rollback = true
```

**`.dockerignore`**

```
.env
.git
__pycache__
*.pyc
.venv
venv
*.db
src/data/
```

### 1b. First Deploy

```bash
# Create app
fly apps create slack-daily-trivia

# Create persistent volume (1 GB)
fly volumes create trivia_data --region iad --size 1

# Set secrets — never commit .env to git or Docker image
# Set secrets — tokens from Phase 0 (0f)
fly secrets set SLACK_BOT_TOKEN=xoxb-...
fly secrets set SLACK_APP_TOKEN=xapp-...
fly secrets set SLACK_SIGNING_SECRET=...

# Deploy
fly deploy

# Verify
fly logs
fly status
```

After deploy, the app connects to Slack via Socket Mode (no public URL needed).

### 1c. Configure in Slack

Once deployed, set up the app from within Slack:

```bash
# Step 1: Invite bot to the trivia channel
/invite @Daily Trivia
```

```
# Step 2: Set which channel gets daily posts
/set-trivia-channel
```
Shows: *"Daily trivia set to post in #general. (Add me to this channel with `/invite @YourBotName`)"*

```
# Step 3: Set daily post time (ET, 24-hour format)
/set-trivia-time 12:00
```
Shows: *"Daily trivia post time set to 12:00 ET"*

```
# Step 4: Test immediate post (doesn't wait for schedule)
/post-trivia
```
Posts a public question with an **Answer** button. Click it → ephemeral choices appear. Pick one → see if you got it right (private to you).

```
# Step 5: Verify commands work
/stats           # your accuracy, breakdowns
/leaderboard     # top 3, filterable by category/difficulty
```

**What happens next:**
- Daily trivia posts automatically Mon-Fri at the configured time
- Weekly leaderboard posts every Friday at noon Eastern
- All answers are private — no one sees each other's picks
- Stats accumulate per user over time
- Scheduler re-syncs every 5 minutes — config changes picked up automatically

**If the bot doesn't post:** check `fly logs` for `not_in_channel` — the bot may not be a member of the channel. Run `/invite @Daily Trivia` in that channel.

### 1d. Auto-Deploy from GitHub (Optional)

Once set up, pushing to `main` auto-deploys — no manual `fly deploy` needed.

**One-time setup:**

```bash
# Create a deploy token (save the output — only shown once)
fly tokens create deploy
```

1. Copy the token value (starts with `fm1...`)
2. Go to GitHub repo → **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `FLY_API_TOKEN` → Value: paste the token → **Add secret**

The workflow file (`.github/workflows/deploy.yml`) is already in the repo.
Now every push to `main` builds and deploys on Fly.io automatically.

**What happens on push:**
- GitHub Actions checks out code
- Fly.io builds the Docker image on their infra (no Docker in Actions needed)
- Deploys with zero downtime — volume preserved, secrets unchanged

**To skip a deploy:** add `[skip ci]` anywhere in the commit message.

---

## Phase 2: Redeploy (Bug Fixes / New Features)

```bash
# If auto-deploy is set up (1d), just push to main. Otherwise:
fly deploy
```

### How Database Survives Redeploys

| What happens | Detail |
|-------------|--------|
| Fly builds new Docker image | Source code changes ship |
| Creates new Machine | Fresh VM with new image |
| Attaches **same** volume | `trivia_data` mounts at `/data` |
| Health checks new Machine | Confirms app is running |
| Stops old Machine | `SIGTERM` → signal handler runs → `stats_store.close()` clean exit |
| All data present | SQLite file intact at `/data/trivia_stats.db` |

**Zero data migration.** Same schema, same file, same path.

### What Breaks Persistence

| Action | Safe? |
|--------|-------|
| Code changes + `fly deploy` | ✅ |
| New deps in `requirements.txt` + `fly deploy` | ✅ |
| Change VM size in `fly.toml` + `fly deploy` | ✅ |
| Add Slack scopes → restart app | ✅ `fly apps restart` |
| `fly volumes delete trivia_data` | ❌ Destroys all data |
| Change `destination` in `fly.toml` mount | ❌ DB at old path unreachable |
| Delete app without backing up volume | ❌ |

### Rollback

```bash
# If the new image is broken, Fly auto-rolls back (auto_rollback = true).
# To manually deploy a previous image:
fly deploy --image registry.fly.io/slack-daily-trivia:deployment-<id>
```

---

## Phase 3: Database Backup & Recovery

### Automatic Snapshots (Free)

Fly Volumes include automatic daily snapshots with 5-day retention, enabled by default.

- **First 10 GB of snapshot storage is free**
- No setup required
- Snapshots are incremental — only changed blocks stored

### Manual Backup

```bash
# Option A: SSH into machine and copy
fly ssh console
cp /data/trivia_stats.db /data/trivia_stats_backup_$(date +%Y%m%d).db

# Option B: Download to local machine
fly ssh console -C "cat /data/trivia_stats.db" > ~/backups/trivia_stats_$(date +%Y%m%d).db
```

### In-App Scheduled Backup

Can add a lightweight background thread in `app.py`:

```python
import shutil, threading, time
from datetime import datetime

def backup_loop():
    while True:
        time.sleep(86400)  # every 24 hours
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        src = "/data/trivia_stats.db"
        dst = f"/data/backup_{ts}.db"
        shutil.copy2(src, dst)

threading.Thread(target=backup_loop, daemon=True).start()
```

### Disaster Recovery

| Scenario | Recovery |
|----------|----------|
| Corrupt database | Restore latest Fly Volume snapshot via dashboard |
| Accidental volume delete | Restore snapshot → creates new volume with same name |
| Full app destroyed | `git clone` repo → `fly deploy` → restore volume snapshot → re-set secrets |
| Fly region outage | App auto-restarts when infra recovers. Volume data is unaffected |

### Restore from Snapshot

```bash
# List snapshots for the volume
fly volumes snapshots list trivia_data

# Restore a snapshot to a new volume
fly volumes create trivia_data_restored \
  --region iad \
  --size 1 \
  --snapshot-id <snapshot-id>

# Update fly.toml mount to point to restored volume
# Then deploy
```

---

## Phase 4: Monitoring

```bash
fly logs               # tail live logs
fly status             # app health + machine state
fly dashboard          # web UI with usage metrics
```

For production, reduce log noise by changing `logging.basicConfig(level=logging.WARNING)` in `app.py`. Slack Bolt is verbose at INFO.

---

## Phase 5: Cost

| Resource | Spec | Monthly |
|----------|------|---------|
| Compute | shared-cpu-1x, 256 MB RAM | $2.02 |
| Persistent volume | 1 GB | $0.15 |
| Bandwidth | Trivia = tiny text messages | ~$0 |
| **Total** | | **~$2.17/mo** |

For 20 users playing one daily question each:
- ~600 answers/month = a few KB
- Database reaches 1 MB after years
- Volume will never fill up

---

## Cheat Sheet

```bash
fly deploy                           # ship it
fly logs                             # tail logs
fly secrets list                     # show secret names (not values)
fly secrets set KEY=VALUE            # add/update a secret
fly secrets unset KEY                # remove a secret
fly volumes list                     # list volumes
fly volumes snapshots list <name>    # list snapshots
fly status                           # app health
fly status --all                     # all machines
fly ssh console                      # shell into running VM
fly apps restart slack-daily-trivia # bounce the app
fly apps list                        # list all apps
fly orgs list                        # list orgs
```

---

## Edge Cases

- **Two people deploy simultaneously**: Fly rejects concurrent deploys. Second person retries.
- **Deploy fails mid-way**: `auto_rollback = true` reverts to last known good image.
- **Volume fills up**: Won't happen. 1 GB holds ~10 million trivia answers.
- **WAL mode**: Not used (removed for DB Browser compatibility). No stale WAL files.
- **App crashes**: Fly auto-restarts it. Socket Mode reconnects automatically.
