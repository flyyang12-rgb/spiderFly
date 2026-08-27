from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TaskPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    script_path: str = Field(min_length=1, max_length=1000)
    python_path: str = Field(default="", max_length=1000)
    app_name: str = Field(default="", max_length=100)
    trigger_type: Literal["manual", "once", "interval", "daily", "weekly"] = "manual"
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: int = Field(default=0, ge=0, le=604800)
    notify_on_success: bool = True
    notify_on_failure: bool = True

    @field_validator("name", "script_path")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("description", "python_path", "app_name")
    @classmethod
    def strip_optional(cls, value: str) -> str:
        return value.strip()


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    script_path: str | None = Field(default=None, min_length=1, max_length=1000)
    python_path: str | None = Field(default=None, max_length=1000)
    app_name: str | None = Field(default=None, max_length=100)
    trigger_type: Literal["manual", "once", "interval", "daily", "weekly"] | None = None
    trigger_config: dict[str, Any] | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=0, le=604800)
    notify_on_success: bool | None = None
    notify_on_failure: bool | None = None


class RunResponse(BaseModel):
    execution_id: int
    status: str
    message: str
