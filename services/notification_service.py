from __future__ import annotations

import json
from datetime import datetime
from html import escape

from core.database import Database
from providers.base import NotificationProvider


class NotificationService:
    def __init__(self, db: Database, provider: NotificationProvider):
        self.db = db
        self.provider = provider

    def send_approval_email(self, approval: dict, recipient: str) -> str:
        analysis = json.loads(approval.get("analysis_json") or "{}")
        rec = analysis.get("recommendation", approval.get("recommendation", "REVIEW"))
        reasons = analysis.get("reasons", [])
        warnings = analysis.get("warnings", [])
        nights = (datetime.fromisoformat(approval["check_out"]) - datetime.fromisoformat(approval["check_in"])).days
        adr = float(approval["gross_revenue"]) / nights if nights else 0.0
        subject = f"{self._emoji(rec)} Booking Approval — {approval['property_key']} — ${approval['gross_revenue']:,.0f}"
        lines = [
            "NEW BOOKING REQUEST",
            "",
            f"Property: {approval['property_key']}",
            f"Guest: {approval['guest_name']}",
            f"Dates: {approval['check_in']} to {approval['check_out']} ({nights} nights)",
            f"Guests: {approval['guests']}",
            f"Gross revenue: ${approval['gross_revenue']:,.2f}",
            f"ADR: ${adr:,.2f}",
            "",
            f"Recommendation: {rec}",
            f"Score: {analysis.get('score', approval.get('score', ''))}/100",
        ]
        if reasons:
            lines += ["", "Reasons:"] + [f"- {r}" for r in reasons]
        if warnings:
            lines += ["", "Warnings:"] + [f"- {w}" for w in warnings]
        lines += ["", "Open Host Command Center to review and record your decision."]
        text = "\n".join(lines)
        html = "<h2>New Booking Request</h2>" + "".join(f"<p>{escape(line)}</p>" for line in lines[2:] if line)
        msg_id = self.provider.send(recipient, subject, text, html)
        self.db.log("INFO", "EMAIL", f"Approval email sent to {recipient}", approval["reservation_id"], {"message_id": msg_id})
        return msg_id

    @staticmethod
    def _emoji(rec: str) -> str:
        return {"ACCEPT": "🟢", "REVIEW": "🟡", "DECLINE": "🔴"}.get(rec, "🟡")
