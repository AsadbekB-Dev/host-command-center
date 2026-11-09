from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SafetyPolicy:
    safe_mode: bool = True
    allow_live_pricing: bool = False
    allow_guest_message_send: bool = False
    allow_live_booking_action: bool = False

    def require(self, capability: str) -> None:
        allowed = {
            "pricing_write": self.allow_live_pricing,
            "guest_message_send": self.allow_guest_message_send,
            "booking_action": self.allow_live_booking_action,
        }.get(capability, False)
        if self.safe_mode or not allowed:
            raise PermissionError(f"Blocked by Safe Mode: {capability}")
