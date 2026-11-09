from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.models import ApprovalState, BookingRequest, PropertyProfile


class EventEngine:
    def __init__(self, db: Database, reasoner: BookingReasoner):
        self.db = db
        self.reasoner = reasoner

    @staticmethod
    def _validate_booking(booking: BookingRequest, prop: PropertyProfile) -> None:
        if not booking.event_id.strip():
            raise ValueError("event_id is required")
        if not booking.reservation_id.strip():
            raise ValueError("reservation_id is required")
        if booking.property_key != prop.key:
            raise ValueError(f"Property mismatch: event={booking.property_key} profile={prop.key}")
        if booking.check_out <= booking.check_in:
            raise ValueError("check_out must be after check_in")
        if booking.guests <= 0:
            raise ValueError("guests must be greater than zero")
        if booking.gross_revenue < 0:
            raise ValueError("gross_revenue cannot be negative")
        if booking.nights > 365:
            raise ValueError("stay length is outside the supported safety range")

    def ingest_booking_request(self, booking: BookingRequest, prop: PropertyProfile):
        self._validate_booking(booking, prop)

        # Atomic idempotency claim: safe even if multiple webhook workers race.
        if not self.db.claim_event(booking.event_id):
            self.db.log("WARN", "EVENT", f"Duplicate event ignored: {booking.event_id}", booking.reservation_id)
            return None

        booking.received_at = booking.received_at or datetime.now()
        if booking.expires_at is None and booking.response_deadline_known:
            booking.expires_at = booking.received_at + timedelta(hours=24)

        self.db.log("INFO", "EVENT", "Booking request received", booking.reservation_id, {"source": booking.source})
        analysis = self.reasoner.analyze(booking, prop)

        existing = self.db.get_approval(booking.reservation_id)
        if booking.expires_at <= datetime.now():
            target_state = ApprovalState.EXPIRED
        elif existing:
            old = ApprovalState(existing["state"])
            # Re-analysis may update facts, but must never resurrect a completed/acted-on request.
            target_state = old if old not in {ApprovalState.NEW, ApprovalState.ANALYZING, ApprovalState.PENDING_APPROVAL} else ApprovalState.PENDING_APPROVAL
        else:
            target_state = ApprovalState.PENDING_APPROVAL

        self.db.save_approval(booking, analysis, target_state)
        self.db.log(
            "INFO",
            "ANALYSIS",
            f"Recommendation: {analysis.recommendation.value} ({analysis.score}/100)",
            booking.reservation_id,
            analysis.to_dict(),
        )
        if target_state == ApprovalState.EXPIRED:
            self.db.log("WARN", "SAFETY", "Request arrived already expired; no approval action allowed", booking.reservation_id)
        return analysis

    @staticmethod
    def simulated_booking(prop: PropertyProfile, guest_name: str = "Demo Guest") -> BookingRequest:
        today = datetime.now().date()
        rid = f"SIM-{uuid.uuid4().hex[:8].upper()}"
        return BookingRequest(
            event_id=f"evt-{uuid.uuid4().hex}",
            reservation_id=rid,
            property_key=prop.key,
            guest_name=guest_name,
            check_in=today + timedelta(days=9),
            check_out=today + timedelta(days=14),
            guests=min(4, prop.max_guests),
            gross_revenue=prop.nightly_target * 5 + 75,
            source="simulation",
            platform_url="https://my.hospitable.com/",
        )
