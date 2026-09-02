# yuki

a private telegram bot that's a long-term life companion — a character named yuki who's on the same weight-loss journey as me, learns japanese alongside my daily habit, and cares about school. she has her own simulated life, remembers things, and messages with realistic delays.

single-user by design. not a product.

**status: step 1 of N — foundation only.** database schema, telegram handlers, access control, onboarding. no simulation engine, no proactive messages, no LLM wiring yet.

**deploy target: vercel + turso**, both free tiers, forever.

## how it works (at a glance)

- **Vercel serverless functions** host `api/webhook.py` — telegram POSTs each of your messages to that URL, we run a short handler, we ack.
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

you should see `{"ok": true, "message": "schema applied"}`. safe to re-run — everything is `CREATE TABLE IF NOT EXISTS`.

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

## commands (step 1)

- `/start` — onboarding: asks name, current weight, target weight, deadline. creates your user row and yuki's matching buddy_state (she starts at the same weight, target, deadline).
- `/status` — dev dump of your `users` row and yuki's `buddy_state` row.
- `/reset` — wipes ALL your data. asks for `YES` to confirm.
- `/setup_goals` — placeholder for step 2. currently replies "coming soon 🙌".
- `/cancel` — bails out of the middle of `/start` or `/reset`.
- anything else — logged to `messages`, canned reply.

## inspecting the db

open your turso database in the dashboard → **SQL** tab, run:

```sql
select * from users;
select * from buddy_state;
select * from messages order by id desc limit 20;
select * from bot_state;  -- onboarding / reset flags
```

## project layout

```
api/
  webhook.py     # vercel handler — receives telegram updates
  setup.py       # ?action=set|info|delete — registers webhook
  migrate.py     # applies the schema (idempotent)
yuki/
  __init__.py
  config.py      # env loading, fail-fast on missing vars
  telegram.py    # thin wrapper over telegram bot api (sendMessage etc)
  db.py          # libsql-client wrapper + named helpers + schema
  handlers.py    # per-command / per-step handler functions
  dispatch.py    # top-level router — reads bot_state, picks handler
vercel.json
requirements.txt
.env.example
```

## what's next

- **step 2** — `/setup_goals` for the three fixed goals (weight/shared, japanese/core, studies/core)
- **step 3** — yuki's simulated life engine (writes to `buddy_life`, evolves `buddy_state`)
- **step 4** — proactive messaging with realistic delays. vercel hobby cron is 1x/day; we'll route around that with a free external cron (cron-job.org) pinging `/api/tick` every 10-15 min.
- **step 5** — LLM wiring via openrouter, yuki's full personality, memory ingestion (facts / preferences / events / **callbacks** — advice you gave her that she uses on you later)
- **step 6** — reading the room: yuki notices when you've gone quiet or told her you're slammed, and eases off on her own
