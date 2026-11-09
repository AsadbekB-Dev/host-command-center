from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from core.database import Database
from core.secret_store import SecretStore
from providers.gmail import GmailProvider
from providers.hospitable import HospitableProvider
from core.hospitable_mapper import property_profile_from_remote
from core.booking_reasoner import BookingReasoner
from core.event_engine import EventEngine
from services.shadow_sync_service import ShadowSyncService


class SetupManager:
    STEPS = ("system", "email", "hospitable", "properties", "webhook", "final")

    def __init__(self, app_dir: Path, db: Database, secrets: SecretStore):
        self.app_dir = Path(app_dir)
        self.data_dir = self.app_dir / "data"
        self.db = db
        self.secrets = secrets

    def step_status(self) -> dict[str, dict[str, Any]]:
        db_ok, db_detail = self.db.integrity_check()
        gmail_provider = GmailProvider(self.data_dir / "gmail_auth.json", self.secrets)
        gmail_configured = gmail_provider.configured()
        recipient = str(self.db.get_setting("notification_recipient", "") or "").strip()
        gmail_live_test = bool(self.db.get_setting("gmail_live_test_passed", False))
        token = self.secrets.get_secret("hospitable_pat", "")
        hospitable_health = bool(self.db.get_setting("hospitable_health_passed", False))
        remote_count = len(self.db.list_remote_properties("hospitable"))
        webhook_token = str(self.db.get_setting("webhook_path_token", "") or "").strip()
        final_done = bool(self.db.get_setting("setup_completed_at", ""))
        return {
            "system": {
                "ok": sys.version_info >= (3, 11) and db_ok,
                "detail": f"Python {sys.version_info.major}.{sys.version_info.minor} • database {db_detail}",
            },
            "email": {
                "ok": gmail_configured and bool(recipient) and gmail_live_test,
                "detail": "Gmail authorized + recipient saved + live test delivered" if gmail_configured and recipient and gmail_live_test else "Needs Gmail OAuth, approval recipient, and successful live test email",
            },
            "hospitable": {
                "ok": bool(token) and hospitable_health,
                "detail": "Hospitable PAT securely stored + connection verified" if token and hospitable_health else "Needs read-only Hospitable PAT and successful connection test",
            },
            "properties": {
                "ok": remote_count > 0,
                "detail": f"{remote_count} live properties cached" if remote_count else "Live properties not synced yet",
            },
            "webhook": {
                "ok": bool(webhook_token),
                "detail": "Webhook path secret prepared (cloud deployment still required)" if webhook_token else "Webhook secret not generated",
            },
            "final": {
                "ok": final_done,
                "detail": f"Last completed {self.db.get_setting('setup_completed_at', '')}" if final_done else "Final readiness check not completed",
            },
        }

    def save_recipient(self, recipient: str) -> None:
        recipient = recipient.strip()
        if "@" not in recipient or recipient.startswith("@") or recipient.endswith("@"):
            raise ValueError("Enter a valid approval email address")
        self.db.set_setting("notification_recipient", recipient)
        self.db.log("INFO", "SETUP", "Approval recipient saved")

    def save_hospitable_token(self, token: str) -> None:
        token = token.strip()
        if len(token) < 12:
            raise ValueError("Hospitable PAT looks too short")
        self.secrets.set_secret("hospitable_pat", token)
        self.db.set_setting("hospitable_secret_saved", True)
        self.db.log("INFO", "SETUP", "Hospitable PAT stored securely")

    def hospitable_health(self) -> tuple[bool, str]:
        token = self.secrets.get_secret("hospitable_pat", "")
        if not token:
            self.db.set_setting("hospitable_health_passed", False)
            return False, "Hospitable PAT is not stored"
        ok, detail = HospitableProvider(token).health()
        self.db.set_setting("hospitable_health_passed", ok)
        return ok, detail

    def sync_properties(self) -> int:
        token = self.secrets.get_secret("hospitable_pat", "")
        if not token:
            raise RuntimeError("Hospitable PAT is not stored")
        rows = HospitableProvider(token).list_properties()
        count = self.db.save_remote_properties("hospitable", rows)
        existing = {p.hospitable_property_id: p for p in self.db.list_properties() if p.hospitable_property_id}
        for row in rows:
            rid = str(row.get("id") or row.get("uuid") or row.get("property_id") or "").strip()
            if not rid:
                continue
            self.db.upsert_property(property_profile_from_remote(row, existing.get(rid)))
        self.db.log("INFO", "SETUP", f"Setup wizard synced {count} Hospitable properties and mapped live profiles")
        return count

    def gmail_configured(self) -> bool:
        return GmailProvider(self.data_dir / "gmail_auth.json", self.secrets).configured()

    def gmail_health(self) -> tuple[bool, str]:
        return GmailProvider(self.data_dir / "gmail_auth.json", self.secrets).health()

    def import_gmail_auth_file(self, path: str | Path) -> None:
        path = Path(path)
        data = __import__("json").loads(path.read_text(encoding="utf-8"))
        required = {"client_id", "client_secret", "refresh_token"}
        if not required.issubset(data):
            raise ValueError("Gmail OAuth output is incomplete")
        self.secrets.set_secret("gmail_oauth_json", __import__("json").dumps(data))
        self.db.set_setting("gmail_secret_saved", True)
        try:
            path.unlink()
        except OSError:
            pass
        legacy = self.data_dir / "gmail_auth.json"
        if legacy.exists() and legacy != path:
            try:
                legacy.unlink()
            except OSError:
                pass
        self.db.log("INFO", "SETUP", "Gmail OAuth credentials moved to encrypted secret storage")


    def send_live_test_email(self) -> str:
        recipient = str(self.db.get_setting("notification_recipient", "") or "").strip()
        if not recipient:
            raise RuntimeError("Approval recipient is not configured")
        provider = GmailProvider(self.data_dir / "gmail_auth.json", self.secrets)
        if not provider.configured():
            raise RuntimeError("Gmail OAuth is not configured")
        msg = provider.send(
            recipient,
            "Host Command Center — LIVE Gmail Test",
            "This is a real test email sent through the Gmail API.\n\nLive reads/alerts are enabled; Airbnb/Hospitable write actions remain disabled.",
        )
        self.db.set_setting("gmail_live_test_passed", True)
        self.db.log("INFO", "SETUP", "Live Gmail test email sent", details={"recipient": recipient, "message_id": msg})
        return msg

    def initialize_shadow_mode(self) -> dict[str, int]:
        token = self.secrets.get_secret("hospitable_pat", "")
        if not token:
            raise RuntimeError("Hospitable PAT is not stored")
        provider = HospitableProvider(token)
        gmail = GmailProvider(self.data_dir / "gmail_auth.json", self.secrets) if self.gmail_configured() else None
        service = ShadowSyncService(self.db, EventEngine(self.db, BookingReasoner()), provider, gmail)
        service.sync_properties()
        return service.sync_reservations(notify=True)

    def prepare_webhook_secret(self) -> str:
        import secrets
        existing = str(self.db.get_setting("webhook_path_token", "") or "")
        if existing:
            return existing
        token = secrets.token_urlsafe(32)
        self.db.set_setting("webhook_path_token", token)
        self.db.log("INFO", "SETUP", "Webhook path secret generated")
        return token

    def mark_complete(self) -> None:
        self.db.set_setting("setup_completed_at", datetime.now().isoformat(timespec="seconds"))
        self.db.log("INFO", "SETUP", "Setup wizard completed")
