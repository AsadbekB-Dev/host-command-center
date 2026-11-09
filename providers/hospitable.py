from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from providers.base import ReservationProvider


class HospitableProvider(ReservationProvider):
    """Read-first client for Hospitable Public API v2.

    Live reservation/property/message reads are enabled. Booking accept/decline
    and pricing/calendar writes are intentionally absent from this class in
    Shadow Mode.
    """

    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, token: str = "", base_url: str = "https://public.api.hospitable.com/v2", timeout: int = 20, max_get_attempts: int = 3):
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_get_attempts = max(1, max_get_attempts)

    def _request(self, method: str, path: str, query: list[tuple[str, str]] | None = None, payload: dict | None = None) -> Any:
        if not self.token:
            raise RuntimeError("Hospitable token is not configured")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        attempts = self.max_get_attempts if method.upper() == "GET" else 1
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "HostCommandCenter/0.4",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1200]
                last_exc = RuntimeError(f"Hospitable HTTP {exc.code}: {detail}")
                if exc.code not in self.RETRYABLE or attempt >= attempts:
                    raise last_exc from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(10.0, float(retry_after)) if retry_after else min(4.0, 0.5 * (2 ** (attempt - 1)))
                except ValueError:
                    delay = min(4.0, 0.5 * (2 ** (attempt - 1)))
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last_exc = RuntimeError(f"Hospitable connection failed: {exc.reason}")
                if attempt >= attempts:
                    raise last_exc from exc
                time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
        raise last_exc or RuntimeError("Hospitable request failed")

    @staticmethod
    def _data_list(obj: Any) -> list[dict[str, Any]]:
        if isinstance(obj, dict) and isinstance(obj.get("data"), list):
            return obj["data"]
        if isinstance(obj, list):
            return obj
        return []

    def _get_all_pages(self, path: str, query: list[tuple[str, str]] | None = None, max_pages: int = 20) -> list[dict[str, Any]]:
        """Best-effort pagination supporting common links/meta page shapes.

        For small accounts this is one request. If the provider exposes a next
        link/current_page+last_page, subsequent pages are followed. The hard cap
        prevents accidental infinite loops if upstream metadata is malformed.
        """
        base_query = list(query or [])
        if not any(k == "per_page" for k, _ in base_query):
            base_query.append(("per_page", "100"))
        page = 1
        out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        while page <= max_pages:
            q = [(k, v) for k, v in base_query if k != "page"] + [("page", str(page))]
            obj = self._request("GET", path, query=q)
            rows = self._data_list(obj)
            for row in rows:
                rid = str(row.get("id") or row.get("uuid") or row.get("code") or json.dumps(row, sort_keys=True, default=str))
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    out.append(row)
            if not rows:
                break

            meta = obj.get("meta", {}) if isinstance(obj, dict) else {}
            links = obj.get("links", {}) if isinstance(obj, dict) else {}
            current = meta.get("current_page") or meta.get("currentPage") or page
            last = meta.get("last_page") or meta.get("lastPage")
            next_link = links.get("next") if isinstance(links, dict) else None
            try:
                if last is not None and int(current) >= int(last):
                    break
            except (TypeError, ValueError):
                pass
            if last is None and not next_link and len(rows) < 100:
                break
            page += 1
        return out

    def health(self) -> tuple[bool, str]:
        if not self.token:
            return False, "Not connected"
        try:
            props = self.list_properties()
            return True, f"Connected — {len(props)} properties returned"
        except Exception as exc:
            return False, str(exc)

    def list_properties(self) -> list[dict[str, Any]]:
        return self._get_all_pages("/properties")

    def list_reservations(self, property_ids: list[str]) -> list[dict[str, Any]]:
        if not property_ids:
            return []
        query: list[tuple[str, str]] = [("properties[]", p) for p in property_ids]
        query += [("include", "financials,guest,properties,listings")]
        return self._get_all_pages("/reservations", query=query)

    def get_reservation(self, reservation_id: str) -> dict[str, Any]:
        obj = self._request("GET", f"/reservations/{urllib.parse.quote(reservation_id)}", query=[("include", "financials,guest,properties,listings,review")])
        return obj.get("data", obj) if isinstance(obj, dict) else {}

    def list_messages(self, reservation_id: str) -> list[dict[str, Any]]:
        return self._get_all_pages(f"/reservations/{urllib.parse.quote(reservation_id)}/messages", max_pages=10)
