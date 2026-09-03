"""A synchronous instruction boundary with typed data and explicit verification.

This module does not schedule work, retry actions, access application settings,
or classify external business effects from a Python exception.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, is_dataclass
from typing import Any, Generic, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError


class InstructionModel(BaseModel):
    """Base for instruction data; conversion must be explicit in the handler."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_default=True,
        frozen=True,
        revalidate_instances="always",
    )


class InstructionError(Exception):
    """A public error without input values or the handler's exception text."""

    def __init__(
        self,
        code: str,
        instruction_id: str,
        stage: str,
        message: str,
        *,
        fields: tuple[tuple[tuple[str | int, ...], str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.instruction_id = instruction_id
        self.stage = stage
        self.fields = fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "instruction_id": self.instruction_id,
            "stage": self.stage,
            "message": str(self),
            "fields": [
                {"path": list(path), "type": error_type}
                for path, error_type in self.fields
            ],
        }


InputT = TypeVar("InputT", bound=InstructionModel)
OutputT = TypeVar("OutputT", bound=InstructionModel)
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def _check_model(
    model: type[InstructionModel], checked: set[type[InstructionModel]] | None = None
) -> None:
    if not isinstance(model, type) or not issubclass(model, InstructionModel):
        raise ValueError("输入、输出及其嵌套模型必须继承 InstructionModel")
    checked = set() if checked is None else checked
    if model in checked:
        return
    checked.add(model)
    for key, expected in InstructionModel.model_config.items():
        if model.model_config.get(key) != expected:
            raise ValueError(f"指令模型不能覆盖基础校验设置：{key}")
    model.model_rebuild()
    for field in model.model_fields.values():
        _check_annotation(field.annotation, checked)


def _check_annotation(annotation: Any, checked: set[type[InstructionModel]]) -> None:
    origin = get_origin(annotation) or annotation
    if isinstance(origin, type):
        if issubclass(origin, BaseModel):
            _check_model(origin, checked)
        elif is_dataclass(origin):
            raise ValueError("当前不支持 dataclass 字段，请使用嵌套 InstructionModel")
    for argument in get_args(annotation):
        _check_annotation(argument, checked)


@dataclass(frozen=True)
class Instruction(Generic[InputT, OutputT]):
    instruction_id: str
    name: str
    version: str
    description: str
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: Callable[[InputT], dict[str, Any]]
    verifier: Callable[[InputT, OutputT], bool]

    def __post_init__(self) -> None:
        if not isinstance(self.instruction_id, str) or not _ID_PATTERN.fullmatch(
            self.instruction_id
        ):
            raise ValueError("指令编号应按功能命名，例如 text.join_nonempty")
        if not isinstance(self.version, str) or not _VERSION_PATTERN.fullmatch(self.version):
            raise ValueError("指令版本应为三个非负整数，例如 0.1.0")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("指令名称不能为空")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("指令用途说明不能为空")
        _check_model(self.input_model)
        _check_model(self.output_model)
        for function in (self.handler, self.verifier):
            if not callable(function):
                raise ValueError("指令实现和结果检查必须可调用")
            if inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(
                getattr(function, "__call__", None)
            ):
                raise ValueError("当前指令入口仅支持同步函数")

    def describe(self) -> dict[str, Any]:
        """Generate metadata from the actual data models, without executing code."""
        return {
            "instruction_id": self.instruction_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(mode="validation"),
            "output_schema": self.output_model.model_json_schema(mode="validation"),
        }


def _validate_data(
    model: type[InputT], payload: object, instruction_id: str, stage: str
) -> InputT:
    code = "INPUT_INVALID" if stage == "input" else "OUTPUT_INVALID"
    label = "输入" if stage == "input" else "输出"
    if not isinstance(payload, dict):
        # Do not accept pre-constructed model instances which may bypass checks.
        raise InstructionError(code, instruction_id, stage, f"指令{label}必须是字典")
    try:
        return model.model_validate(deepcopy(payload), strict=True)
    except ValidationError as exc:
        fields = tuple(
            (tuple(item["loc"]), item["type"])
            for item in exc.errors(include_url=False, include_context=False, include_input=False)
        )
        raise InstructionError(
            code, instruction_id, stage, f"指令{label}不符合定义", fields=fields
        ) from exc
    except Exception as exc:
        raise InstructionError(
            code, instruction_id, stage, f"指令{label}校验发生异常"
        ) from exc


class InstructionRegistry:
    """An explicit per-instance registry; each call executes at most once."""

    def __init__(self) -> None:
        self._instructions: dict[str, Instruction[Any, Any]] = {}

    def register(self, instruction: Instruction[Any, Any]) -> None:
        if not isinstance(instruction, Instruction):
            raise TypeError("只能注册 Instruction 定义")
        if instruction.instruction_id in self._instructions:
            raise InstructionError(
                "INSTRUCTION_DUPLICATE",
                instruction.instruction_id,
                "register",
                "指令编号已经注册，原定义未被替换",
            )
        self._instructions[instruction.instruction_id] = instruction

    def catalog(self) -> list[dict[str, Any]]:
        return [self._instructions[key].describe() for key in sorted(self._instructions)]

    def execute(
        self, instruction_id: str, inputs: dict[str, Any] | None = None
    ) -> InstructionModel:
        instruction = self._instructions.get(instruction_id)
        if instruction is None:
            raise InstructionError(
                "INSTRUCTION_NOT_FOUND", instruction_id, "lookup", "没有找到该指令"
            )
        checked_input = _validate_data(
            instruction.input_model, {} if inputs is None else inputs, instruction_id, "input"
        )
        # Keep the original validated input even if a handler mutates a nested list.
        original_input = checked_input.model_copy(deep=True)
        try:
            raw_output = instruction.handler(checked_input)
        except InstructionError:
            # Instruction authors may provide an intentional, user-facing failure.
            raise
        except Exception as exc:
            raise InstructionError(
                "EXECUTION_ERROR", instruction_id, "execute", "指令执行发生异常"
            ) from exc
        if inspect.iscoroutine(raw_output):
            raw_output.close()
        checked_output = _validate_data(
            instruction.output_model, raw_output, instruction_id, "output"
        )
        try:
            # Verification cannot mutate the output that will be returned to callers.
            verified = instruction.verifier(
                original_input, checked_output.model_copy(deep=True)
            )
        except Exception as exc:
            raise InstructionError(
                "VERIFICATION_ERROR", instruction_id, "verify", "结果检查发生异常"
            ) from exc
        if inspect.iscoroutine(verified):
            verified.close()
        if verified is not True:
            raise InstructionError(
                "VERIFICATION_FAILED", instruction_id, "verify", "结果未通过检查"
            )
        return checked_output
