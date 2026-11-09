from __future__ import annotations

from core.models import ApprovalState


_ALLOWED: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.NEW: {ApprovalState.ANALYZING, ApprovalState.DISMISSED},
    ApprovalState.ANALYZING: {ApprovalState.PENDING_APPROVAL, ApprovalState.DISMISSED},
    ApprovalState.PENDING_APPROVAL: {
        ApprovalState.APPROVED,
        ApprovalState.DECLINED,
        ApprovalState.EXPIRED,
        ApprovalState.DISMISSED,
    },
    ApprovalState.APPROVED: {ApprovalState.WAITING_PLATFORM_CONFIRMATION, ApprovalState.DISMISSED},
    ApprovalState.WAITING_PLATFORM_CONFIRMATION: {
        ApprovalState.CONFIRMED,
        ApprovalState.EXPIRED,
        ApprovalState.DISMISSED,
    },
    ApprovalState.DECLINED: {ApprovalState.DISMISSED},
    ApprovalState.CONFIRMED: {ApprovalState.DISMISSED},
    ApprovalState.NOT_ACCEPTED: {ApprovalState.DISMISSED},
    ApprovalState.CANCELLED: {ApprovalState.DISMISSED},
    ApprovalState.EXPIRED: {ApprovalState.DISMISSED},
    ApprovalState.DISMISSED: set(),
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: ApprovalState, target: ApprovalState) -> bool:
    return target in _ALLOWED.get(current, set())


def require_transition(current: ApprovalState, target: ApprovalState) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"Invalid approval transition: {current.value} -> {target.value}")
