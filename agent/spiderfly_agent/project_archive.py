"""Portable ZIP layout for the controller and the independently installed Agent."""
import hashlib
from pathlib import PureWindowsPath
import re
import shutil
import stat
from zipfile import ZipFile

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2000


def package_hash(digest, entry, requirements):
    fields = ("project-zip-v1", digest, entry, requirements)
    return hashlib.sha256(b"\0".join(field.encode("utf-8") for field in fields)).hexdigest()


def _parts(name):
    parts = name.replace("\\", "/").split("/")
    if len(parts) > 12 or len(name) > 200 or any(
        part in ("", ".", "..") or part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()
        or re.search(r'[<>:"|?*\x00-\x1f\x7f]', part) for part in parts
    ):
        raise ValueError(f"项目文件路径无效：{name!r}")
    return tuple(parts)


def _members(archive):
    members = archive.infolist()
    if len(members) > MAX_ENTRIES or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES:
        raise ValueError("项目解包后最多 2000 个文件或目录、200 MB")
    paths = {}
    files = {}
    for item in members:
        name = item.orig_filename.replace("\\", "/")
        folder = name.endswith("/")
        kind = stat.S_IFMT(item.external_attr >> 16) if item.create_system == 3 else 0
        if item.external_attr & 0x400 or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not folder):
            raise ValueError("项目不能包含符号链接或特殊文件")
        parts = _parts(name[:-1] if folder else name)
        for index in range(1, len(parts) + 1):
            exact = parts[:index]
            key = tuple(part.casefold() for part in exact)
            is_dir = index < len(parts) or folder
            if key in paths and paths[key] != (exact, is_dir):
                raise ValueError("项目包含大小写冲突或文件与目录同名")
            paths[key] = (exact, is_dir)
        if not folder:
            if parts in files:
                raise ValueError("项目包含重复文件")
            files[parts] = item
    directories = [parts for parts, is_dir in paths.values() if is_dir]
    return files, directories


def _root_parts(files, directories):
    first = next(iter(files), ())
    if (len(first) > 1 and all(len(parts) > 1 and parts[0] == first[0] for parts in files)
            and all(parts[0] == first[0] for parts in directories)):
        return first[:1]
    return ()


def extract_project(archive_file, entry, destination):
    destination.mkdir()
    with ZipFile(archive_file) as archive:
        files, directories = _members(archive)
        entry_parts = _parts(entry)
        if entry_parts not in files or not entry_parts[-1].lower().endswith(".py"):
            raise ValueError("当前执行的项目入口不存在")
        root = _root_parts(files, directories)
        for parts in directories:
            destination.joinpath(*parts[len(root):]).mkdir(parents=True, exist_ok=True)
        for parts, item in files.items():
            output = destination.joinpath(*parts[len(root):])
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream)
    script = destination.joinpath(*entry_parts[len(root):])
    if not script.is_file():
        raise ValueError("当前执行的项目入口不存在")
    return script, destination
