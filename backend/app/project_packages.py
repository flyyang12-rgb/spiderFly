"""Import an immutable Python project and prepare a private copy per execution."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
from zipfile import BadZipFile, ZipFile

from . import config

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2000


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS project_packages (
        app_id INTEGER PRIMARY KEY REFERENCES rpa_apps(id) ON DELETE CASCADE,
        entry TEXT NOT NULL, digest TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS project_executions (
        execution_id INTEGER PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
        app_id INTEGER NOT NULL, entry TEXT NOT NULL, digest TEXT NOT NULL)""")


def _find(conn, table, column, value):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        row = conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (value,)).fetchone()
        return dict(row) if row else None
    return None


def for_app(conn, app_id):
    return _find(conn, "project_packages", "app_id", app_id)


def for_task(conn, task_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_packages'").fetchone():
        row = conn.execute("SELECT p.* FROM project_packages p JOIN tasks t ON t.app_id=p.app_id WHERE t.id=?",
                           (task_id,)).fetchone()
        return dict(row) if row else None
    return None


def for_execution(conn, execution_id):
    return _find(conn, "project_executions", "execution_id", execution_id)


def require_script_task(conn, task_id):
    if for_task(conn, task_id):
        raise ValueError("项目按整包保存，请通过项目导入提交程序，单脚本版本操作不适用")


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


def inspect(content, entry):
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("项目 ZIP 不能为空且不能超过 50 MB")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            files, directories = _members(archive)
            entries = sorted(("/".join(parts) for parts in files if parts[-1].lower().endswith(".py")), key=str.casefold)
            if not entries:
                raise ValueError("项目中没有 Python 文件")
            if not entry:
                return {"entries": entries}
            parts = _parts(entry)
            if "/".join(parts) not in entries:
                raise ValueError("请选择压缩包内实际存在的 Python 入口")
            from .environments import MAX_SCRIPT_BYTES, MAX_REQUIREMENTS_CHARS, validate_python_upload
            if files[parts].file_size > MAX_SCRIPT_BYTES:
                raise ValueError("Python 入口超过 2 MB")
            source = archive.read(files[parts])
            validate_python_upload(parts[-1], source)
            compile(source.decode("utf-8-sig"), "/".join(parts), "exec")
            root = _root_parts(files, directories)
            requirements_item = files.get(root + ("requirements.txt",))
            if requirements_item and requirements_item.file_size > 4 * MAX_REQUIREMENTS_CHARS:
                raise ValueError("项目依赖文件过大")
            requirements = archive.read(requirements_item).decode("utf-8-sig") if requirements_item else ""
            if archive.testzip() is not None:
                raise ValueError("项目 ZIP 有损坏的文件")
            return {"entry": "/".join(parts), "digest": hashlib.sha256(content).hexdigest(),
                    "source": source, "requirements": requirements}
    except (BadZipFile, RuntimeError, NotImplementedError, UnicodeDecodeError) as exc:
        raise ValueError("项目 ZIP 损坏、加密或包含不支持的文件编码") from exc
    except SyntaxError as exc:
        raise ValueError(f"项目入口有语法错误（第 {exc.lineno} 行）：{exc.msg}") from exc


def _root_parts(files, directories):
    first = next(iter(files), ())
    if (len(first) > 1 and all(len(parts) > 1 and parts[0] == first[0] for parts in files)
            and all(parts[0] == first[0] for parts in directories)):
        return first[:1]
    return ()


def archive_path(app_dir, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("项目版本标识无效")
    return app_dir / f"project-{digest}.zip"


def prepare(snapshot, tmp):
    apps_root = config.RPA_APPS_DIR.resolve()
    app_dir = apps_root / str(int(snapshot["app_id"]))
    path = archive_path(app_dir, snapshot["digest"])
    if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("当前执行的项目包缺失或路径无效")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != snapshot["digest"]:
        raise ValueError("当前执行的项目包校验失败")
    destination = tmp / "project"
    destination.mkdir()
    with ZipFile(io.BytesIO(content)) as archive:
        files, directories = _members(archive)
        root = _root_parts(files, directories)
        for parts in directories:
            destination.joinpath(*parts[len(root):]).mkdir(parents=True, exist_ok=True)
        for parts, item in files.items():
            output = destination.joinpath(*parts[len(root):])
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream)
    entry = _parts(snapshot["entry"])
    script = destination.joinpath(*entry[len(root):])
    if not script.is_file():
        raise ValueError("当前执行的项目入口不存在")
    return script, destination
