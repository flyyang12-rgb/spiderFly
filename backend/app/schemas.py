from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_TASK_TIMEOUT_SECONDS


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "operator"] = "operator"
    password: str = Field(min_length=10, max_length=200)


class TaskPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    app_id: int = Field(ge=1)
    trigger_type: Literal["manual", "daily", "weekly"] = "manual"
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: int = Field(default=DEFAULT_TASK_TIMEOUT_SECONDS, ge=0, le=604800)
    notify_on_success: bool = True
    notify_on_failure: bool = True

    @field_validator("name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("description")
    @classmethod
    def strip_optional(cls, value: str) -> str:
        return value.strip()

    @field_validator("timeout_seconds")
    @classmethod
    def normalize_timeout(cls, value: int) -> int:
        del value
        return DEFAULT_TASK_TIMEOUT_SECONDS


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    app_id: int | None = Field(default=None, ge=1)
    trigger_type: Literal["manual", "daily", "weekly"] | None = None
    trigger_config: dict[str, Any] | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=0, le=604800)
    notify_on_success: bool | None = None
    notify_on_failure: bool | None = None
    version: int | None = Field(default=None, ge=1)

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        # Omitted fields keep their defaults; none of the stored fields is nullable.
        if value is None:
            raise ValueError("修改字段不能为 null；不修改的字段请省略")
        return value

    @field_validator("name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return TaskPayload.strip_required(value)

    @field_validator("description")
    @classmethod
    def strip_optional(cls, value: str) -> str:
        return value.strip()

    @field_validator("timeout_seconds")
    @classmethod
    def normalize_optional_timeout(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return DEFAULT_TASK_TIMEOUT_SECONDS


class RunResponse(BaseModel):
    execution_id: int
    status: str
    message: str
