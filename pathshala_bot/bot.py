"""Telegram bot: admin commands to trigger the pipeline + a daily auto-post schedule.

Commands
  /start, /help            what the bot does
  /exams                   list target exams
  /types                   list content formats
  /styles                  list video styles
  /spaces                  admin: list spaces
  /preview [space] [exam] [type] [style]   admin: run a space's agent, send everything to *this* chat only
  /generate [space] [exam] [type] [style]  admin: run a space's agent and post to the space's channels
  /quiz [exam]             anyone: replay a random quiz question from past content (no AI call)
  /history                 admin: last runs
  /akhbar [edition]        admin: build today's paper (e.g. rajasthan-hi, central-en) and send it here
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
from .models import CONTENT_TYPES, VIDEO_STYLES, ContentPackage
from .store import get_history

log = logging.getLogger(__name__)
_run_lock = asyncio.Lock()


def _is_admin(update: Update) -> bool:
    if not settings.telegram_admin_ids:
        return False
    return str(update.effective_user.id) in settings.telegram_admin_ids


def _parse_args(args: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """(space id, exam id, content type, video style) from command arguments, in any order."""
    from . import control

    exams = load_exams()
    try:
        cfg = control.load()
        space = next((s.id for a in args if (s := control.find_space(cfg, a))), None)
        exams = control.all_exam_defs(cfg)
    except Exception:
        space = None
    exam = next((a for a in args if a in exams), None)
    ctype = next((a for a in args if a in CONTENT_TYPES), None)
    style = next((a for a in args if a in VIDEO_STYLES), None)
    return space, exam, ctype, style


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"🙏 Namaste! I'm the {settings.brand_name} bot.\n\n"
        "Every day I research a high-yield topic for exams like Rajasthan SI, JEE Main, NEET and board exams, "
        "then post quizzes, PDF notes and short videos.\n\n"
        "/exams – target exams\n/quiz <exam> – practice question\n/types – content formats\n/styles – video styles\n\n"
        "Admins: /spaces, /preview, /generate, /history"
    )


async def exams_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"<code>{e.id}</code> – {e.name} ({e.language})" for e in load_exams().values()]
    await update.message.reply_text("🎯 <b>Target exams</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def spaces_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    from . import control

    cfg = control.load()
    lines = [f"{'🟢' if s.enabled else '⚪️'} <code>{s.id}</code> – {s.name}: {', '.join(s.exam_ids)} → "
             f"{len(control.space_channels(cfg, s))} channel(s)" for s in cfg.spaces]
    await update.message.reply_text("🧭 <b>Spaces</b>\n" + ("\n".join(lines) or "No spaces yet – add one in the admin panel.")
                                    + "\n\n/generate &lt;space&gt; – post now · /preview &lt;space&gt; – send here only", parse_mode=ParseMode.HTML)


async def types_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"<code>{k}</code> – {v}" for k, v in CONTENT_TYPES.items()]
    await update.message.reply_text("🧩 <b>Formats</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def _run_and_report(chat_id: int | str, bot, args: list[str], preview_chat: str | None) -> None:
    """Run a space's agent. preview_chat: send everything to that chat only; None: post to the space's channels."""
    space_id, exam, ctype, style = _parse_args(args)
    if _run_lock.locked():
        await bot.send_message(chat_id, "⏳ A run started from Telegram is already in progress, please wait.")
        return
    async with _run_lock:
        try:
            from . import agent, control

            cfg = control.load()
            control.apply(cfg)
            spaces = [s for s in cfg.spaces if s.enabled] or cfg.spaces
            if space_id:
                space = cfg.space(space_id)
            elif exam:
                space = next((s for s in spaces if exam in s.exam_ids), None)
                if space is None and preview_chat is None:
                    raise KeyError(f"No space contains the exam '{exam}'. Add it to a space in the admin panel.")
            else:
                space = spaces[0] if len(spaces) == 1 else None
                if space is None:
                    raise KeyError("Say which space: " + ", ".join(s.id for s in cfg.spaces) if cfg.spaces else
                                   "No spaces yet – add one in the admin panel.")
            await bot.send_message(chat_id, f"🔎 {space.name if space else exam}: choosing a fresh topic and researching… this takes a few minutes.")
            channels = [control.Channel(name="preview", platform="telegram", chat_id=preview_chat)] if preview_chat else None
            if space is None:  # preview of an exam that is in no space
                result = await asyncio.to_thread(pipeline.run, exam_id=exam, content_type=ctype, video_style=style,
                                                 exams=control.all_exam_defs(cfg), channels=channels)
            else:
                result = await asyncio.to_thread(agent.run_space, cfg, space, channels=channels, exam_id=exam,
                                                 content_type=ctype, video_style=style)
        except Exception as e:  # bad args etc.
            await bot.send_message(chat_id, f"❌ {e}")
            return
        await bot.send_message(chat_id, ("✅ " if result.ok else "⚠️ ") + result.summary()[:4000])


async def preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    chat = update.effective_chat.id
    ctx.application.create_task(_run_and_report(chat, ctx.bot, list(ctx.args), str(chat)))


async def generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    chat = update.effective_chat.id
    ctx.application.create_task(_run_and_report(chat, ctx.bot, list(ctx.args), None))


async def quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _, exam, _, _ = _parse_args(ctx.args)
    questions = []
    try:
        stored = await asyncio.to_thread(get_history().recent_contents, exam, 50)
    except Exception:
        log.exception("could not read past content")
        stored = []
    for data in stored:
        try:
            pkg = ContentPackage.model_validate(data)
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


async def styles_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [f"<code>{k}</code> – {v}" for k, v in VIDEO_STYLES.items()]
    await update.message.reply_text("🎬 <b>Video styles</b> (add one to /preview or /generate)\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    rows = get_history().last_runs(10)
    text = "\n".join(f"{r[0][:16]} · {r[1]} · {r[3]} · {r[4]}\n   {r[2]}" for r in rows) or "No runs yet."
    await update.message.reply_text(text)


async def scheduled_post(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    report_to = settings.telegram_admin_ids[0] if settings.telegram_admin_ids else settings.telegram_channel_id
    from . import control

    for sp in control.load().spaces:  # bot-only mode: every enabled space posts once per POST_TIMES slot
        if sp.enabled:
            await _run_and_report(report_to, ctx.bot, [sp.id], None)


async def akhbar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: build today's paper for an edition (e.g. /akhbar rajasthan-hi, /akhbar central-en) and send it here."""
    if not _is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    from . import control

    cfg = control.load()
    arg = " ".join(ctx.args).strip().lower()
    eds = [e for e in cfg.editions if e.enabled] or cfg.editions
    ed = next((e for e in cfg.editions if e.id == arg or e.region.lower() == arg), None) if arg else (eds[0] if eds else None)
    if ed is None:
        await update.message.reply_text("Editions: " + ", ".join(e.id for e in cfg.editions))
        return
    chat = str(update.effective_chat.id)
    preview_channel = control.Channel(name="preview", platform="telegram", chat_id=chat)
    if _run_lock.locked():
        await update.message.reply_text("⏳ A run is already in progress, please wait.")
        return
    desk = ed.desk()

    async def job():
        from . import akhbar

        async with _run_lock:
            await ctx.bot.send_message(chat, f"🗞 Searching today's news for the {desk.label()}… this takes a few minutes.")
            res = await asyncio.to_thread(akhbar.run, desk, [preview_channel], False)
            await ctx.bot.send_message(chat, ("✅ " if res.ok else "⚠️ ") + res.summary()[:4000])

    ctx.application.create_task(job())


def build_application(with_schedule: bool = False) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("exams", exams_cmd))
    app.add_handler(CommandHandler("spaces", spaces_cmd))
    app.add_handler(CommandHandler("types", types_cmd))
    app.add_handler(CommandHandler("styles", styles_cmd))
    app.add_handler(CommandHandler("preview", preview))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("akhbar", akhbar_cmd))

    if with_schedule:  # legacy: POST_TIMES from .env (the admin panel scheduler replaces this)
        tz = ZoneInfo(settings.post_timezone)
        for t in settings.post_times:
            hh, mm = (int(x) for x in t.split(":"))
            app.job_queue.run_daily(scheduled_post, time=dt.time(hh, mm, tzinfo=tz), name=f"post-{t}")
            log.info("scheduled daily post at %s %s", t, settings.post_timezone)
    return app


def main() -> None:
    """Bot only, with the legacy .env schedule. Prefer `python -m pathshala_bot serve`."""
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = build_application(with_schedule=True)
    log.info("bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
