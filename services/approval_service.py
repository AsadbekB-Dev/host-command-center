from __future__ import annotations

from datetime import datetime
import webbrowser

from core.database import Database
from core.models import ApprovalState
from core.state_machine import require_transition


class ApprovalExpired(RuntimeError):
    pass


class ApprovalService:
    def __init__(self, db: Database):
        self.db = db

    def _check_not_expired(self, row: dict) -> None:
        raw = row.get("expires_at")
        if raw and datetime.fromisoformat(raw) <= datetime.now():
            current = ApprovalState(row["state"])
            if current == ApprovalState.PENDING_APPROVAL:
                self.db.update_state(row["reservation_id"], ApprovalState.EXPIRED)
                self.db.log("WARN", "SAFETY", "Blocked action on expired booking request", row["reservation_id"])
            raise ApprovalExpired("This booking request has expired. Refresh the platform before taking action.")

    def approve(self, reservation_id: str) -> None:
        row = self._require(reservation_id)
        self._check_not_expired(row)
        current = ApprovalState(row["state"])
        require_transition(current, ApprovalState.APPROVED)
        self.db.update_state(reservation_id, ApprovalState.APPROVED)
        self.db.log("INFO", "APPROVAL", "User approved reservation for platform action", reservation_id)
        self.db.update_state(reservation_id, ApprovalState.WAITING_PLATFORM_CONFIRMATION)
        self.db.log("INFO", "SAFETY", "Safe Mode: no live accept action sent; waiting for official platform confirmation", reservation_id)

    def decline(self, reservation_id: str) -> None:
        row = self._require(reservation_id)
        self._check_not_expired(row)
        current = ApprovalState(row["state"])
        require_transition(current, ApprovalState.DECLINED)
        self.db.update_state(reservation_id, ApprovalState.DECLINED)
        self.db.log("INFO", "APPROVAL", "User marked reservation as decline decision; no live action sent in Safe Mode", reservation_id)

    def dismiss(self, reservation_id: str) -> None:
        row = self._require(reservation_id)
        current = ApprovalState(row["state"])
        require_transition(current, ApprovalState.DISMISSED)
        self.db.update_state(reservation_id, ApprovalState.DISMISSED)
        self.db.log("INFO", "APPROVAL", "Approval card dismissed", reservation_id)

    def open_platform(self, reservation_id: str) -> None:
        row = self._require(reservation_id)
        url = row.get("platform_url") or "https://my.hospitable.com/"
        webbrowser.open(url)
        self.db.log("INFO", "PLATFORM", "Opened official platform for manual confirmation", reservation_id, {"url": url})

    def _require(self, reservation_id: str) -> dict:
        row = self.db.get_approval(reservation_id)
        if not row:
            raise KeyError(f"Reservation not found: {reservation_id}")
        return row
