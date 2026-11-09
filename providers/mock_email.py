from __future__ import annotations

from pathlib import Path
from datetime import datetime

from providers.base import NotificationProvider


class MockEmailProvider(NotificationProvider):
    def __init__(self, outbox_dir: str | Path):
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def health(self) -> tuple[bool, str]:
        return True, "Simulation"

    def send(self, recipient: str, subject: str, text_body: str, html_body: str | None = None) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.outbox_dir / f"{stamp}.txt"
        path.write_text(f"TO: {recipient}\nSUBJECT: {subject}\n\n{text_body}\n", encoding="utf-8")
        return path.name
