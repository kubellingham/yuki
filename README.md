# yuki

a private telegram bot that's a long-term life companion — a character named yuki who's on the same weight-loss journey as me, learns japanese alongside my daily habit, and cares about school. she has her own simulated life, remembers things, and messages with realistic delays.

single-user by design. not a product.

**status: step 1 of N — foundation only.** database schema, telegram handlers, access control, onboarding. no simulation engine, no proactive messages, no LLM wiring yet. those come in later steps.

## prerequisites

- python 3.11+
- a telegram account
- (later steps only) an openrouter api key

## setup

### 1. create the bot with @BotFather

on telegram:

1. message [@BotFather](https://t.me/BotFather)
2. send `/newbot`
3. give it a display name (shown in the chat header)
4. give it a username — must end in `bot` (e.g. `my_yuki_bot`)
5. copy the api token BotFather sends back — that's `TELEGRAM_BOT_TOKEN`

### 2. get your telegram user id

message [@userinfobot](https://t.me/userinfobot) on telegram. it replies with your numeric id (something like `123456789`). that's `MY_TELEGRAM_ID`. the bot refuses to talk to anyone else.

### 3. (optional, for later steps) get an openrouter api key

sign up at [openrouter.ai](https://openrouter.ai/), create an api key, drop it in `.env` as `OPENROUTER_API_KEY`. not needed for step 1.

### 4. install and run

```bash
python -m venv .venv
source .venv/bin/activate           # windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# open .env and fill in TELEGRAM_BOT_TOKEN and MY_TELEGRAM_ID

python main.py
```

on first run the bot creates `buddy.db` (sqlite) in the working directory.

## commands (step 1)

- `/start` — onboarding: asks name, current weight, target weight, deadline. creates user + buddy state. yuki starts at the same weight, target, deadline as you.
- `/status` — dev dump of your user row and yuki's buddy_state row.
- `/reset` — wipes ALL your data. asks for `YES` before doing it.
- `/setup_goals` — placeholder for step 2. currently replies "coming soon 🙌".
- `/cancel` — bails out of the middle of `/start` or `/reset`.
- anything else — logged to the `messages` table, canned reply.

## inspecting the db

```bash
sqlite3 buddy.db ".tables"
sqlite3 buddy.db ".schema"
sqlite3 buddy.db "select * from users;"
sqlite3 buddy.db "select * from buddy_state;"
sqlite3 buddy.db "select * from messages order by id desc limit 20;"
```

## project layout

- `main.py` — application setup + handler registration only
- `config.py` — env loading
- `db.py` — aiosqlite storage, raw sql, named helpers (`get_user`, `create_user`, `log_message`, `wipe_user`, …)
- `handlers.py` — telegram handlers, onboarding conversation, access control
- `.env.example` — env template
- `requirements.txt` — pinned deps
- `buddy.db` — created on first run (gitignored)

## what's next

- **step 2** — wire up `/setup_goals` for the three fixed goals (weight/shared, japanese/core, studies/core)
- **step 3** — yuki's simulated life engine (writes to `buddy_life`, evolves `buddy_state`)
- **step 4** — proactive messaging with realistic delays (APScheduler)
- **step 5** — LLM wiring via openrouter, yuki's full personality, memory ingestion
- **step 6** — callback memory + room-reading (yuki notices when you've gone quiet)
