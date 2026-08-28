"""Guardado de tokens cifrados con DPAPI (solo los descifra tu usuario de Windows)."""
from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
from typing import Any

from .paths import state_file, tokens_file


# --------------------------------------------------------------------------
# DPAPI: CryptProtectData / CryptUnprotectData
# --------------------------------------------------------------------------
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DataBlob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return out


CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "SpotifyTidalSync tokens"


def dpapi_encrypt(data: bytes) -> bytes:
    src, dst = _blob(data), _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src), _DESCRIPTION, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst),
    )
    if not ok:
        raise OSError("CryptProtectData fallo: %s" % ctypes.GetLastError())
    return _blob_bytes(dst)


def dpapi_decrypt(data: bytes) -> bytes:
    src, dst = _blob(data), _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst),
    )
    if not ok:
        raise OSError("CryptUnprotectData fallo: %s" % ctypes.GetLastError())
    return _blob_bytes(dst)


# --------------------------------------------------------------------------
# Almacen de tokens
# --------------------------------------------------------------------------
class TokenStore:
    """{"spotify": {...}, "tidal": {...}} cifrado en disco."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        path = tokens_file()
        if not path.exists():
            return {}
        try:
            return json.loads(dpapi_decrypt(path.read_bytes()).decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            # Perfil distinto, fichero corrupto o maquina distinta: hay que volver a loguearse.
            return {}

    def _write(self) -> None:
        raw = json.dumps(self._data, ensure_ascii=False).encode("utf-8")
        tokens_file().write_bytes(dpapi_encrypt(raw))

    def get(self, service: str) -> dict[str, Any] | None:
        return self._data.get(service)

    def save(self, service: str, token: dict[str, Any]) -> None:
        token = dict(token)
        if "expires_in" in token and "expires_at" not in token:
            token["expires_at"] = time.time() + float(token["expires_in"]) - 60
        # Un refresh sin refresh_token nuevo debe conservar el anterior.
        old = self._data.get(service) or {}
        if not token.get("refresh_token") and old.get("refresh_token"):
            token["refresh_token"] = old["refresh_token"]
        self._data[service] = token
        self._write()

    def clear(self, service: str) -> None:
        self._data.pop(service, None)
        self._write()

    def has(self, service: str) -> bool:
        return bool(self._data.get(service, {}).get("access_token"))


# --------------------------------------------------------------------------
# Estado de sincronizacion (snapshots para detectar borrados)
# --------------------------------------------------------------------------
class StateStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = self._read()

    @staticmethod
    def _read() -> dict[str, Any]:
        path = state_file()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        state_file().write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def snapshot(self, key: str) -> set[str]:
        return set(self.data.get("snapshots", {}).get(key, []))

    def set_snapshot(self, key: str, values: set[str]) -> None:
        self.data.setdefault("snapshots", {})[key] = sorted(values)

    @property
    def last_sync(self) -> str | None:
        return self.data.get("last_sync")

    @last_sync.setter
    def last_sync(self, value: str) -> None:
        self.data["last_sync"] = value
