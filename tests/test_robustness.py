from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.models import ApprovalState, BookingRequest, PropertyProfile
from core.state_machine import InvalidTransition
from services.approval_service import ApprovalExpired, ApprovalService


def p():
    return PropertyProfile("TEST", "Test", "", 3, 2, 6, 200, 300, 600, 150)


def b(event="evt", reservation="res", **kw):
    data = dict(
        event_id=event, reservation_id=reservation, property_key="TEST", guest_name="Demo",
        check_in=date(2026,10,10), check_out=date(2026,10,15), guests=4, gross_revenue=1600,
    )
    data.update(kw)
    return BookingRequest(**data)


def test_atomic_claim_under_concurrency(tmp_path: Path):
    db = Database(tmp_path/"db.sqlite"); db.upsert_property(p())
    engine = EventEngine(db, BookingReasoner())
    item = b()
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda _: engine.ingest_booking_request(item, p()), range(80)))
    assert sum(x is not None for x in results) == 1
    assert len(db.list_approvals()) == 1


@pytest.mark.parametrize("item", [
    b("e1","r1", check_out=date(2026,10,10)),
    b("e2","r2", guests=0),
    b("e3","r3", gross_revenue=-1),
])
def test_invalid_event_rejected(tmp_path: Path, item):
    db = Database(tmp_path/"db.sqlite")
    with pytest.raises(ValueError):
        EventEngine(db, BookingReasoner()).ingest_booking_request(item, p())


def test_expired_event_cannot_be_approved(tmp_path: Path):
    db = Database(tmp_path/"db.sqlite"); db.upsert_property(p())
    item = b(expires_at=datetime.now()-timedelta(seconds=1))
    EventEngine(db, BookingReasoner()).ingest_booking_request(item, p())
    assert db.get_approval("res")["state"] == ApprovalState.EXPIRED.value
    with pytest.raises((ApprovalExpired, InvalidTransition)):
        ApprovalService(db).approve("res")


def test_update_does_not_resurrect_waiting_state(tmp_path: Path):
    db = Database(tmp_path/"db.sqlite"); db.upsert_property(p())
    engine = EventEngine(db, BookingReasoner())
    engine.ingest_booking_request(b("e1","r"), p())
    ApprovalService(db).approve("r")
    engine.ingest_booking_request(b("e2","r", gross_revenue=1900), p())
    assert db.get_approval("r")["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value
