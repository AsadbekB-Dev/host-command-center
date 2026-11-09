from __future__ import annotations

import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.models import ApprovalState, BookingRequest, PropertyProfile, Recommendation
from core.state_machine import InvalidTransition
from services.approval_service import ApprovalExpired, ApprovalService
from services.shadow_sync_service import ShadowSyncService


def prop() -> PropertyProfile:
    return PropertyProfile("TEST", "Test Property", "Test", 3, 2, 6, 200, 300, 600, 150)


def booking(event="evt-1", reservation="res-1", *, guests=4, revenue=1600, expires=None, check_in=None, check_out=None):
    return BookingRequest(
        event_id=event,
        reservation_id=reservation,
        property_key="TEST",
        guest_name="Demo",
        check_in=check_in or date(2026, 10, 10),
        check_out=check_out or date(2026, 10, 15),
        guests=guests,
        gross_revenue=revenue,
        expires_at=expires,
    )


class FakeHospitable:
    def __init__(self):
        self.status = "pending"
        self.updated = "2026-09-22T10:00:00-07:00"

    def list_properties(self):
        return [{"id":"p1","name":"Live Property","bedrooms":3,"bathrooms":2,"max_guests":6}]

    def list_reservations(self, property_ids):
        return [{
            "id":"live-r1", "status":self.status, "updated_at":self.updated,
            "check_in":"2026-10-10", "check_out":"2026-10-15", "guest_count":4,
            "guest":{"first_name":"Live","last_name":"Guest"}, "property":{"id":"p1"},
            "financials":{"total_price":1750}, "created_at":datetime.now().isoformat(),
        }]


class FakeGmail:
    def __init__(self): self.sent=[]
    def configured(self): return True
    def send(self, recipient, subject, text_body, html_body=None):
        self.sent.append((recipient, subject))
        return f"msg-{len(self.sent)}"


def run() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, fn):
        try:
            detail = fn() or "PASS"
            results.append((name, True, str(detail)))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "lab.sqlite")
        p = prop(); db.upsert_property(p)
        engine = EventEngine(db, BookingReasoner())

        record("SQLite integrity", lambda: db.integrity_check()[1] if db.integrity_check()[0] else (_ for _ in ()).throw(RuntimeError(db.integrity_check()[1])))

        def duplicate_storm():
            b = booking("storm-event", "storm-res")
            accepted = 0
            lock = threading.Lock()
            def hit(_):
                nonlocal accepted
                out = engine.ingest_booking_request(b, p)
                if out is not None:
                    with lock: accepted += 1
            with ThreadPoolExecutor(max_workers=24) as ex:
                list(ex.map(hit, range(100)))
            assert accepted == 1, accepted
            assert len([x for x in db.list_approvals() if x["reservation_id"] == "storm-res"]) == 1
            return "100 concurrent duplicates -> 1 accepted event"
        record("Concurrent duplicate webhook storm", duplicate_storm)

        def malformed_events():
            bad = [
                booking("bad-1", "bad-res-1", check_in=date(2026, 10, 10), check_out=date(2026, 10, 10)),
                booking("bad-2", "bad-res-2", guests=0),
                booking("bad-3", "bad-res-3", revenue=-1),
            ]
            for item in bad:
                try: engine.ingest_booking_request(item, p)
                except ValueError: continue
                raise AssertionError("Malformed booking was accepted")
            return "invalid dates/guests/revenue rejected"
        record("Malformed event rejection", malformed_events)

        def expired_block():
            b = booking("expired-event", "expired-res", expires=datetime.now() - timedelta(minutes=1))
            engine.ingest_booking_request(b, p)
            assert db.get_approval("expired-res")["state"] == ApprovalState.EXPIRED.value
            try: ApprovalService(db).approve("expired-res")
            except (ApprovalExpired, InvalidTransition): return "expired request cannot be approved"
            raise AssertionError("Expired approval succeeded")
        record("Expired request protection", expired_block)

        def double_click():
            b = booking("double-event", "double-res")
            engine.ingest_booking_request(b, p)
            svc = ApprovalService(db); svc.approve("double-res")
            try: svc.approve("double-res")
            except InvalidTransition: return "second click rejected by state machine"
            raise AssertionError("double approval was allowed")
        record("Double-click / replay protection", double_click)

        def terminal_not_resurrected():
            b1 = booking("term-event-1", "term-res")
            engine.ingest_booking_request(b1, p); ApprovalService(db).approve("term-res")
            b2 = booking("term-event-2", "term-res", revenue=1800)
            engine.ingest_booking_request(b2, p)
            assert db.get_approval("term-res")["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value
            return "new reservation.changed event preserved acted-on state"
        record("No terminal-state resurrection", terminal_not_resurrected)

        def missing_data_review():
            live = PropertyProfile("HOSP-X", "Live", max_guests=6, source="hospitable", policy_configured=False)
            b = BookingRequest("live-missing","live-missing","HOSP-X","Guest",date(2026,10,1),date(2026,10,5),4,0,source="hospitable",financials_known=False)
            a = BookingReasoner().analyze(b, live)
            assert a.recommendation == Recommendation.REVIEW
            return "missing live financial/policy data cannot auto-recommend accept/decline"
        record("Missing live data fails safe to REVIEW", missing_data_review)

        def shadow_email_dedupe():
            d2=Database(Path(td)/"shadow.sqlite"); d2.set_setting("notification_recipient","owner@example.com")
            hp=FakeHospitable(); gm=FakeGmail(); svc=ShadowSyncService(d2,EventEngine(d2,BookingReasoner()),hp,gm)
            svc.sync_properties(); lp=d2.list_properties()[0]
            lp.nightly_floor=200; lp.nightly_target=300; lp.nightly_ceiling=600; lp.policy_configured=True; d2.upsert_property(lp)
            first=svc.sync_reservations(True); second=svc.sync_reservations(True)
            assert first["emailed"] == 1 and second["emailed"] == 0 and len(gm.sent) == 1
            return "same live pending event -> one host email"
        record("Live approval-email dedupe", shadow_email_dedupe)

        def provider_reconciliation():
            d3=Database(Path(td)/"reconcile.sqlite"); d3.set_setting("notification_recipient","owner@example.com")
            hp=FakeHospitable(); gm=FakeGmail(); svc=ShadowSyncService(d3,EventEngine(d3,BookingReasoner()),hp,gm)
            svc.sync_properties(); lp=d3.list_properties()[0]
            lp.nightly_floor=200; lp.nightly_target=300; lp.nightly_ceiling=600; lp.policy_configured=True; d3.upsert_property(lp)
            svc.sync_reservations(False)
            hp.status="accepted"; hp.updated="2026-09-22T11:00:00-07:00"; svc.sync_reservations(False)
            assert d3.get_approval("live-r1")["state"] == ApprovalState.CONFIRMED.value
            return "provider accepted status overrides local pending state"
        record("Provider source-of-truth reconciliation", provider_reconciliation)

        def restart_persistence():
            db2 = Database(Path(td) / "lab.sqlite")
            assert db2.get_approval("double-res") is not None
            assert db2.has_event("double-event")
            ok, detail = db2.integrity_check(); assert ok, detail
            return "state survives process/database reopen"
        record("Restart persistence", restart_persistence)

    return results


def main() -> int:
    results = run(); width = max(len(name) for name, _, _ in results)
    print("HOST COMMAND CENTER V0.4 ROBUSTNESS LAB")
    print("=" * 78)
    failed = 0
    for name, ok, detail in results:
        if not ok: failed += 1
        print(f"{'PASS' if ok else 'FAIL':4}  {name:<{width}}  {detail}")
    print("=" * 78)
    print(f"{len(results)-failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
