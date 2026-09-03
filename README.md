# yuki

a private telegram bot that's a long-term life companion — a character named yuki who's on the same weight-loss journey as me, learns japanese alongside my daily habit, and cares about school. she has her own simulated life, remembers things, and messages with realistic delays.

single-user by design. not a product.

**status: step 2 of N — goals system.** foundation (schema, onboarding, access control) is in from step 1; step 2 adds `/setup_goals` for the three fixed goals (weight/shared, japanese/core with phase tracking, studies/core with field + subjects). still no simulation engine, no proactive messages, no LLM wiring yet — those come in later steps.

**deploy target: vercel + turso**, both free tiers, forever.

## how it works (at a glance)

- **Vercel serverless functions** host `api/index.py` — telegram POSTs each of your messages to `/api/webhook` (rewritten internally), we run a short handler, we ack.
- **Turso** (hosted sqlite over HTTPS) is the persistent database. Same sqlite schema as a local db, just accessed remotely.
- No polling process, no VM, no laptop — as long as vercel and turso are up, yuki is reachable.

## deploy (one time, ~10 min)

### 1. create the telegram bot

on telegram:

1. message [@BotFather](https://t.me/BotFather)
2. send `/newbot`, follow the prompts (name + username ending in `bot`)
3. copy the api token → this is `TELEGRAM_BOT_TOKEN`

### 2. get your telegram user id

message [@userinfobot](https://t.me/userinfobot) on telegram. it replies with your numeric id (e.g. `123456789`) → this is `MY_TELEGRAM_ID`. the bot refuses to talk to anyone else.

### 3. create a turso database

1. go to [turso.tech](https://turso.tech) → **Sign in with GitHub** (no card)
2. **Create Database** → name it `yuki` → pick a region near you
3. on the database page, click **Connect**:
   - copy the **Database URL** (`libsql://...`) → `TURSO_DATABASE_URL`
   - click **Generate Token** → copy it (shown once) → `TURSO_AUTH_TOKEN`

### 4. pick a webhook secret

any long random string. suggestion:

```bash
openssl rand -hex 32
```

that's your `WEBHOOK_SECRET`.

### 5. deploy to vercel

1. push this repo to github (or fork it)
2. on [vercel.com](https://vercel.com), **Add New… → Project → Import** the repo
3. in **Environment Variables**, add all five:
   - `TELEGRAM_BOT_TOKEN`
   - `MY_TELEGRAM_ID`
   - `TURSO_DATABASE_URL`
   - `TURSO_AUTH_TOKEN`
   - `WEBHOOK_SECRET`
   - (optional) `BUDDY_NAME` — defaults to `Yuki`
4. click **Deploy**. wait ~1 minute. you'll get a URL like `https://yuki-abc123.vercel.app`.

### 6. apply the schema (once)

hit this in your browser (replace both bits):

```
https://<your-vercel-url>/api/migrate?secret=<WEBHOOK_SECRET>
```

you should see `{"ok": true, "message": "schema applied"}`. safe to re-run — the CREATEs are `IF NOT EXISTS` and the column migrations are guarded by a `PRAGMA table_info` check (so re-runs just log "already present"). **run this again after pulling any new step** — steps that add columns rely on it.

### 7. register the webhook (once)

```
https://<your-vercel-url>/api/setup?secret=<WEBHOOK_SECRET>
```

you should see `{"ok": true, ..., "_target_url": "https://<your-vercel-url>/api/webhook"}` — telegram now knows where to send your messages.

sanity check anytime:

```
https://<your-vercel-url>/api/setup?secret=<WEBHOOK_SECRET>&action=info
```

### 8. talk to yuki

open your bot on telegram, send `/start`. that's it.

## commands

- `/start` — onboarding: asks name, current weight, target weight, deadline. creates your user row and yuki's matching buddy_state (she starts at the same weight, target, deadline).
- `/setup_goals` — walks you through the three fixed goals:
  1. **weight** (shared) — confirms the numbers from `/start`.
  2. **japanese** (core) — starts you at the `kana` phase (hiragana + katakana). yuki is a native speaker; the goal carries a swap ritual (you teach her english, she teaches you japanese) that later steps will lean on.
  3. **studies** (core) — asks your field, optionally your current-term subjects.
  re-running after completion shows the summary and offers to update only the studies field/subjects (the three goals themselves are a fixed set).
- `/status` — dev dump: your `users` row, yuki's `buddy_state`, and all your `goals` rows with their `data` JSON.
- `/reset` — wipes ALL your data (users, goals, buddy state, messages, memories, everything). asks for `YES` to confirm.
- `/cancel` — bails out of the middle of `/start`, `/reset`, or `/setup_goals`.
- anything else — logged to `messages`, canned reply.

## inspecting the db

open your turso database in the dashboard → **SQL** tab, run:

```sql
select * from users;
select * from buddy_state;
select id, title, goal_type, active, data from goals where user_id = <your id>;
select * from messages order by id desc limit 20;
select * from bot_state;  -- onboarding / setup_goals / reset flags
```

## project layout

```
api/
  index.py       # single vercel entrypoint — routes /api/webhook, /api/setup, /api/migrate
yuki/
  __init__.py
  config.py      # env loading, fail-fast on missing vars
  telegram.py    # thin wrapper over telegram bot api (sendMessage etc)
  db.py          # libsql-client + SCHEMA_STATEMENTS + MIGRATIONS + named helpers
  handlers.py    # per-command / per-step handler functions (onboarding + setup_goals)
  dispatch.py    # top-level router — reads bot_state, picks handler
vercel.json      # rewrites /api/{webhook,setup,migrate} → /api/index?_route=…
pyproject.toml   # [project] deps + [tool.vercel] entrypoint
.env.example
```

## what's next

- ~~**step 1** — foundation (schema + onboarding + access control)~~ ✅
- ~~**step 2** — `/setup_goals` for the three fixed goals (weight/shared, japanese/core, studies/core)~~ ✅
- **step 3** — yuki's simulated life engine (writes to `buddy_life`, evolves `buddy_state`)
- **step 4** — proactive messaging with realistic delays. vercel hobby cron is 1x/day; we'll route around that with a free external cron (cron-job.org) pinging `/api/tick` every 10-15 min.
- **step 5** — LLM wiring via openrouter, yuki's full personality, memory ingestion (facts / preferences / events / **callbacks** — advice you gave her that she uses on you later)
- **step 6** — reading the room: yuki notices when you've gone quiet or told her you're slammed, and eases off on her own
