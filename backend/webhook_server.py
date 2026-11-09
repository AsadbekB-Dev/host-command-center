from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime


HOSPITABLE_NET = ipaddress.ip_network("38.80.170.0/24")


class WebhookHandler(BaseHTTPRequestHandler):
    store = Path("data/webhook_inbox")
    path_token = ""
    max_bytes = 1_000_000
    restrict_hospitable_ip = False

    def _expected_path(self) -> str:
        base = "/webhooks/hospitable"
        return f"{base}/{self.path_token}" if self.path_token else base

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": True, "service": "host-command-webhook", "time": datetime.now().isoformat()})
        else:
            self._json(404, {"ok": False})

    def do_POST(self):
        if self.path.rstrip("/") not in {self._expected_path(), "/hospitable" if not self.path_token else "__disabled__"}:
            self._json(404, {"ok": False, "error": "not_found"})
            return

        if self.restrict_hospitable_ip:
            try:
                client_ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                self._json(403, {"ok": False, "error": "bad_source_ip"})
                return
            if client_ip not in HOSPITABLE_NET:
                self._json(403, {"ok": False, "error": "source_ip_not_allowed"})
                return

        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._json(415, {"ok": False, "error": "content_type_must_be_json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"ok": False, "error": "bad_content_length"})
            return
        if length <= 0 or length > self.max_bytes:
            self._json(413, {"ok": False, "error": "payload_size_invalid"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "json_object_required"})
            return

        self.store.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(raw).hexdigest()
        path = self.store / f"{digest}.json"
        duplicate = path.exists()
        if not duplicate:
            tmp = self.store / f".{digest}.tmp"
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
        # Always 200 after durable storage so Hospitable does not retry a duplicate.
        self._json(200, {"ok": True, "duplicate": duplicate, "id": digest[:16]})

    def log_message(self, fmt, *args):
        print("[webhook]", fmt % args)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--path-token", default="")
    p.add_argument("--store", default="data/webhook_inbox")
    p.add_argument("--max-bytes", type=int, default=1_000_000)
    p.add_argument("--restrict-hospitable-ip", action="store_true")
    args = p.parse_args()
    WebhookHandler.path_token = args.path_token.strip().strip("/")
    WebhookHandler.store = Path(args.store)
    WebhookHandler.max_bytes = max(1024, args.max_bytes)
    WebhookHandler.restrict_hospitable_ip = bool(args.restrict_hospitable_ip)
    server = ThreadingHTTPServer((args.host, args.port), WebhookHandler)
    path = "/webhooks/hospitable" + (f"/{WebhookHandler.path_token}" if WebhookHandler.path_token else "")
    print(f"Hospitable webhook receiver: http://{args.host}:{args.port}{path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
