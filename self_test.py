from __future__ import annotations

import tempfile
from pathlib import Path
from datetime import date

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.models import ApprovalState, BookingRequest, PropertyProfile
from providers.mock_email import MockEmailProvider
from services.approval_service import ApprovalService


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "test.db")
        p = PropertyProfile("TEST", "Test", "", 3, 2, 6, 200, 300, 600, 150)
        db.upsert_property(p)
        b = BookingRequest("event-1", "reservation-1", "TEST", "Demo Guest", date(2026,10,10), date(2026,10,15), 4, 1600)
        engine = EventEngine(db, BookingReasoner())
        analysis = engine.ingest_booking_request(b, p)
        assert analysis is not None and analysis.score >= 72
        assert engine.ingest_booking_request(b, p) is None
        ApprovalService(db).approve("reservation-1")
        assert db.get_approval("reservation-1")["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value
        out = Path(td) / "outbox"
        MockEmailProvider(out).send("owner@example.com", "Self test", "OK")
        assert list(out.glob("*.txt"))
    print("Host Command Center self-test: PASS")


if __name__ == "__main__":
    main()
