from pathlib import Path
from core.migration import migrate_previous_runtime_data


def test_previous_version_runtime_migration(tmp_path: Path):
    old = tmp_path / "Host_Command_Center_v0_3" / "data"
    old.mkdir(parents=True)
    (old / "host_command_center.db").write_bytes(b"db")
    (old / "secrets.json").write_text('{"x":"encrypted"}')
    new = tmp_path / "Host_Command_Center_v0_4"
    new.mkdir()
    got = migrate_previous_runtime_data(new)
    assert got == "Host_Command_Center_v0_3"
    assert (new / "data" / "host_command_center.db").read_bytes() == b"db"
    assert (new / "data" / "secrets.json").exists()


def test_migration_never_overwrites_existing_runtime(tmp_path: Path):
    old = tmp_path / "Host_Command_Center_v0_3" / "data"; old.mkdir(parents=True)
    (old / "secrets.json").write_text("old")
    new_data = tmp_path / "Host_Command_Center_v0_4" / "data"; new_data.mkdir(parents=True)
    (new_data / "secrets.json").write_text("new")
    assert migrate_previous_runtime_data(tmp_path / "Host_Command_Center_v0_4") is None
    assert (new_data / "secrets.json").read_text() == "new"
