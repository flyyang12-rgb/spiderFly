"""List regular files in one folder without changing or opening their contents."""

from __future__ import annotations

import os
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import Field, field_validator

from ._validation import DataModel, TaskError, checked


OPERATION = "file.list"


class ListFilesInput(DataModel):
    folder_path: str = Field(min_length=1, description="已有文件夹；相对路径按当前运行目录解释")
    pattern: str = Field(default="*", min_length=1, description="文件名通配符，例如 *.xlsx；忽略大小写")

    @field_validator("folder_path", "pattern")
    @classmethod
    def check_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("路径或匹配条件不能为空白，也不能包含空字符")
        return value

    @field_validator("pattern")
    @classmethod
    def check_pattern(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError("匹配条件只能填写文件名通配符，不能带文件夹路径")
        return value


class ListFilesOutput(DataModel):
    files: list[str] = Field(description="匹配的普通文件绝对路径，按文件名排序")
    count: int = Field(ge=0, description="文件数量")


def _matches(name: str, pattern: str) -> bool:
    return fnmatchcase(name.casefold(), pattern.casefold())


def _sort_key(path: str) -> tuple[str, str]:
    name = Path(path).name
    return name.casefold(), name


def _list_files(inputs: ListFilesInput) -> dict[str, object]:
    try:
        folder = os.path.abspath(inputs.folder_path)
        with os.scandir(folder) as entries:
            files = [
                entry.path for entry in entries
                if _matches(entry.name, inputs.pattern) and entry.is_file(follow_symlinks=False)
            ]
    except FileNotFoundError as exc:
        raise TaskError(
            "FILE_FOLDER_NOT_FOUND", OPERATION, "execute", "文件夹不存在或已被移走。",
        ) from exc
    except NotADirectoryError as exc:
        raise TaskError(
            "FILE_FOLDER_INVALID", OPERATION, "execute", "请填写文件夹路径，不能填写单个文件。",
        ) from exc
    except PermissionError as exc:
        raise TaskError(
            "FILE_ACCESS_DENIED", OPERATION, "execute", "没有权限列出这个文件夹中的文件。",
        ) from exc
    except OSError as exc:
        raise TaskError(
            "FILE_LIST_FAILED", OPERATION, "execute", "读取文件清单失败，请检查路径及磁盘是否可用。",
        ) from exc
    files.sort(key=_sort_key)
    return {"files": files, "count": len(files)}


def verify_files(inputs: ListFilesInput, result: ListFilesOutput) -> bool:
    # Check the returned list, not a second scan: the folder may change meanwhile.
    folder = Path(os.path.abspath(inputs.folder_path))
    return (
        result.count == len(result.files)
        and len(set(result.files)) == result.count
        and result.files == sorted(result.files, key=_sort_key)
        and all(
            Path(path).is_absolute()
            and Path(path).parent == folder
            and _matches(Path(path).name, inputs.pattern)
            for path in result.files
        )
    )


def list_files(folder_path: str, pattern: str = "*") -> ListFilesOutput:
    return checked(OPERATION, ListFilesInput, ListFilesOutput, _list_files, verify_files, {"folder_path": folder_path, "pattern": pattern})
