"""Per-user Windows identity, encrypted with DPAPI (never persisted in plaintext)."""

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Agent 身份只支持 Windows 当前用户 DPAPI")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), buffer)
    target = _Blob()
    name = "CryptUnprotectData" if decrypt else "CryptProtectData"
    function = getattr(crypt, name)
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_identity(directory: Path) -> tuple[str, Ed25519PrivateKey]:
    path = directory / "identity.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        machine_id = str(uuid.UUID(value["machine_id"]))
        key = Ed25519PrivateKey.from_private_bytes(_dpapi(base64.b64decode(value["private_key"]), decrypt=True))
        return machine_id, key
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    machine_id = str(uuid.uuid4())
    encrypted = _dpapi(raw)
    save_json(path, {"machine_id": machine_id, "private_key": base64.b64encode(encrypted).decode("ascii")})
    return machine_id, key


def public_key(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
