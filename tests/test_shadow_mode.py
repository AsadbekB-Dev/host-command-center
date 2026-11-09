from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.hospitable_mapper import booking_from_remote, property_profile_from_remote
from core.models import ApprovalState, PropertyProfile, Recommendation
from services.shadow_sync_service import ShadowSyncService


class FakeHospitable:
    def __init__(self):
        self.status = "pending"
        self.updated = "2026-09-22T10:00:00-07:00"

    def list_properties(self):
        return [{"id":"p1","name":"Real Airbnb House","bedrooms":3,"bathrooms":2,"max_guests":6,"address":{"city":"Springfield","state":"CA"}}]

    def list_reservations(self, property_ids):
        assert property_ids == ["p1"]
        return [{
            "id":"r1", "status":self.status, "updated_at":self.updated,
            "check_in":"2026-10-10", "check_out":"2026-10-15", "guest_count":4,
            "guest":{"first_name":"Live","last_name":"Guest"}, "property":{"id":"p1"},
            "financials":{"total_price":1750}, "created_at":datetime.now().isoformat(),
        }]


class FakeGmail:
    def __init__(self): self.sent=[]
    def configured(self): return True
    def send(self, recipient, subject, text_body, html_body=None):
        self.sent.append((recipient,subject,text_body))
        return f"msg-{len(self.sent)}"


def test_missing_live_policy_forces_review():
    prop = property_profile_from_remote({"id":"p1","name":"Live","max_guests":6})
    booking = booking_from_remote({
        "id":"r1","status":"pending","check_in":"2026-10-10","check_out":"2026-10-15",
        "guest_count":4,"property":{"id":"p1"},"financials":{"total_price":2000},
    }, prop)
    analysis = BookingReasoner().analyze(booking, prop)
    assert analysis.recommendation == Recommendation.REVIEW
    assert any("Pricing policy is not configured" in x for x in analysis.warnings)


def test_missing_financials_forces_review():
    prop = PropertyProfile("HOSP-p1","Live",max_guests=6,nightly_floor=200,nightly_target=300,nightly_ceiling=600,hospitable_property_id="p1",source="hospitable",policy_configured=True)
    booking = booking_from_remote({
        "id":"r1","status":"pending","check_in":"2026-10-10","check_out":"2026-10-15",
        "guest_count":4,"property":{"id":"p1"},
    }, prop)
    analysis = BookingReasoner().analyze(booking, prop)
    assert booking.financials_known is False
    assert analysis.recommendation == Recommendation.REVIEW


def test_shadow_sync_live_pending_emails_once_and_reconciles(tmp_path: Path):
    db=Database(tmp_path/"db.sqlite")
    db.set_setting("notification_recipient","owner@example.com")
    provider=FakeHospitable(); gmail=FakeGmail()
    svc=ShadowSyncService(db,EventEngine(db,BookingReasoner()),provider,gmail)
    svc.sync_properties()
    # Configure policy after live property discovery.
    p=db.list_properties()[0]
    p.nightly_floor=200; p.nightly_target=300; p.nightly_ceiling=600; p.cleaning_cost=100; p.policy_configured=True
    db.upsert_property(p)

    first=svc.sync_reservations(notify=True)
    assert first["pending"]==1 and first["emailed"]==1
    assert len(gmail.sent)==1
    assert db.get_approval("r1")["state"] == ApprovalState.PENDING_APPROVAL.value

    second=svc.sync_reservations(notify=True)
    assert second["emailed"]==0
    assert len(gmail.sent)==1

    provider.status="accepted"; provider.updated="2026-09-22T11:00:00-07:00"
    third=svc.sync_reservations(notify=True)
    assert third["accepted"]==1
    assert db.get_approval("r1")["state"] == ApprovalState.CONFIRMED.value


def test_remote_property_refresh_preserves_local_policy(tmp_path: Path):
    db=Database(tmp_path/"db.sqlite"); provider=FakeHospitable(); gmail=FakeGmail()
    svc=ShadowSyncService(db,EventEngine(db,BookingReasoner()),provider,gmail)
    svc.sync_properties()
    p=db.list_properties()[0]
    p.nightly_floor=210; p.nightly_target=330; p.nightly_ceiling=700; p.policy_configured=True
    db.upsert_property(p)
    svc.sync_properties()
    refreshed=db.list_properties()[0]
    assert refreshed.nightly_target==330
    assert refreshed.policy_configured is True

def test_pending_update_after_manual_decision_does_not_reemail(tmp_path: Path):
    from services.approval_service import ApprovalService
    db=Database(tmp_path/"db.sqlite"); db.set_setting("notification_recipient","owner@example.com")
    provider=FakeHospitable(); gmail=FakeGmail()
    svc=ShadowSyncService(db,EventEngine(db,BookingReasoner()),provider,gmail)
    svc.sync_properties(); p=db.list_properties()[0]
    p.nightly_floor=200; p.nightly_target=300; p.nightly_ceiling=600; p.policy_configured=True; db.upsert_property(p)
    svc.sync_reservations(notify=True)
    ApprovalService(db).approve("r1")
    provider.updated="2026-09-22T10:30:00-07:00"
    provider.status="pending"
    out=svc.sync_reservations(notify=True)
    assert out["emailed"] == 0
    assert len(gmail.sent) == 1
    assert db.get_approval("r1")["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value
