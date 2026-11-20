"""One-time helper: get a YouTube refresh token for the upload scope.

1. Google Cloud Console -> enable "YouTube Data API v3".
2. Create an OAuth client ID of type "Desktop app"; put its id/secret in .env
   as YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET.
3. Run:  python scripts/youtube_auth.py
   Open the printed URL, sign in with the channel's Google account, approve.
4. Copy the printed YOUTUBE_REFRESH_TOKEN into .env.
"""

from __future__ import annotations

import http.server
import os
import secrets
import sys
import threading
import urllib.parse
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first")
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state and "code" in q:
                got["code"] = q["code"]
                msg = b"Authorised - you can close this tab."
            else:
                msg = b"Missing code or bad state."
            self.send_response(200)
            self.end_headers()
            self.wfile.write(msg)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
         "access_type": "offline", "prompt": "consent", "state": state}
    )
    print("Open this URL in a browser on this machine:\n\n" + url + "\n")
    while "code" not in got:
        threading.Event().wait(0.5)
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": got["code"], "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    })
    r.raise_for_status()
    print("\nYOUTUBE_REFRESH_TOKEN=" + r.json()["refresh_token"])


if __name__ == "__main__":
    main()
