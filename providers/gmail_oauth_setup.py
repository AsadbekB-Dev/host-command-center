from __future__ import annotations

import argparse
import json
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = "https://www.googleapis.com/auth/gmail.send"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def load_client(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    cfg = obj.get("installed") or obj.get("web") or obj
    return cfg


def main() -> int:
    p = argparse.ArgumentParser(description="Authorize Host Command Center to send Gmail using OAuth.")
    p.add_argument("client_secret_json", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/gmail_auth.json"))
    args = p.parse_args()

    cfg = load_client(args.client_secret_json)
    client_id = cfg["client_id"]
    client_secret = cfg["client_secret"]
    state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_response(400); self.end_headers(); self.wfile.write(b"State mismatch")
                return
            if "error" in q:
                result["error"] = q["error"][0]
            else:
                result["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Host Command Center authorized.</h2><p>You can close this tab.</p>")

        def log_message(self, *_):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("Opening Google authorization in your browser...")
    webbrowser.open(url)
    server.handle_request()
    server.server_close()

    if result.get("error"):
        raise SystemExit(f"Authorization failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise SystemExit("No authorization code received")

    form = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=form, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = json.loads(resp.read().decode("utf-8"))

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Google did not return a refresh token. Remove prior app consent and try again with prompt=consent.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": SCOPES,
    }, indent=2), encoding="utf-8")
    print(f"Saved OAuth configuration to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
