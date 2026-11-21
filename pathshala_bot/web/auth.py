"""Admin login: a login page + signed session cookie (no browser Basic-auth pop-ups).

- Cookie: HMAC-SHA256 signed {user, expiry}; HttpOnly, SameSite=Lax (not sent on
  cross-site requests), Secure when served over HTTPS. The signing key is derived from
  ADMIN_PASSWORD (+ optional SESSION_SECRET), so changing the password logs everyone out.
- Scripts may still send `Authorization: Basic ...`; the server never answers with a
  WWW-Authenticate challenge, so browsers never show their own login dialog.
- Failed logins are rate-limited per client address.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

COOKIE = "pathshala_session"
SESSION_DAYS = 7
MAX_FAILURES = 8          # per address ...
FAILURE_WINDOW = 15 * 60  # ... within 15 minutes


def admin_user() -> str:
    return os.getenv("ADMIN_USER", "admin")


def admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "")


def _key() -> bytes:
    return hashlib.sha256(f"pathshala-session|{os.getenv('SESSION_SECRET', '')}|{admin_password()}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user: str, days: int = SESSION_DAYS) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int(time.time()) + days * 86400}).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token: str | None) -> str | None:
    """The user name in a valid, unexpired token, else None."""
    if not token or "." not in token or not admin_password():
        return None
    payload, sig = token.rsplit(".", 1)
    expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time() or data.get("u") != admin_user():
        return None
    return data["u"]


def check_password(user: str, password: str) -> bool:
    expected = admin_password()
    if not expected:
        return False
    ok_user = secrets.compare_digest(user.encode(), admin_user().encode())
    ok_pass = secrets.compare_digest(password.encode(), expected.encode())
    return ok_user and ok_pass


def check_basic_header(header: str | None) -> str | None:
    """Accept `Authorization: Basic ...` from scripts (curl -u); never challenge browsers."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return user if check_password(user, password) else None


class LoginLimiter:
    """Slow down password guessing: too many failures from one address -> temporary lockout."""

    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, addr: str) -> int:
        """Seconds until this address may try again (0 = allowed)."""
        now = time.time()
        with self._lock:
            recent = [t for t in self._fails.get(addr, []) if now - t < FAILURE_WINDOW]
            self._fails[addr] = recent
            if len(recent) >= MAX_FAILURES:
                return int(FAILURE_WINDOW - (now - recent[0])) + 1
        return 0

    def fail(self, addr: str) -> None:
        with self._lock:
            self._fails.setdefault(addr, []).append(time.time())

    def reset(self, addr: str) -> None:
        with self._lock:
            self._fails.pop(addr, None)


limiter = LoginLimiter()
