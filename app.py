from pathlib import Path
from core.migration import migrate_previous_runtime_data
from ui.main_window import HostCommandCenter


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    migrated = migrate_previous_runtime_data(root)
    app = HostCommandCenter(root)
    if migrated:
        app.db.log("INFO", "MIGRATION", f"Imported runtime state from {migrated}")
    app.mainloop()
