"""Import an immutable Python project and prepare a private copy per execution."""
from __future__ import annotations

import hashlib
import io
import re
from zipfile import BadZipFile, ZipFile

from . import config

from .project_archive import MAX_ARCHIVE_BYTES, _members, _parts, _root_parts, extract_project


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


def archive_path(app_dir, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("项目版本标识无效")
    return app_dir / f"project-{digest}.zip"


def _verified_content(apps_root, snapshot):
    apps_root = apps_root.resolve()
    app_dir = apps_root / str(int(snapshot["app_id"]))
    path = archive_path(app_dir, snapshot["digest"])
    if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("当前执行的项目包缺失或路径无效")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != snapshot["digest"]:
        raise ValueError("当前执行的项目包校验失败")
    return content


def freeze_remote_archive(conn, apps_root, project):
    if not conn.execute("SELECT 1 FROM remote_project_archives WHERE digest=?", (project["digest"],)).fetchone():
        content = _verified_content(apps_root, project)
        conn.execute("INSERT INTO remote_project_archives(digest,content) VALUES(?,?)", (project["digest"], content))


def prepare(snapshot, tmp):
    content = _verified_content(config.RPA_APPS_DIR, snapshot)
    return extract_project(io.BytesIO(content), snapshot["entry"], tmp / "project")
