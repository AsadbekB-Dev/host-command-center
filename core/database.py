from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from core.models import ApprovalState, BookingAnalysis, BookingRequest, PropertyProfile


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS properties (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_properties (
                    remote_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_reservations (
                    remote_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    status TEXT,
                    property_id TEXT,
                    payload TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remote_messages (
                    remote_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    reservation_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    property_key TEXT NOT NULL,
                    guest_name TEXT NOT NULL,
                    check_in TEXT NOT NULL,
                    check_out TEXT NOT NULL,
                    guests INTEGER NOT NULL,
                    gross_revenue REAL NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state TEXT NOT NULL,
                    recommendation TEXT,
                    score INTEGER,
                    analysis_json TEXT,
                    received_at TEXT,
                    expires_at TEXT,
                    platform_url TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_claims (
                    kind TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    PRIMARY KEY(kind,reservation_id,event_id)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reservation_id TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def integrity_check(self) -> tuple[bool, str]:
        with self.connect() as con:
            row = con.execute("PRAGMA integrity_check").fetchone()
        detail = str(row[0]) if row else "unknown"
        return detail.lower() == "ok", detail

    def upsert_property(self, prop: PropertyProfile) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO properties(key,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (prop.key, json.dumps(prop.to_dict()), datetime.now().isoformat()),
            )

    def list_properties(self) -> list[PropertyProfile]:
        with self.connect() as con:
            rows = con.execute("SELECT payload FROM properties ORDER BY key").fetchall()
        out: list[PropertyProfile] = []
        for r in rows:
            data = json.loads(r["payload"])
            # Forward compatibility for profiles created by older builds.
            data.setdefault("source", "local")
            data.setdefault("policy_configured", True)
            out.append(PropertyProfile(**data))
        return out

    def save_remote_properties(self, provider: str, rows: list[dict[str, Any]]) -> int:
        count = 0
        now = datetime.now().isoformat()
        with self.connect() as con:
            for row in rows:
                rid = str(row.get("id") or row.get("uuid") or row.get("property_id") or "").strip()
                if not rid:
                    continue
                con.execute(
                    "INSERT INTO remote_properties(remote_id,provider,payload,synced_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(remote_id) DO UPDATE SET provider=excluded.provider,payload=excluded.payload,synced_at=excluded.synced_at",
                    (rid, provider, json.dumps(row), now),
                )
                count += 1
        return count

    def list_remote_properties(self, provider: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT remote_id,provider,payload,synced_at FROM remote_properties"
        params: tuple[Any, ...] = ()
        if provider:
            q += " WHERE provider=?"
            params = (provider,)
        q += " ORDER BY synced_at DESC,remote_id"
        with self.connect() as con:
            rows = con.execute(q, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            payload = json.loads(r["payload"])
            payload["_remote_id"] = r["remote_id"]
            payload["_provider"] = r["provider"]
            payload["_synced_at"] = r["synced_at"]
            out.append(payload)
        return out

    def save_remote_reservations(self, provider: str, rows: list[dict[str, Any]]) -> int:
        from core.hospitable_mapper import reservation_property_id, reservation_status
        count = 0
        now = datetime.now().isoformat()
        with self.connect() as con:
            for row in rows:
                rid = str(row.get("id") or row.get("uuid") or row.get("reservation_id") or row.get("code") or "").strip()
                if not rid:
                    continue
                con.execute(
                    "INSERT INTO remote_reservations(remote_id,provider,status,property_id,payload,synced_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(remote_id) DO UPDATE SET provider=excluded.provider,status=excluded.status,property_id=excluded.property_id,payload=excluded.payload,synced_at=excluded.synced_at",
                    (rid, provider, reservation_status(row), reservation_property_id(row), json.dumps(row), now),
                )
                count += 1
        return count

    def list_remote_reservations(self, provider: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        q = "SELECT remote_id,provider,status,property_id,payload,synced_at FROM remote_reservations"
        params: list[Any] = []
        if provider:
            q += " WHERE provider=?"
            params.append(provider)
        q += " ORDER BY synced_at DESC,remote_id LIMIT ?"
        params.append(limit)
        with self.connect() as con:
            rows = con.execute(q, tuple(params)).fetchall()
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            payload.update({"_remote_id": r["remote_id"], "_provider": r["provider"], "_status": r["status"], "_property_id": r["property_id"], "_synced_at": r["synced_at"]})
            out.append(payload)
        return out

    def save_remote_messages(self, provider: str, reservation_id: str, rows: list[dict[str, Any]]) -> int:
        count = 0
        now = datetime.now().isoformat()
        with self.connect() as con:
            for i, row in enumerate(rows):
                rid = str(row.get("id") or row.get("uuid") or row.get("message_id") or f"{reservation_id}:{i}:{hash(json.dumps(row, sort_keys=True, default=str))}")
                con.execute(
                    "INSERT INTO remote_messages(remote_id,reservation_id,provider,payload,synced_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(remote_id) DO UPDATE SET reservation_id=excluded.reservation_id,provider=excluded.provider,payload=excluded.payload,synced_at=excluded.synced_at",
                    (rid, reservation_id, provider, json.dumps(row), now),
                )
                count += 1
        return count

    def list_remote_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT remote_id,reservation_id,provider,payload,synced_at FROM remote_messages ORDER BY synced_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            payload.update({"_remote_id": r["remote_id"], "_reservation_id": r["reservation_id"], "_provider": r["provider"], "_synced_at": r["synced_at"]})
            out.append(payload)
        return out

    def has_event(self, event_id: str) -> bool:
        with self.connect() as con:
            return con.execute("SELECT 1 FROM processed_events WHERE event_id=?", (event_id,)).fetchone() is not None

    def claim_event(self, event_id: str) -> bool:
        if not event_id.strip():
            raise ValueError("event_id is required")
        with self.connect() as con:
            cur = con.execute("INSERT OR IGNORE INTO processed_events(event_id,processed_at) VALUES(?,?)", (event_id, datetime.now().isoformat()))
            return cur.rowcount == 1

    def mark_event(self, event_id: str) -> None:
        self.claim_event(event_id)

    def claim_notification(self, kind: str, reservation_id: str, event_id: str) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO notification_claims(kind,reservation_id,event_id,claimed_at) VALUES(?,?,?,?)",
                (kind, reservation_id, event_id, datetime.now().isoformat()),
            )
            return cur.rowcount == 1

    def release_notification(self, kind: str, reservation_id: str, event_id: str) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM notification_claims WHERE kind=? AND reservation_id=? AND event_id=?", (kind, reservation_id, event_id))

    def save_approval(self, booking: BookingRequest, analysis: BookingAnalysis, state: ApprovalState) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO approvals(
                    reservation_id,event_id,property_key,guest_name,check_in,check_out,guests,
                    gross_revenue,source,status,state,recommendation,score,analysis_json,received_at,
                    expires_at,platform_url,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reservation_id) DO UPDATE SET
                    event_id=excluded.event_id, property_key=excluded.property_key,
                    guest_name=excluded.guest_name, check_in=excluded.check_in,
                    check_out=excluded.check_out, guests=excluded.guests,
                    gross_revenue=excluded.gross_revenue, source=excluded.source,
                    status=excluded.status, state=excluded.state,
                    recommendation=excluded.recommendation, score=excluded.score,
                    analysis_json=excluded.analysis_json, received_at=excluded.received_at,
                    expires_at=excluded.expires_at, platform_url=excluded.platform_url,
                    updated_at=excluded.updated_at
                """,
                (
                    booking.reservation_id, booking.event_id, booking.property_key, booking.guest_name,
                    booking.check_in.isoformat(), booking.check_out.isoformat(), booking.guests,
                    booking.gross_revenue, booking.source, booking.status, state.value,
                    analysis.recommendation.value, analysis.score, json.dumps(analysis.to_dict()),
                    booking.received_at.isoformat() if booking.received_at else None,
                    booking.expires_at.isoformat() if booking.expires_at else None,
                    booking.platform_url, datetime.now().isoformat(),
                ),
            )

    def update_state(self, reservation_id: str, state: ApprovalState) -> None:
        with self.connect() as con:
            cur = con.execute("UPDATE approvals SET state=?,updated_at=? WHERE reservation_id=?", (state.value, datetime.now().isoformat(), reservation_id))
            if cur.rowcount != 1:
                raise KeyError(f"Reservation not found: {reservation_id}")

    def get_approval(self, reservation_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM approvals WHERE reservation_id=?", (reservation_id,)).fetchone()
        return dict(row) if row else None

    def list_approvals(self, include_dismissed: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM approvals"
        params: tuple[Any, ...] = ()
        if not include_dismissed:
            q += " WHERE state != ?"
            params = (ApprovalState.DISMISSED.value,)
        q += " ORDER BY COALESCE(received_at,updated_at) DESC"
        with self.connect() as con:
            rows = con.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def expire_overdue_approvals(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        with self.connect() as con:
            cur = con.execute(
                """UPDATE approvals SET state=?,updated_at=? WHERE state=? AND expires_at IS NOT NULL AND expires_at <= ?""",
                (ApprovalState.EXPIRED.value, now.isoformat(), ApprovalState.PENDING_APPROVAL.value, now.isoformat()),
            )
            count = cur.rowcount
        if count:
            self.log("WARN", "SAFETY", f"Expired {count} overdue approval(s)")
        return count

    def log(self, level: str, category: str, message: str, reservation_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO audit_log(created_at,level,category,message,reservation_id,details_json) VALUES(?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), level, category, message, reservation_id, json.dumps(details or {})),
            )

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_setting(self, key: str, value: Any) -> None:
        raw = json.dumps(value)
        with self.connect() as con:
            con.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, raw, datetime.now().isoformat()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
