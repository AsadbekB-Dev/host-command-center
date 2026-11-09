from __future__ import annotations

import shutil
from pathlib import Path


PREVIOUS_DIRS = ["Host_Command_Center_v0_3_1", "Host_Command_Center_v0_3", "Host_Command_Center_v0_2", "Host_Command_Center_v0_1"]


def migrate_previous_runtime_data(app_dir: Path) -> str | None:
    """Copy user runtime state from a sibling older build on first launch.

    DPAPI-protected secrets remain usable because the ciphertext is still tied
    to the same Windows user. Package defaults are never copied backward.
    """
    app_dir = Path(app_dir).resolve()
    data_dir = app_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # If this build already has user state, do not overwrite it.
    if (data_dir / "host_command_center.db").exists() or (data_dir / "secrets.json").exists():
        return None

    for name in PREVIOUS_DIRS:
        old = app_dir.parent / name / "data"
        if not old.exists() or old.resolve() == data_dir.resolve():
            continue
        copied = False
        for filename in ("host_command_center.db", "host_command_center.db-wal", "host_command_center.db-shm", "secrets.json"):
            src = old / filename
            if src.exists():
                shutil.copy2(src, data_dir / filename)
                copied = True
        # Preserve queued webhook events if any; they will be reconciled safely.
        old_inbox = old / "webhook_inbox"
        new_inbox = data_dir / "webhook_inbox"
        if old_inbox.exists():
            new_inbox.mkdir(parents=True, exist_ok=True)
            for src in old_inbox.glob("*.json"):
                dst = new_inbox / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)
                    copied = True
        if copied:
            return name
    return None
