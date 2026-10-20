"""Read a generic credential from Windows Credential Manager (created with cmdkey).

The secret is returned to the caller only; it is never logged or written to disk.
"""
from __future__ import annotations

import ctypes
import os


def read_generic_credential(target: str) -> str | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    ptr = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(ptr)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        cred = ptr.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        advapi32.CredFree(ptr)
    # cmdkey stores the password as UTF-16; fall back to UTF-8 for other tools.
    try:
        return blob.decode("utf-16-le") if len(blob) % 2 == 0 else blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob.decode("utf-8", errors="strict")
