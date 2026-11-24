"""Public pages students use: the series page (the funnel) and the online test.

    GET  /t/<series>                    every released test, stage by stage
    GET  /t/<series>/<test>             the test player (timer, sections, palette, review)
    POST /t/<series>/<test>/start       {name, phone, email} -> questions WITHOUT answers + a signed token
    POST /t/<series>/<test>/submit      {token, answers, seconds} -> score, rank, solutions
    GET  /t/<series>/<test>/<paper|solutions>.pdf   free tests only

Answers never reach the browser before submitting. Sign-up tests need a name and a phone
number (stored as a lead); private tests are not served at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import control
from ..config import PACKAGE_DIR, settings
from . import builder
from .models import KINDS, Test

log = logging.getLogger(__name__)
_env = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=select_autoescape(["html"]))
TOKEN_HOURS = 12
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-eval'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# ---------------------------------------------------------------- helpers

def _key() -> bytes:
    from ..web import auth

    return hashlib.sha256(f"pathshala-tests|{os.getenv('SESSION_SECRET', '')}|{auth.admin_password()}".encode()).digest()


def make_token(series_id: str, test_id: str, lead_id: int, name: str) -> str:
    payload = json.dumps({"s": series_id, "t": test_id, "l": lead_id, "n": name[:60], "e": int(time.time()) + TOKEN_HOURS * 3600},
                         separators=(",", ":")).encode().hex()
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{payload}.{sig}"


def read_token(token: str, series_id: str, test_id: str) -> dict | None:
    try:
        payload, sig = (token or "").rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]):
            return None
        data = json.loads(bytes.fromhex(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("s") != series_id or data.get("t") != test_id or data.get("e", 0) < time.time():
        return None
    return data


class Limiter:
    """At most `n` requests per `window` seconds per client address."""

    def __init__(self, n: int, window: int):
        self.n, self.window = n, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, addr: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits[addr]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            if len(self.hits) > 20000:  # forget idle addresses
                for k in [k for k, v in self.hits.items() if not v][:10000]:
                    self.hits.pop(k, None)
            return True


starts = Limiter(30, 600)
submits = Limiter(30, 600)


def _addr(request: Request) -> str:
    return request.client.host if request.client else "?"


def _visible(series_id: str, test_id: str | None = None) -> tuple[control.ControlConfig, control.TestSeries, Test | None]:
    cfg = control.load()
    series = cfg.series(series_id)
    if series is None or not series.enabled:
        raise HTTPException(404, "Not found")
    if test_id is None:
        return cfg, series, None
    test = builder.load_test(series_id, test_id)
    if test is None or test.access == "private":
        raise HTTPException(404, "Not found")
    return cfg, series, test


def clean_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    digits = digits.lstrip("0") if len(digits) == 11 else digits
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def score(test: Test, answers: dict) -> dict:
    """Marks with each section's marking scheme; per-question review with the key and solution."""
    total = max_score = 0.0
    sections, review = [], []
    right = wrong = skipped = 0
    n = 0
    qs = test.by_id()
    for sec in test.sections:
        s = {"subject": sec.subject, "questions": 0, "correct": 0, "wrong": 0, "skipped": 0, "score": 0.0, "max": 0.0}
        for qid in sec.question_ids:
            q = qs.get(qid)
            if q is None:
                continue
            n += 1
            s["questions"] += 1
            s["max"] += sec.marks_per_question
            a = answers.get(qid)
            chosen = a if isinstance(a, int) and 0 <= a < len(q.options) else None
            if chosen is None:
                s["skipped"] += 1
                status = "skipped"
            elif chosen == q.correct_index:
                s["correct"] += 1
                s["score"] += sec.marks_per_question
                status = "correct"
            else:
                s["wrong"] += 1
                s["score"] -= sec.negative_marks
                status = "wrong"
            review.append({"n": n, "id": qid, "chosen": chosen, "correct": q.correct_index, "status": status,
                           "solution": q.solution, "explanation": q.explanation, "hint": q.hint, "source_url": q.source_url})
        s["score"] = round(s["score"], 2)
        total += s["score"]
        max_score += s["max"]
        right, wrong, skipped = right + s["correct"], wrong + s["wrong"], skipped + s["skipped"]
        sections.append(s)
    attempted = right + wrong
    return {"score": round(total, 2), "max_score": round(max_score, 2), "correct": right, "wrong": wrong, "skipped": skipped,
            "accuracy": round(100 * right / attempted, 1) if attempted else 0.0, "sections": sections, "review": review}


def public_questions(test: Test) -> list[dict]:
    out, n = [], 0
    for sec, q in test.ordered():
        n += 1
        out.append({"n": n, "id": q.id, "section": sec.subject, "question": q.question, "options": q.options,
                    "svg": ("data:image/svg+xml;base64," + __import__("base64").b64encode(q.svg.encode()).decode()) if q.svg else "",
                    "ref": q.pyq_reference, "hint": q.hint})
    return out


def _page(html: str) -> HTMLResponse:
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                       "Referrer-Policy": "same-origin"})


def _listing(series: control.TestSeries) -> list[dict]:
    tests = sorted((t for t in builder.built(series.id).values() if t.access != "private"), key=lambda t: t.order)
    out = []
    for kind, label in KINDS.items():
        items = [{"id": t.id, "title": t.title, "questions": len(t.questions), "minutes": t.duration_minutes,
                  "marks": t.total_marks, "access": t.access,
                  "pdf": t.access == "free" and (builder.test_dir(series.id, t.id) / "paper.pdf").exists()}
                 for t in tests if t.kind == kind]
        if items:
            out.append({"kind": kind, "label": label, "tests": items})
    return out


# ---------------------------------------------------------------- routes

def router() -> APIRouter:
    r = APIRouter()

    @r.get("/t/{series_id}", response_class=HTMLResponse)
    def series_page(series_id: str):
        cfg, series, _ = _visible(series_id)
        exam = builder.exam_for(cfg, series)
        return _page(_env.get_template("test_home.html").render(
            series=series, exam=exam, stages=_listing(series), brand=settings.brand_name, handle=settings.brand_handle))

    @r.get("/t/{series_id}/{test_id}", response_class=HTMLResponse)
    def test_page(series_id: str, test_id: str):
        cfg, series, test = _visible(series_id, test_id)
        exam = builder.exam_for(cfg, series)
        bp = builder.load_blueprint(series)
        meta = {"series": series.id, "series_name": series.name, "test": test.id, "title": test.title, "kind": KINDS[test.kind],
                "exam": exam.name, "language": test.language, "minutes": test.duration_minutes, "marks": test.total_marks,
                "access": test.access, "note": test.note, "brand": settings.brand_name,
                "instructions": bp.instructions if bp else [],
                "sections": [{"subject": s.subject, "questions": len(s.question_ids), "marks": s.marks_per_question,
                              "negative": s.negative_marks} for s in test.sections]}
        return _page(_env.get_template("test_player.html").render(
            meta_json=json.dumps(meta, ensure_ascii=False).replace("</", "<\\/"), title=test.title, brand=settings.brand_name,
            lang="hi" if test.language == "hi" else "en"))

    @r.post("/t/{series_id}/{test_id}/start")
    async def start(series_id: str, test_id: str, request: Request):
        if not starts.allow(_addr(request)):
            raise HTTPException(429, "Too many attempts. Please try again in a few minutes.")
        _, series, test = _visible(series_id, test_id)
        body = await request.json()
        name = re.sub(r"\s+", " ", str(body.get("name", ""))).strip()[:60]
        email = str(body.get("email", "")).strip()[:120]
        lead_id = 0
        if test.access == "signup":
            phone = clean_phone(str(body.get("phone", "")))
            if len(name) < 2:
                raise HTTPException(400, "Please enter your name")
            if phone is None:
                raise HTTPException(400, "Please enter a valid 10-digit mobile number")
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise HTTPException(400, "Please check your email address")
            from ..store import get_history

            try:
                lead_id = get_history().add_lead(series.id, test.id, name, phone, email)
            except Exception as e:
                log.exception("could not store lead")
                raise HTTPException(503, "Could not register right now, please try again") from e
        return {"token": make_token(series.id, test.id, lead_id, name), "questions": public_questions(test)}

    @r.post("/t/{series_id}/{test_id}/submit")
    async def submit(series_id: str, test_id: str, request: Request):
        if not submits.allow(_addr(request)):
            raise HTTPException(429, "Too many submissions. Please try again in a few minutes.")
        _, series, test = _visible(series_id, test_id)
        body = await request.json()
        tok = read_token(str(body.get("token", "")), series.id, test.id)
        if tok is None:
            raise HTTPException(403, "This attempt has expired. Please start the test again.")
        raw = body.get("answers") or {}
        answers = {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}
        res = score(test, answers)
        seconds = int(body.get("seconds") or 0) if str(body.get("seconds") or "0").isdigit() else 0
        from ..store import get_history

        hist = get_history()
        try:
            others = hist.test_scores(series.id, test.id)
            hist.add_attempt({"series_id": series.id, "test_id": test.id, "lead_id": tok.get("l") or None, "name": tok.get("n", ""),
                              "score": res["score"], "max_score": res["max_score"], "correct": res["correct"], "wrong": res["wrong"],
                              "skipped": res["skipped"], "seconds": min(seconds, 86400),
                              "answers": {k: v for k, v in answers.items() if isinstance(v, int)}})
        except Exception:
            log.exception("could not store attempt")
            others = []
        res["attempts"] = len(others) + 1
        res["rank"] = 1 + sum(1 for s in others if s > res["score"])
        res["percentile"] = round(100.0 * sum(1 for s in others if s < res["score"]) / len(others), 1) if others else 100.0
        nxt = sorted((t for t in builder.built(series.id).values() if t.access != "private" and t.order > test.order), key=lambda t: t.order)
        res["next"] = [{"id": t.id, "title": t.title, "kind": KINDS[t.kind]} for t in nxt[:3]]
        return res

    @r.get("/t/{series_id}/{test_id}/{name}.pdf")
    def pdf(series_id: str, test_id: str, name: str):
        _, series, test = _visible(series_id, test_id)
        if test.access != "free" or name not in ("paper", "solutions"):
            raise HTTPException(404, "Not found")
        path = builder.test_dir(series.id, test.id) / f"{name}.pdf"
        if not path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(path, media_type="application/pdf", filename=f"{series.id}-{test.id}-{name}.pdf")

    return r
