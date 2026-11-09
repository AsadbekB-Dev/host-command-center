from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, date
from enum import Enum
from typing import Any, Optional


class ApprovalState(str, Enum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    WAITING_PLATFORM_CONFIRMATION = "WAITING_PLATFORM_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    DISMISSED = "DISMISSED"


class Recommendation(str, Enum):
    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    DECLINE = "DECLINE"


@dataclass(slots=True)
class PropertyProfile:
    key: str
    name: str
    address: str = ""
    bedrooms: int = 0
    bathrooms: float = 0.0
    max_guests: int = 0
    nightly_floor: float = 0.0
    nightly_target: float = 0.0
    nightly_ceiling: float = 0.0
    cleaning_cost: float = 0.0
    enabled: bool = True
    hospitable_property_id: str = ""
    source: str = "local"
    policy_configured: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BookingRequest:
    event_id: str
    reservation_id: str
    property_key: str
    guest_name: str
    check_in: date
    check_out: date
    guests: int
    gross_revenue: float
    status: str = "pending"
    source: str = "simulation"
    received_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    platform_url: str = ""
    guest_rating: Optional[float] = None
    guest_review_count: Optional[int] = None
    financials_known: bool = True
    calendar_context_known: bool = True
    response_deadline_known: bool = True

    @property
    def nights(self) -> int:
        return max(0, (self.check_out - self.check_in).days)

    @property
    def adr(self) -> float:
        return self.gross_revenue / self.nights if self.nights else 0.0


@dataclass(slots=True)
class BookingAnalysis:
    recommendation: Recommendation
    confidence: str
    score: int
    reasons: list[str]
    warnings: list[str]
    estimated_net: float
    adr: float
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recommendation"] = self.recommendation.value
        d["created_at"] = self.created_at.isoformat()
        return d
