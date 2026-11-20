"""Telegram bot: admin commands to trigger the pipeline + a daily auto-post schedule.

Commands
  /start, /help            what the bot does
  /exams                   list target exams
  /types                   list content formats
  /preview [exam] [type]   admin: generate and send everything to *this* chat only (no YouTube/IG)
  /generate [exam] [type]  admin: generate and publish to the channel + YouTube + Instagram
  /quiz [exam]             anyone: replay a random quiz question from past content (no AI call)
  /history                 admin: last runs
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import random
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import pipeline
from .config import load_exams, settings
from .models import CONTENT_TYPES, ContentPackage
from .store import History

log = logging.getLogger(__name__)
_run_lock = asyncio.Lock()


def _is_admin(update: Update) -> bool:
    if not settings.telegram_admin_ids:
        return False
    return str(update.effective_user.id) in settings.telegram_admin_ids


def _parse_args(args: list[str]) -> tuple[str | None, str | None]:
    exams = load_exams()
    exam = next((a for a in args if a in exams), None)
    ctype = next((a for a in args if a in CONTENT_TYPES), None)
    return exam, ctype


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"🙏 Namaste! I'm the {settings.brand_name} bot.\n\n"
        "Every day I research a high-yield topic for exams like Rajasthan SI, JEE Main, NEET and board exams, "
        "then post quizzes, PDF notes and short videos.\n\n"
        "/exams – target exams\n/quiz <exam> – practice question\n/types – content formats\n\n"
        "Admins: /preview, /generate, /history"
    )


async def exams_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"<code>{e.id}</code> – {e.name} ({e.language})" for e in load_exams().values()]
    await update.message.reply_text("🎯 <b>Target exams</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def types_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"<code>{k}</code> – {v}" for k, v in CONTENT_TYPES.items()]
    await update.message.reply_text("🧩 <b>Formats</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def _run_and_report(chat_id: int | str, bot, exam: str | None, ctype: str | None, platforms: set[str], target_chat: str | None) -> None:
    if _run_lock.locked():
        await bot.send_message(chat_id, "⏳ A run is already in progress, please wait.")
        return
    async with _run_lock:
        await bot.send_message(chat_id, f"🔎 Researching {exam or 'a random exam'} ({ctype or 'random format'})… this takes a few minutes.")
        try:
            result = await asyncio.to_thread(pipeline.run, exam_id=exam, content_type=ctype, platforms=platforms, chat_id=target_chat)
        except Exception as e:  # bad args etc.
            await bot.send_message(chat_id, f"❌ {e}")
            return
        await bot.send_message(chat_id, ("✅ " if result.ok else "⚠️ ") + result.summary()[:4000])


async def preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    exam, ctype = _parse_args(ctx.args)
    chat = update.effective_chat.id
    ctx.application.create_task(_run_and_report(chat, ctx.bot, exam, ctype, {"telegram"}, str(chat)))


async def generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    exam, ctype = _parse_args(ctx.args)
    chat = update.effective_chat.id
    ctx.application.create_task(_run_and_report(chat, ctx.bot, exam, ctype, set(pipeline.PLATFORMS), None))


async def quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    exam, _ = _parse_args(ctx.args)
    pattern = f"*_{exam}_*/content.json" if exam else "*/content.json"
    files = sorted(settings.output_dir.glob(pattern))
    questions = []
    for f in files[-50:]:
        try:
            pkg = ContentPackage.model_validate_json(f.read_text(encoding="utf-8"))
            questions += [(pkg, q) for q in pkg.mcqs]
        except Exception:
            continue
    if not questions:
        await update.message.reply_text("No questions yet for that exam – check back after the next post!")
        return
    pkg, q = random.choice(questions)
    await ctx.bot.send_poll(
        update.effective_chat.id, q.question[:300], [o[:100] for o in q.options],
        type="quiz", correct_option_id=q.correct_index, explanation=q.explanation[:200], is_anonymous=False,
    )


async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    rows = History(settings.db_path).last_runs(10)
    text = "\n".join(f"{r[0][:16]} · {r[1]} · {r[3]} · {r[4]}\n   {r[2]}" for r in rows) or "No runs yet."
    await update.message.reply_text(text)


async def scheduled_post(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    report_to = settings.telegram_admin_ids[0] if settings.telegram_admin_ids else settings.telegram_channel_id
    await _run_and_report(report_to, ctx.bot, None, None, set(pipeline.PLATFORMS), None)


def main() -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("exams", exams_cmd))
    app.add_handler(CommandHandler("types", types_cmd))
    app.add_handler(CommandHandler("preview", preview))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("history", history_cmd))

    tz = ZoneInfo(settings.post_timezone)
    for t in settings.post_times:
        hh, mm = (int(x) for x in t.split(":"))
        app.job_queue.run_daily(scheduled_post, time=dt.time(hh, mm, tzinfo=tz), name=f"post-{t}")
        log.info("scheduled daily post at %s %s", t, settings.post_timezone)

    log.info("bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
