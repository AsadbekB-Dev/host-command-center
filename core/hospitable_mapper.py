from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from core.models import BookingRequest, PropertyProfile


def _dig(obj: Any, *path: str, default=None):
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _first(obj: dict[str, Any], paths: list[tuple[str, ...]], default=None):
    for p in paths:
        val = _dig(obj, *p, default=None)
        if val not in (None, "", []):
            return val
    return default


def _num(val: Any, default: float = 0.0) -> float:
    try:
        if isinstance(val, dict):
            val = val.get("amount", val.get("value", default))
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _date(val: Any) -> date | None:
    if not val:
        return None
    s = str(val).strip()
    for candidate in (s, s[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    return None


def _dt(val: Any) -> datetime | None:
    if not val:
        return None
    s = str(val).strip().replace("Z", "+00:00")
    try:
        out = datetime.fromisoformat(s)
        if out.tzinfo is not None:
            out = out.astimezone().replace(tzinfo=None)
        return out
    except ValueError:
        return None


def property_profile_from_remote(row: dict[str, Any], existing: PropertyProfile | None = None) -> PropertyProfile:
    rid = str(row.get("id") or row.get("uuid") or row.get("property_id") or "").strip()
    if not rid:
        raise ValueError("Remote property is missing an id")

    name = str(_first(row, [("name",), ("public_name",), ("listing_name",)], f"Hospitable {rid[:8]}"))
    address_obj = row.get("address") if isinstance(row.get("address"), dict) else {}
    address_parts = [
        address_obj.get("street"), address_obj.get("line1"), address_obj.get("city"),
        address_obj.get("state"), address_obj.get("province"), address_obj.get("postal_code"),
    ]
    address = ", ".join(str(x) for x in address_parts if x)
    if not address:
        address = str(_first(row, [("address", "formatted"), ("formatted_address",), ("location", "address")], ""))

    bedrooms = _int(_first(row, [("bedrooms",), ("bedroom_count",), ("capacity", "bedrooms")], 0))
    bathrooms = _num(_first(row, [("bathrooms",), ("bathroom_count",), ("capacity", "bathrooms")], 0))
    max_guests = _int(_first(row, [("max_guests",), ("guest_capacity",), ("capacity", "guests"), ("capacity", "max_guests")], 0))

    # Existing automation policy is retained across provider refreshes.
    if existing:
        floor, target, ceiling = existing.nightly_floor, existing.nightly_target, existing.nightly_ceiling
        cleaning = existing.cleaning_cost
        policy_configured = existing.policy_configured
        enabled = existing.enabled
    else:
        floor = target = ceiling = cleaning = 0.0
        policy_configured = False
        enabled = True

    return PropertyProfile(
        key=f"HOSP-{rid}",
        name=name,
        address=address,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        max_guests=max_guests,
        nightly_floor=floor,
        nightly_target=target,
        nightly_ceiling=ceiling,
        cleaning_cost=cleaning,
        enabled=enabled,
        hospitable_property_id=rid,
        source="hospitable",
        policy_configured=policy_configured,
    )


def _property_id(row: dict[str, Any]) -> str:
    direct = _first(row, [("property_id",), ("property", "id"), ("property", "uuid")], "")
    if direct:
        return str(direct)
    props = row.get("properties")
    if isinstance(props, list) and props:
        first = props[0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("uuid") or "")
        return str(first)
    return ""


def _guest_name(row: dict[str, Any]) -> str:
    guest = row.get("guest") if isinstance(row.get("guest"), dict) else {}
    name = guest.get("name") or row.get("guest_name")
    if name:
        return str(name)
    first = guest.get("first_name") or row.get("guest_first_name") or ""
    last = guest.get("last_name") or row.get("guest_last_name") or ""
    return (f"{first} {last}").strip() or "Guest"


def _financial_value(row: dict[str, Any]) -> tuple[float, bool]:
    fin = row.get("financials") if isinstance(row.get("financials"), dict) else {}
    candidates = [
        fin.get("revenue"), fin.get("total_price"), fin.get("total"), fin.get("payout"),
        fin.get("accommodation"), fin.get("subtotal"),
        row.get("revenue"), row.get("total_price"), row.get("total"), row.get("payout"), row.get("gross_revenue"),
    ]
    for val in candidates:
        if val not in (None, ""):
            return _num(val), True
    return 0.0, False


def reservation_status(row: dict[str, Any]) -> str:
    raw = str(row.get("status") or _dig(row, "reservation", "status", default="") or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    aliases = {
        "request": "pending", "requested": "pending", "request_to_book": "pending",
        "pending_verification": "checkpoint", "confirmed": "accepted", "booked": "accepted",
        "notaccepted": "not_accepted", "declined": "not_accepted", "rejected": "not_accepted",
        "canceled": "cancelled",
    }
    return aliases.get(raw, raw or "unknown")


def booking_from_remote(row: dict[str, Any], prop: PropertyProfile) -> BookingRequest:
    rid = str(row.get("id") or row.get("uuid") or row.get("reservation_id") or row.get("code") or "").strip()
    if not rid:
        raise ValueError("Remote reservation is missing an id")
    check_in = _date(_first(row, [("check_in",), ("checkin",), ("checkin_date",), ("arrival_date",), ("arrival",)]))
    check_out = _date(_first(row, [("check_out",), ("checkout",), ("checkout_date",), ("departure_date",), ("departure",)]))
    if not check_in or not check_out:
        raise ValueError(f"Reservation {rid} is missing check-in/check-out dates")

    guests = _int(_first(row, [("guest_count",), ("guests", "total"), ("guests", "count"), ("number_of_guests",)], 1), 1)
    gross, known = _financial_value(row)
    status = reservation_status(row)
    created_raw = _first(row, [("created_at",), ("requested_at",), ("booked_at",)], None)
    parsed_created = _dt(created_raw)
    created = parsed_created or datetime.now()
    explicit_deadline = _dt(_first(row, [("expires_at",), ("response_deadline",), ("deadline",)], None))
    deadline_known = explicit_deadline is not None
    updated = str(_first(row, [("updated_at",), ("modified_at",), ("last_updated_at",)], ""))

    stable = {
        "id": rid,
        "status": status,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "guests": guests,
        "gross": gross if known else None,
        "updated": updated,
    }
    event_hash = hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    expires = explicit_deadline if status == "pending" else None

    return BookingRequest(
        event_id=f"hosp-{rid}-{event_hash}",
        reservation_id=rid,
        property_key=prop.key,
        guest_name=_guest_name(row),
        check_in=check_in,
        check_out=check_out,
        guests=max(1, guests),
        gross_revenue=max(0.0, gross),
        status=status,
        source="hospitable",
        received_at=created,
        expires_at=expires,
        platform_url="https://my.hospitable.com/",
        financials_known=known,
        calendar_context_known=False,
        response_deadline_known=deadline_known,
    )


def reservation_property_id(row: dict[str, Any]) -> str:
    return _property_id(row)


def reservation_financial_value(row: dict[str, Any]) -> tuple[float, bool]:
    return _financial_value(row)
