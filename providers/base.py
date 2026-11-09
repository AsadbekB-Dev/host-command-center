from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ReservationProvider(ABC):
    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    @abstractmethod
    def list_properties(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_reservations(self, property_ids: list[str]) -> list[dict[str, Any]]: ...


class NotificationProvider(ABC):
    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    @abstractmethod
    def send(self, recipient: str, subject: str, text_body: str, html_body: str | None = None) -> str: ...
