from __future__ import annotations

import os
import stat
from pathlib import Path, PureWindowsPath
from typing import BinaryIO
from urllib.parse import quote

from starlette.responses import StreamingResponse

from .config import EXECUTIONS_DIR


TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}
MAX_FILES = 200
MAX_ENTRIES = 2000
MAX_DEPTH = 8
MAX_PATH_CHARS = 1024
CHUNK_BYTES = 64 * 1024
UNAVAILABLE_MESSAGE = "部分结果文件暂时无法读取"
_REPARSE_POINT = 0x400
_HIDDEN = 0x2


def _valid_name(name: str) -> bool:
    return bool(name) and not (
        name in {".", ".."}
        or name.startswith((".", "~$"))
        or name.lower().endswith((".tmp", ".part", ".partial"))
        or name.endswith((".", " "))
        or any(ord(char) < 32 or char in '<>:"\\|?*\x7f' for char in name)
        or PureWindowsPath(name).is_reserved()
    )


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value) > MAX_PATH_CHARS:
        raise ValueError("文件路径无效")
    parts = tuple(value.split("/"))
    if len(parts) > MAX_DEPTH or not all(_valid_name(part) for part in parts):
        raise ValueError("文件路径无效")
    return parts


def _root(execution_id: int) -> Path:
    if type(execution_id) is not int or execution_id < 1:
        raise ValueError("执行记录编号无效")
    return Path(os.path.abspath(EXECUTIONS_DIR)) / str(execution_id) / "artifacts"


def _safe_info(info: os.stat_result, *, hidden: bool = True) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return not (
        stat.S_ISLNK(info.st_mode)
        or attributes & _REPARSE_POINT
        or (hidden and attributes & _HIDDEN)
    )


def _checked_path(
    root: Path, parts: tuple[str, ...], *, directory: bool = False
) -> tuple[Path, os.stat_result]:
    # Check parents before resolving: resolve() alone would conceal junctions.
    for parent in (*reversed(root.parents), root):
        info = parent.lstat()
        if not _safe_info(info, hidden=False) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("执行资料目录无效")
    candidate = root
    info = root.lstat()
    for index, part in enumerate(parts):
        if not _valid_name(part):
            raise ValueError("文件路径无效")
        candidate = candidate / part
        info = candidate.lstat()
        if not _safe_info(info):
            raise ValueError("文件不可用")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise FileNotFoundError("文件不存在")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("目录不可用")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("文件必须是普通文件")
    if candidate.resolve(strict=True) != candidate:
        raise ValueError("文件路径无效")
    return candidate, info


def list_artifacts(execution_id: int) -> dict:
    result: dict = {"files": [], "truncated": False, "error": ""}
    try:
        root = _root(execution_id)
        _checked_path(root, (), directory=True)
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        result["error"] = UNAVAILABLE_MESSAGE
        return result

    visited = 0
    stopped = False

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        nonlocal visited, stopped
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if visited >= MAX_ENTRIES:
                        result["truncated"] = stopped = True
                        return
                    visited += 1
                    if not _valid_name(entry.name):
                        continue
                    child_parts = (*parts, entry.name)
                    relative = "/".join(child_parts)
                    if len(relative) > MAX_PATH_CHARS:
                        result["truncated"] = True
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if not _safe_info(info):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if len(child_parts) >= MAX_DEPTH:
                                result["truncated"] = True
                                continue
                            child, _ = _checked_path(root, child_parts, directory=True)
                            walk(child, child_parts)
                            if stopped:
                                return
                        elif stat.S_ISREG(info.st_mode):
                            _, info = _checked_path(root, child_parts)
                            if len(result["files"]) >= MAX_FILES:
                                result["truncated"] = stopped = True
                                return
                            result["files"].append(
                                {
                                    "path": relative,
                                    "name": entry.name,
                                    "size_bytes": info.st_size,
                                }
                            )
                    except (FileNotFoundError, ValueError):
                        continue
                    except OSError:
                        result["error"] = UNAVAILABLE_MESSAGE
        except OSError:
            result["error"] = UNAVAILABLE_MESSAGE

    walk(root, ())
    result["files"].sort(key=lambda item: (item["path"].casefold(), item["path"]))
    return result


def _windows_handle_path(fd: int) -> Path:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(fd)
    required = function(handle, None, 0, 0)
    if not required:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = function(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise OSError("无法核对文件实际位置")
    name = buffer.value
    if name.startswith("\\\\?\\UNC\\"):
        name = "\\\\" + name[8:]
    elif name.startswith("\\\\?\\"):
        name = name[4:]
    return Path(name)


def open_artifact(execution_id: int, relative_path: str) -> BinaryIO:
    parts = _relative_parts(relative_path)
    root = _root(execution_id)
    candidate, before = _checked_path(root, parts)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(candidate, flags)
    try:
        opened = os.fstat(fd)
        if (
            not _safe_info(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not os.path.samestat(before, opened)
        ):
            raise OSError("文件已变化，请刷新后重试")
        _, after = _checked_path(root, parts)
        if not os.path.samestat(after, opened):
            raise OSError("文件已变化，请刷新后重试")
        if os.name == "nt" and _windows_handle_path(fd) != candidate:
            raise OSError("文件实际位置无效")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


class ArtifactDownloadResponse(StreamingResponse):
    def __init__(self, stream: BinaryIO, filename: str) -> None:
        self.stream = stream
        try:
            self.size_bytes = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            super().__init__(
                self._chunks(),
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(self.size_bytes),
                    "Content-Disposition": (
                        'attachment; filename="artifact"; '
                        f"filename*=UTF-8''{quote(filename, safe='')}"
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )
        except BaseException:
            stream.close()
            raise

    def _chunks(self):
        remaining = self.size_bytes
        try:
            while remaining:
                chunk = self.stream.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise OSError("文件内容已变化，请刷新后重试")
                remaining -= len(chunk)
                yield chunk
        finally:
            self.stream.close()

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()
