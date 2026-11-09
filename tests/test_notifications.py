from pathlib import Path

from providers.mock_email import MockEmailProvider


def test_mock_email(tmp_path: Path):
    p = MockEmailProvider(tmp_path)
    name = p.send("owner@example.com", "Test", "Hello")
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert "owner@example.com" in text
    assert "Hello" in text
