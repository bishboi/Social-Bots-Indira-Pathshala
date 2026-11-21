"""Login page + session cookie; the server must never trigger the browser's Basic-auth dialog."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from pathshala_bot.config import settings
    from pathshala_bot.web import auth
    from pathshala_bot.web.app import create_app

    saved = dict(vars(settings))
    object.__setattr__(settings, "output_dir", tmp_path)
    object.__setattr__(settings, "db_path", tmp_path / "h.sqlite3")
    object.__setattr__(settings, "supabase_url", "")
    monkeypatch.setenv("CONTROL_FILE", str(tmp_path / "c.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret pass")
    auth.limiter = auth.LoginLimiter()
    with TestClient(create_app(with_bot=False), follow_redirects=False) as c:
        yield c
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def test_no_basic_auth_challenge_anywhere(client):
    for path in ("/", "/api/config", "/api/status", "/files/x/y"):
        r = client.get(path)
        assert "www-authenticate" not in {k.lower() for k in r.headers}, path
    assert client.get("/api/status").status_code == 401


def test_home_redirects_to_login_then_login_sets_cookie(client):
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    page = client.get("/login")
    assert page.status_code == 200 and 'name="password"' in page.text

    bad = client.post("/login", data={"username": "admin", "password": "nope"})
    assert bad.headers["location"] == "/login?error=wrong" and "pathshala_session" not in bad.cookies

    ok = client.post("/login", data={"username": "admin", "password": "s3cret pass"})
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/").status_code == 200  # cookie kept by the client
    assert client.get("/api/status").status_code == 200
    assert client.get("/login").status_code == 303  # already signed in

    client.post("/logout")
    assert client.get("/api/status").status_code == 401


def test_tampered_or_stale_cookie_rejected(client, monkeypatch):
    from pathshala_bot.web import auth

    token = auth.make_token("admin")
    client.cookies.set(auth.COOKIE, token[:-2] + "xx")
    assert client.get("/api/status").status_code == 401
    client.cookies.set(auth.COOKIE, token)
    assert client.get("/api/status").status_code == 200
    monkeypatch.setenv("ADMIN_PASSWORD", "changed")  # changing the password logs everyone out
    assert client.get("/api/status").status_code == 401
    client.cookies.set(auth.COOKIE, auth.make_token("admin", days=-1))
    assert client.get("/api/status").status_code == 401  # expired


def test_basic_header_still_works_for_scripts(client):
    assert client.get("/api/status", auth=("admin", "s3cret pass")).status_code == 200
    assert client.get("/api/status", auth=("admin", "wrong")).status_code == 401


def test_lockout_after_repeated_failures(client, monkeypatch):
    import asyncio

    from pathshala_bot.web import auth

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    for _ in range(auth.MAX_FAILURES):
        client.post("/login", data={"username": "admin", "password": "x"})
    r = client.post("/login", data={"username": "admin", "password": "s3cret pass"})
    assert "error=locked" in r.headers["location"]


def test_missing_password_message(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    page = client.get("/login")
    assert "ADMIN_PASSWORD is not set" in page.text and "disabled" in page.text
    assert client.get("/api/status").status_code == 503
