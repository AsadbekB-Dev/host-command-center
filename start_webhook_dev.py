from pathlib import Path
from core.database import Database
from backend.webhook_server import WebhookHandler
from http.server import ThreadingHTTPServer

root = Path(__file__).resolve().parent
db = Database(root / "data" / "host_command_center.db")
WebhookHandler.path_token = str(db.get_setting("webhook_path_token", "") or "").strip().strip("/")
WebhookHandler.store = root / "data" / "webhook_inbox"
server = ThreadingHTTPServer(("127.0.0.1", 8787), WebhookHandler)
path = "/webhooks/hospitable" + (f"/{WebhookHandler.path_token}" if WebhookHandler.path_token else "")
print(f"LOCAL DEV webhook receiver: http://127.0.0.1:8787{path}")
print("This is localhost-only. Hospitable cannot reach it without a secure tunnel/cloud deployment.")
server.serve_forever()
