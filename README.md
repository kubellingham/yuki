# yuki

a private telegram bot that's a long-term life companion — a character named yuki who's on the same weight-loss journey as me, learns japanese alongside my daily habit, and cares about school. she has her own simulated life, remembers things, and messages with realistic delays.

single-user by design. not a product.

**status: step 4 of N — proactive messaging.** foundation (step 1) + goals (step 2) + LLM chat (step 5a) + simulated life (step 3) + **yuki reaches out on her own** (this step). a second cron pings `/api/outreach` every ~30 min; per-user it runs a deterministic decision (quiet-hours mute, back off if either side messaged in the last 4h, respect `mood=off`, probability scales with how long you've been quiet, +20% boost if a fresh life event just landed) and only when it fires does it spend one LLM call to draft a short natural text. still no memory ingestion (step 5b), no full room-reading (step 6 — light preview already ships: quiet from you tilts her mood down, and mood=off backs her off entirely).

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
3. in **Environment Variables**, add all six:
   - `TELEGRAM_BOT_TOKEN`
   - `MY_TELEGRAM_ID`
   - `TURSO_DATABASE_URL`
   - `TURSO_AUTH_TOKEN`
   - `WEBHOOK_SECRET`
   - `OPENROUTER_API_KEY` — get one at [openrouter.ai](https://openrouter.ai/); yuki won't have a brain without it (chat will degrade to "brain's offline" until it's set)
   - (optional) `BUDDY_NAME` — defaults to `Yuki`
   - (optional) `MODEL_NAME` — defaults to `deepseek/deepseek-chat`. any OpenRouter model id works.
   - (optional) `USER_TIMEZONE` — defaults to `UTC`. sets the quiet-hours window (22:00–07:00 local) that gates proactive outreach. any zoneinfo name works, e.g. `Africa/Lagos`, `Europe/London`, `America/New_York`.
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

### 8. wire the simulated-life cron (once, for step 3)

yuki's mood, streak, weight, and life events evolve on a background tick. vercel hobby cron is only 1x/day, so we use a free external cron.

1. sign up at [cron-job.org](https://cron-job.org/) (GitHub login, no card)
2. **Create cronjob**:
   - **URL**: `https://yuki-wine.vercel.app/api/tick?secret=<WEBHOOK_SECRET>` (paste your actual secret)
   - **Schedule**: every 4 hours (or hourly if you want faster evolution; nothing breaks, it just moves quicker)
   - **Request method**: GET
   - **Notifications**: your call — I usually leave failure-notifications on
3. **Save**
4. Optionally, click **Execute now** once to trigger the first tick immediately and see it in `/status`

### 9. wire the outreach cron (once, for step 4)

separate cron so it can run more often than the life ticker.

1. still in [cron-job.org](https://cron-job.org/), **Create cronjob** again
2. **URL**: `https://yuki-wine.vercel.app/api/outreach?secret=<WEBHOOK_SECRET>`
3. **Schedule**: every 30 minutes (`*/30 * * * *`)
4. **Method**: GET
5. Save

each ping either sends nothing (most of the time) or fires one short unprompted message from yuki. she's silent during your quiet hours, silent for the first 4h after either of you spoke, and silent when her mood is `off`.

### 10. talk to yuki

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
- **anything else** — routed to yuki via OpenRouter with her personality prompt + the last 20 messages as context. logged to `messages` on both sides so future turns build on prior turns.

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
  telegram.py    # sendMessage + sendChatAction (typing indicator)
  db.py          # libsql-client + SCHEMA_STATEMENTS + MIGRATIONS + named helpers
  handlers.py    # per-command / per-step handler functions (onboarding + setup_goals + free-form-with-LLM)
  dispatch.py    # top-level router — reads bot_state, picks handler
  llm.py         # OpenRouter chat-completions client
  persona.py     # yuki's system prompt template + per-request assembly
  reply_format.py # shared LLM reply cleanup + burst splitting
  simulator.py   # tick logic — mood/streak/weight evolution + life event generation
  outreach.py    # proactive-messaging decision + drafting
vercel.json      # rewrites + 30s maxDuration for LLM calls
pyproject.toml   # [project] deps + [tool.vercel] entrypoint
.env.example
```

## what's next

- ~~**step 1** — foundation (schema + onboarding + access control)~~ ✅
- ~~**step 2** — `/setup_goals` for the three fixed goals (weight/shared, japanese/core, studies/core)~~ ✅
- ~~**step 5a** — LLM wired for free-form chat via OpenRouter, yuki's personality prompt~~ ✅
- ~~**step 3** — simulated life engine (mood/streak/weight evolve on a 4h cron, occasional LLM-generated buddy_life events, "she lies" mechanic now active)~~ ✅
- ~~**step 4** — proactive messaging (deterministic decision every 30 min: quiet hours, cooldown, mood check, quiet-scaled probability + fresh-event boost; LLM drafts only when it fires)~~ ✅
- **step 5b** — memory ingestion (facts / preferences / events / **callbacks** — advice you gave her that she uses on you later). LLM-as-classifier over recent messages.
- **step 6** — full room-reading: yuki notices when you've gone quiet or told her you're slammed, and eases off on her own (light preview of this already ships in steps 3 + 4 — quiet from you tilts her mood down, and mood=off mutes outreach)
