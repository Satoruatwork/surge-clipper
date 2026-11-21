"""Encrypt secrets at rest with Windows DPAPI (bound to this Windows user). Other OSes: base64 only (dev use)."""
from __future__ import annotations

import base64
import ctypes
import sys

_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _ENTROPY = b"surge-clip-engine"

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes) -> bytes:
        inp, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def _protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data)

    def _unprotect(data: bytes) -> bytes:
        # CryptUnprotectData has an extra ppszDataDescr out-param in position 2
        inp, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        if not _crypt32.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x01,
                                           ctypes.byref(out)):
            raise OSError("DPAPI unprotect failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)
else:  # pragma: no cover
    def _protect(data: bytes) -> bytes:
        return data

    def _unprotect(data: bytes) -> bytes:
        return data


def seal(text: str) -> str:
    return "dpapi:" + base64.b64encode(_protect(text.encode("utf-8"))).decode("ascii")


def unseal(token: str) -> str:
    if not token:
        return ""
    if not token.startswith("dpapi:"):
        return token
    try:
        return _unprotect(base64.b64decode(token[6:])).decode("utf-8")
    except (OSError, ValueError):
        return ""
