from __future__ import annotations

from datetime import date
from pathlib import Path

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.models import ApprovalState, BookingRequest, PropertyProfile, Recommendation
from services.approval_service import ApprovalService


def prop():
    return PropertyProfile(
        key="TEST",
        name="Test 3BR",
        address="Test",
        bedrooms=3,
        bathrooms=2,
        max_guests=6,
        nightly_floor=200,
        nightly_target=300,
        nightly_ceiling=600,
        cleaning_cost=150,
    )


def booking(event="evt-1", reservation="res-1", guests=4, revenue=1600):
    return BookingRequest(
        event_id=event,
        reservation_id=reservation,
        property_key="TEST",
        guest_name="Demo",
        check_in=date(2026, 10, 10),
        check_out=date(2026, 10, 15),
        guests=guests,
        gross_revenue=revenue,
    )


def test_good_booking_accept():
    result = BookingReasoner().analyze(booking(), prop())
    assert result.recommendation == Recommendation.ACCEPT
    assert result.score >= 72


def test_over_capacity_not_accept():
    result = BookingReasoner().analyze(booking(guests=20), prop())
    assert result.recommendation != Recommendation.ACCEPT
    assert any("exceeds property capacity" in w for w in result.warnings)


def test_event_idempotency(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    e = EventEngine(db, BookingReasoner())
    p = prop()
    db.upsert_property(p)
    first = e.ingest_booking_request(booking(), p)
    second = e.ingest_booking_request(booking(), p)
    assert first is not None
    assert second is None
    assert len(db.list_approvals()) == 1


def test_approval_safe_mode_stops_at_waiting(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    p = prop(); db.upsert_property(p)
    EventEngine(db, BookingReasoner()).ingest_booking_request(booking(), p)
    ApprovalService(db).approve("res-1")
    row = db.get_approval("res-1")
    assert row["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value
