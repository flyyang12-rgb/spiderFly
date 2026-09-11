"""Validated data and errors for ordinary Python helpers; no registry or dispatch."""
from __future__ import annotations

from copy import deepcopy
from pydantic import BaseModel, ConfigDict, ValidationError
from .errors import TaskError

class DataModel(BaseModel):
    """Base for file-helper data; conversion must be explicit in the handler."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_default=True,
        frozen=True,
        revalidate_instances="always",
    )


def _validate_data(
    model: type[DataModel], payload: object, operation: str, stage: str
) -> DataModel:
    code = "INPUT_INVALID" if stage == "input" else "OUTPUT_INVALID"
    label = "输入" if stage == "input" else "输出"
    if not isinstance(payload, dict):
        # Do not accept pre-constructed model instances which may bypass checks.
        raise TaskError(code, operation, stage, f"操作{label}必须是字典")
    try:
        return model.model_validate(deepcopy(payload), strict=True)
    except ValidationError as exc:
        fields = tuple(
            (tuple(item["loc"]), item["type"])
            for item in exc.errors(include_url=False, include_context=False, include_input=False)
        )
        raise TaskError(
            code, operation, stage, f"操作{label}不符合定义", fields=fields
        ) from exc
    except Exception as exc:
        raise TaskError(
            code, operation, stage, f"操作{label}校验发生异常"
        ) from exc


def checked(operation, input_model, output_model, handler, verifier, values):
    """Validate data, execute once, and verify the returned result."""
    inputs = _validate_data(input_model, values, operation, "input")
    original = inputs.model_copy(deep=True)
    output = _validate_data(output_model, handler(inputs), operation, "output")
    try:
        valid = verifier(original, output.model_copy(deep=True))
    except Exception as exc:
        raise TaskError("VERIFICATION_ERROR", operation, "verify", "结果检查发生异常") from exc
    if valid is not True:
        raise TaskError("VERIFICATION_FAILED", operation, "verify", "结果未通过检查")
    return output
