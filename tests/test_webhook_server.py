from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from backend.webhook_server import WebhookHandler


def _start(tmp_path: Path):
    class Handler(WebhookHandler):
        pass
    Handler.store = tmp_path / "inbox"
    Handler.path_token = "test-token"
    Handler.restrict_hospitable_ip = False
    Handler.max_bytes = 100_000
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_webhook_dedupes_retries(tmp_path: Path):
    server = _start(tmp_path)
    try:
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}/webhooks/hospitable/test-token"
        payload = json.dumps({"action":"reservation.created","data":{"id":"abc"}}).encode()
        results=[]
        for _ in range(2):
            req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                results.append(json.loads(resp.read().decode()))
        assert results[0]["duplicate"] is False
        assert results[1]["duplicate"] is True
        assert len(list((tmp_path/"inbox").glob("*.json"))) == 1
    finally:
        server.shutdown(); server.server_close()


def test_webhook_rejects_wrong_content_type(tmp_path: Path):
    server = _start(tmp_path)
    try:
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}/webhooks/hospitable/test-token"
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type":"text/plain"})
        try:
            urllib.request.urlopen(req, timeout=3)
            raise AssertionError("wrong content type accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 415
    finally:
        server.shutdown(); server.server_close()
