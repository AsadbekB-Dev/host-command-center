from pathlib import Path

from core.database import Database
from core.secret_store import SecretStore
from core.setup_manager import SetupManager


def test_setup_manager_tracks_recipient_and_webhook(tmp_path: Path):
    app = tmp_path
    (app / "data").mkdir()
    db = Database(app / "data" / "test.db")
    secrets = SecretStore(app / "data" / "secrets.json")
    mgr = SetupManager(app, db, secrets)

    assert mgr.step_status()["email"]["ok"] is False
    mgr.save_recipient("owner@example.com")
    assert db.get_setting("notification_recipient") == "owner@example.com"
    first = mgr.prepare_webhook_secret()
    second = mgr.prepare_webhook_secret()
    assert first == second and len(first) > 20
    assert mgr.step_status()["webhook"]["ok"] is True


def test_hospitable_secret_round_trip(tmp_path: Path):
    store = SecretStore(tmp_path / "secrets.json")
    store.set_secret("hospitable_pat", "pat_1234567890abcdef")
    assert store.get_secret("hospitable_pat") == "pat_1234567890abcdef"
    if store.persistent:
        assert (tmp_path / "secrets.json").exists()
    else:
        assert not (tmp_path / "secrets.json").exists()


def test_import_gmail_oauth_moves_secret_and_deletes_plaintext(tmp_path: Path):
    app = tmp_path
    data = app / "data"
    data.mkdir()
    db = Database(data / "test.db")
    store = SecretStore(data / "secrets.json")
    mgr = SetupManager(app, db, store)
    auth = data / "temp.json"
    auth.write_text('{"client_id":"a","client_secret":"b","refresh_token":"c"}', encoding="utf-8")
    mgr.import_gmail_auth_file(auth)
    assert not auth.exists()
    assert "refresh_token" in store.get_secret("gmail_oauth_json")
    assert mgr.gmail_configured() is True
