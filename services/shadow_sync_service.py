from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from core.database import Database
from core.event_engine import EventEngine
from core.hospitable_mapper import booking_from_remote, property_profile_from_remote, reservation_property_id, reservation_status
from core.models import ApprovalState, PropertyProfile
from providers.gmail import GmailProvider
from providers.hospitable import HospitableProvider
from services.notification_service import NotificationService


class ShadowSyncService:
    """Live-read / live-notify sync. No Airbnb/Hospitable writes exist here."""

    def __init__(self, db: Database, engine: EventEngine, provider: HospitableProvider, gmail: GmailProvider | None = None):
        self.db = db
        self.engine = engine
        self.provider = provider
        self.gmail = gmail

    def sync_properties(self) -> int:
        rows = self.provider.list_properties()
        self.db.save_remote_properties("hospitable", rows)
        existing_by_remote = {p.hospitable_property_id: p for p in self.db.list_properties() if p.hospitable_property_id}
        live_ids: set[str] = set()
        for row in rows:
            rid = str(row.get("id") or row.get("uuid") or row.get("property_id") or "").strip()
            if not rid:
                continue
            live_ids.add(rid)
            profile = property_profile_from_remote(row, existing_by_remote.get(rid))
            self.db.upsert_property(profile)
        self.db.log("INFO", "HOSPITABLE", f"Live property sync stored {len(rows)} properties")
        return len(rows)

    def _profile_map(self) -> dict[str, PropertyProfile]:
        return {p.hospitable_property_id: p for p in self.db.list_properties() if p.source == "hospitable" and p.hospitable_property_id}

    def sync_reservations(self, notify: bool = True) -> dict[str, int]:
        props = self._profile_map()
        if not props:
            self.sync_properties()
            props = self._profile_map()
        rows = self.provider.list_reservations(list(props.keys()))
        self.db.save_remote_reservations("hospitable", rows)
        stats = {"fetched": len(rows), "pending": 0, "accepted": 0, "cancelled": 0, "not_accepted": 0, "unmapped": 0, "errors": 0, "emailed": 0}

        for row in rows:
            try:
                pid = reservation_property_id(row)
                prop = props.get(pid)
                if not prop:
                    stats["unmapped"] += 1
                    continue
                status = reservation_status(row)
                booking = booking_from_remote(row, prop)
                existing = self.db.get_approval(booking.reservation_id)

                if status == "pending":
                    stats["pending"] += 1
                    analysis = self.engine.ingest_booking_request(booking, prop)
                    current = self.db.get_approval(booking.reservation_id)
                    if notify and current and current.get("state") == ApprovalState.PENDING_APPROVAL.value and self.gmail and self.gmail.configured():
                        recipient = str(self.db.get_setting("notification_recipient", "") or "").strip()
                        if recipient and self.db.claim_notification("approval_email", booking.reservation_id, booking.event_id):
                            try:
                                NotificationService(self.db, self.gmail).send_approval_email(current, recipient)
                                stats["emailed"] += 1
                            except Exception as exc:
                                self.db.release_notification("approval_email", booking.reservation_id, booking.event_id)
                                self.db.log("ERROR", "EMAIL", "Automatic approval email failed", booking.reservation_id, {"error": str(exc)})
                    continue

                if status == "accepted":
                    stats["accepted"] += 1
                    if existing and existing["state"] != ApprovalState.CONFIRMED.value:
                        self.db.update_state(booking.reservation_id, ApprovalState.CONFIRMED)
                        self.db.log("INFO", "RECONCILE", "Provider reports reservation accepted; state set to CONFIRMED", booking.reservation_id)
                elif status == "cancelled":
                    stats["cancelled"] += 1
                    if existing and existing["state"] != ApprovalState.CANCELLED.value:
                        self.db.update_state(booking.reservation_id, ApprovalState.CANCELLED)
                        self.db.log("INFO", "RECONCILE", "Provider reports reservation cancelled", booking.reservation_id)
                elif status == "not_accepted":
                    stats["not_accepted"] += 1
                    if existing and existing["state"] != ApprovalState.NOT_ACCEPTED.value:
                        self.db.update_state(booking.reservation_id, ApprovalState.NOT_ACCEPTED)
                        self.db.log("INFO", "RECONCILE", "Provider reports reservation not accepted", booking.reservation_id)
            except Exception as exc:
                stats["errors"] += 1
                rid = str(row.get("id") or row.get("uuid") or row.get("code") or "")
                self.db.log("ERROR", "RECONCILE", "Reservation sync row failed", rid, {"error": str(exc)})

        self.db.set_setting("last_reservation_sync_at", datetime.now().isoformat(timespec="seconds"))
        self.db.log("INFO", "HOSPITABLE", "Live reservation reconciliation complete", details=stats)
        return stats
