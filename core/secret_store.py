from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


class SecretStore:
    """Small secret store.

    On Windows, values are encrypted with the current user's DPAPI key before
    being written to disk. On non-Windows hosts (used only for development and
    automated tests), secrets remain memory-only and are never persisted.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._memory: dict[str, str] = {}

    @property
    def persistent(self) -> bool:
        return os.name == "nt"

    def set_secret(self, key: str, value: str) -> None:
        value = value.strip()
        if not value:
            self.delete_secret(key)
            return
        if not self.persistent:
            self._memory[key] = value
            return
        data = self._load_file()
        data[key] = self._protect(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_secret(self, key: str, default: str = "") -> str:
        if not self.persistent:
            return self._memory.get(key, default)
        data = self._load_file()
        raw = data.get(key)
        if not raw:
            return default
        try:
            return self._unprotect(raw)
        except Exception:
            return default

    def delete_secret(self, key: str) -> None:
        self._memory.pop(key, None)
        if not self.persistent:
            return
        data = self._load_file()
        if key in data:
            del data[key]
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_file(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _protect(value: str) -> str:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = value.encode("utf-8")
        buf = ctypes.create_string_buffer(raw)
        in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), "Host Command Center", None, None, None, 0, ctypes.byref(out_blob)):
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return base64.b64encode(encrypted).decode("ascii")
        finally:
            kernel32.LocalFree(out_blob.pbData)

    @staticmethod
    def _unprotect(value: str) -> str:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        enc = base64.b64decode(value)
        buf = ctypes.create_string_buffer(enc)
        in_blob = DATA_BLOB(len(enc), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise ctypes.WinError()
        try:
            raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return raw.decode("utf-8")
        finally:
            kernel32.LocalFree(out_blob.pbData)
