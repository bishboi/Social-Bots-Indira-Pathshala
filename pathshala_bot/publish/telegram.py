"""Telegram Bot API publisher (plain HTTPS, so the pipeline also runs outside the bot process)."""

from __future__ import annotations

import html
import logging
import re
import time
from pathlib import Path

import httpx

from ..config import Exam, settings
from ..models import ContentPackage

log = logging.getLogger(__name__)

def safe_html(text: str) -> str:
    """Escape everything, then restore the handful of tags we allow the model to use."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"&lt;(/?)(b|i|u|s|code|pre)&gt;", r"<\1\2>", escaped, flags=re.I)


class Telegram:
    def __init__(self, token: str | None = None):
        self.token = token or settings.telegram_bot_token
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.http = httpx.Client(timeout=httpx.Timeout(30, write=300, read=300))

    def call(self, method: str, data: dict | None = None, files: dict | None = None, json: dict | None = None) -> dict:
        for attempt in range(4):
            r = self.http.post(f"{self.base}/{method}", data=data, files=files, json=json)
            body = r.json()
            if body.get("ok"):
                return body["result"]
            retry = body.get("parameters", {}).get("retry_after")
            if r.status_code == 429 and retry and attempt < 3:
                time.sleep(int(retry) + 1)
                continue
            raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
        raise RuntimeError(f"Telegram {method} failed after retries")

    def send_message(self, chat_id: str, text: str) -> dict:
        return self.call("sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}})

    def send_photo(self, chat_id: str, photo: Path, caption: str) -> dict:
        with photo.open("rb") as f:
            return self.call("sendPhoto", data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}, files={"photo": f})

    def send_document(self, chat_id: str, doc: Path, caption: str) -> dict:
        with doc.open("rb") as f:
            return self.call("sendDocument", data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}, files={"document": (doc.name, f, "application/pdf")})

    def send_video(self, chat_id: str, video: Path, caption: str) -> dict:
        with video.open("rb") as f:
            return self.call(
                "sendVideo",
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML", "supports_streaming": "true", "width": "1080", "height": "1920"},
                files={"video": (video.name, f, "video/mp4")},
            )

    def send_quiz(self, chat_id: str, question: str, options: list[str], correct: int, explanation: str) -> dict:
        return self.call(
            "sendPoll",
            json={
                "chat_id": chat_id,
                "question": question[:300],
                "options": [{"text": o[:100]} for o in options],
                "type": "quiz",
                "correct_option_id": correct,
                "explanation": explanation[:200],
                "is_anonymous": True,
            },
        )


def publish(pkg: ContentPackage, exam: Exam, assets: dict[str, Path], chat_id: str | None = None) -> dict:
    tg = Telegram()
    chat = chat_id or exam.telegram_chat or settings.telegram_channel_id
    if not chat:
        raise RuntimeError("No Telegram chat: set TELEGRAM_CHANNEL_ID or exams.yaml telegram_chat")
    tags = " ".join(f"#{t}" for t in pkg.short.hashtags[:5])
    result: dict = {"chat_id": chat, "messages": []}

    caption = f"<b>{safe_html(pkg.hook)}</b>\n\n{safe_html(pkg.telegram_post)}\n\n{tags}"
    if "card" in assets and len(caption) <= 1024:
        result["messages"].append(tg.send_photo(chat, assets["card"], caption)["message_id"])
    else:
        if "card" in assets:
            result["messages"].append(tg.send_photo(chat, assets["card"], f"<b>{safe_html(pkg.title)}</b>")["message_id"])
        result["messages"].append(tg.send_message(chat, caption[:4096])["message_id"])

    m = pkg.mnemonic
    if m is not None and m.decode:
        head = "🧠 <b>याद रखने की ट्रिक</b>" if exam.language == "hi" else "🧠 <b>Memory trick</b>"
        lines = "\n".join(f"▫️ <b>{safe_html(c.cue)}</b> → {safe_html(c.stands_for)}" for c in m.decode)
        tip = f"\n\n💡 {safe_html(m.tip)}" if m.tip else ""
        result["messages"].append(tg.send_message(chat, f"{head}\n\n<b>“{safe_html(m.trick)}”</b>\n\n{lines}{tip}"[:4096])["message_id"])

    for q in pkg.mcqs:
        result["messages"].append(tg.send_quiz(chat, q.question, q.options, q.correct_index, q.explanation)["message_id"])
        time.sleep(1.2)

    if "pdf" in assets:
        cap = f"📄 <b>{safe_html(pkg.title)}</b>\n{safe_html(pkg.subject)}\nFree PDF notes by {safe_html(settings.brand_name)}"
        result["messages"].append(tg.send_document(chat, assets["pdf"], cap)["message_id"])

    if "video" in assets and settings.telegram_send_video:
        cap = f"🎬 <b>{safe_html(pkg.short.title)}</b>"
        result["messages"].append(tg.send_video(chat, assets["video"], cap)["message_id"])
    return result
