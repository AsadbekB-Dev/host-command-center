from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from providers.base import NotificationProvider


TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailProvider(NotificationProvider):
    """Minimal Gmail API sender using an OAuth refresh token.

    No Gmail password is stored. V0.4 can load OAuth credentials from the
    Windows DPAPI-backed SecretStore. A legacy local JSON file remains accepted
    only for migration/backward compatibility.
    """

    def __init__(self, auth_file: str | Path, secret_store: Any | None = None):
        self.auth_file = Path(auth_file)
        self.secret_store = secret_store

    def configured(self) -> bool:
        if self.secret_store:
            raw = self.secret_store.get_secret("gmail_oauth_json", "")
            if raw:
                return True
        return self.auth_file.exists()

    def _load(self) -> dict:
        data = None
        if self.secret_store:
            raw = self.secret_store.get_secret("gmail_oauth_json", "")
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Encrypted Gmail OAuth data is invalid") from exc
        if data is None and self.auth_file.exists():
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
        if data is None:
            raise RuntimeError("Gmail OAuth is not configured")
        required = {"client_id", "client_secret", "refresh_token"}
        if not required.issubset(data):
            raise RuntimeError("Gmail OAuth configuration is incomplete")
        return data

    def _access_token(self) -> str:
        cfg = self._load()
        form = urllib.parse.urlencode({
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        }).encode("utf-8")
        req = urllib.request.Request(TOKEN_URL, data=form, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))["access_token"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Gmail token refresh failed ({exc.code}): {detail}") from exc

    def health(self) -> tuple[bool, str]:
        if not self.configured():
            return False, "Not connected"
        try:
            self._access_token()
            return True, "Connected"
        except Exception as exc:
            return False, str(exc)

    def send(self, recipient: str, subject: str, text_body: str, html_body: str | None = None) -> str:
        if not recipient.strip():
            raise ValueError("Recipient email is empty")
        msg = EmailMessage()
        msg["To"] = recipient.strip()
        msg["Subject"] = subject
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        payload = json.dumps({"raw": raw}).encode("utf-8")
        req = urllib.request.Request(
            GMAIL_SEND_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return str(result.get("id", "sent"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Gmail send failed ({exc.code}): {detail}") from exc
