from __future__ import annotations

from datetime import datetime
from typing import Iterable

from core.models import BookingAnalysis, BookingRequest, PropertyProfile, Recommendation


class BookingReasoner:
    """Conservative, auditable booking analyzer.

    Shadow Mode uses objective booking/property facts only. Missing financial or
    policy data forces REVIEW rather than inventing confidence.
    """

    def analyze(
        self,
        booking: BookingRequest,
        prop: PropertyProfile,
        blocked_dates: Iterable[str] = (),
        orphan_nights_after: int | None = None,
        orphan_nights_before: int | None = None,
    ) -> BookingAnalysis:
        reasons: list[str] = []
        warnings: list[str] = []
        score = 50
        force_review = False

        if booking.nights <= 0:
            warnings.append("Invalid or zero-night stay")
            score -= 60
        elif booking.nights >= 7:
            reasons.append(f"Useful {booking.nights}-night stay reduces turnover frequency")
            score += 12
        elif booking.nights >= 3:
            reasons.append(f"Solid {booking.nights}-night stay length")
            score += 7
        elif booking.nights == 1:
            warnings.append("One-night stay creates high turnover for limited revenue")
            score -= 8

        if prop.max_guests > 0:
            if booking.guests > prop.max_guests:
                warnings.append(f"Guest count ({booking.guests}) exceeds property capacity ({prop.max_guests})")
                score -= 70
            else:
                reasons.append(f"Guest count is within capacity ({booking.guests}/{prop.max_guests})")
                score += 5
        else:
            warnings.append("Property guest-capacity rule has not been configured yet")
            force_review = True

        adr = booking.adr
        pricing_ready = prop.policy_configured and prop.nightly_target > 0 and prop.nightly_ceiling > 0
        if not booking.financials_known:
            warnings.append("Reservation financials were not available from the provider; verify price manually")
            force_review = True
        elif not pricing_ready:
            warnings.append("Pricing policy is not configured for this live property yet")
            force_review = True
        else:
            if adr < prop.nightly_floor:
                warnings.append(f"ADR ${adr:,.0f} is below configured floor ${prop.nightly_floor:,.0f}")
                score -= 25
            elif adr >= prop.nightly_target:
                reasons.append(f"ADR ${adr:,.0f} meets/exceeds target ${prop.nightly_target:,.0f}")
                score += 15
            else:
                reasons.append(f"ADR ${adr:,.0f} is above floor but below target")
                score += 4

            if adr > prop.nightly_ceiling * 1.25:
                warnings.append("Rate is unusually high versus configured range; verify reservation financials")
                score -= 3

        if not booking.calendar_context_known:
            warnings.append("Full calendar/gap context is not available yet; verify calendar fit manually")
            force_review = True
        elif orphan_nights_before == 1 or orphan_nights_after == 1:
            warnings.append("Booking creates a one-night orphan gap")
            score -= 10
        elif orphan_nights_before and 1 < orphan_nights_before <= 2:
            warnings.append(f"Booking leaves a short {orphan_nights_before}-night gap before arrival")
            score -= 4
        elif orphan_nights_after and 1 < orphan_nights_after <= 2:
            warnings.append(f"Booking leaves a short {orphan_nights_after}-night gap after checkout")
            score -= 4
        else:
            reasons.append("No major calendar-fragmentation warning detected")
            score += 5

        if booking.financials_known and pricing_ready and booking.gross_revenue >= prop.nightly_target * max(booking.nights, 1):
            reasons.append("Gross booking value is healthy for configured property target")
            score += 6

        estimated_net = max(0.0, booking.gross_revenue - prop.cleaning_cost) if booking.financials_known else 0.0

        capacity_violation = any("exceeds property capacity" in w for w in warnings)
        if force_review and not capacity_violation:
            rec = Recommendation.REVIEW
            confidence = "Low"
        elif score >= 72 and not capacity_violation:
            rec = Recommendation.ACCEPT
            confidence = "High" if score >= 85 else "Medium"
        elif score < 25:
            rec = Recommendation.DECLINE
            confidence = "High" if score < 5 else "Medium"
        else:
            rec = Recommendation.REVIEW
            confidence = "Medium"

        return BookingAnalysis(
            recommendation=rec,
            confidence=confidence,
            score=max(0, min(100, score)),
            reasons=reasons,
            warnings=warnings,
            estimated_net=estimated_net,
            adr=adr,
            created_at=datetime.now(),
        )
