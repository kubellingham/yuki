"""Buddy bot entry point. Wires up handlers and starts polling."""
from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import MY_TELEGRAM_ID, TELEGRAM_BOT_TOKEN
from db import init_db
from handlers import (
    ASK_DEADLINE,
    ASK_NAME,
    ASK_START_WEIGHT,
    ASK_TARGET_WEIGHT,
    CONFIRM_RESET,
    CONFIRM_SETUP,
    access_denied,
    cancel_conversation,
    reset_command,
    reset_confirm,
    setup_goals_command,
    start_ask_deadline,
    start_ask_name,
    start_ask_start_weight,
    start_ask_target_weight,
    start_command,
    start_confirm,
    status_command,
    text_handler,
)


async def _post_init(app: Application) -> None:
    await init_db()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    only_me = filters.User(user_id=MY_TELEGRAM_ID)
    text = filters.TEXT & ~filters.COMMAND

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start_command, filters=only_me)],
        states={
            ASK_NAME: [MessageHandler(only_me & text, start_ask_name)],
            ASK_START_WEIGHT: [MessageHandler(only_me & text, start_ask_start_weight)],
            ASK_TARGET_WEIGHT: [MessageHandler(only_me & text, start_ask_target_weight)],
            ASK_DEADLINE: [MessageHandler(only_me & text, start_ask_deadline)],
            CONFIRM_SETUP: [MessageHandler(only_me & text, start_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation, filters=only_me)],
        # in-memory state — a restart wipes any half-finished onboarding,
        # which is fine: the user just types /start again.
    )

    reset = ConversationHandler(
        entry_points=[CommandHandler("reset", reset_command, filters=only_me)],
        states={
            CONFIRM_RESET: [MessageHandler(only_me & text, reset_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation, filters=only_me)],
    )

    app.add_handler(onboarding)
    app.add_handler(reset)
    app.add_handler(CommandHandler("status", status_command, filters=only_me))
    app.add_handler(CommandHandler("setup_goals", setup_goals_command, filters=only_me))
    app.add_handler(MessageHandler(only_me & text, text_handler))
    # Everyone else gets a polite no.
    app.add_handler(MessageHandler(~only_me, access_denied))

    logging.getLogger(__name__).info(
        "starting bot; authorized telegram_id=%s", MY_TELEGRAM_ID
    )
    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
