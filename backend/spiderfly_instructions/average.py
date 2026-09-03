"""Average one number or comma-separated numeric text, without file access."""

from __future__ import annotations

from math import isfinite
from statistics import mean

from pydantic import Field, field_validator

from .core import Instruction, InstructionError, InstructionModel


INSTRUCTION_ID = "math.average"


class AverageInput(InstructionModel):
    value: str | int | float = Field(description="单个数字，或用英文逗号分隔的数字文字")

    @field_validator("value", mode="before")
    @classmethod
    def check_value_type(cls, value: object) -> object:
        if type(value) not in (str, int, float):
            raise ValueError("只接受文字、整数或小数，不接受布尔值")
        return value


class AverageOutput(InstructionModel):
    average: float = Field(allow_inf_nan=False, description="所有数字的算术平均数")
    count: int = Field(ge=1, description="参与计算的数字数量")


def _numbers(value: str | int | float) -> list[float]:
    parts = value.split(",") if isinstance(value, str) else [value]
    numbers = []
    for index, part in enumerate(parts, start=1):
        if isinstance(part, str) and not part.strip():
            raise InstructionError(
                "MATH_VALUE_INVALID", INSTRUCTION_ID, "execute",
                f"第 {index} 项为空，请填写数字；英文逗号只用于分隔数字。",
            )
        try:
            number = float(part)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InstructionError(
                "MATH_VALUE_INVALID", INSTRUCTION_ID, "execute",
                f"第 {index} 项不是可计算的数字，请使用数字或英文逗号分隔的数字文字。",
            ) from exc
        if not isfinite(number):
            raise InstructionError(
                "MATH_VALUE_INVALID", INSTRUCTION_ID, "execute",
                f"第 {index} 项不是有限数字，或超出可计算的数值范围。",
            )
        numbers.append(number)
    return numbers


def average(inputs: AverageInput) -> dict[str, object]:
    numbers = _numbers(inputs.value)
    # mean avoids intermediate overflow and cancellation from sum(values) / count.
    return {"average": float(mean(numbers)), "count": len(numbers)}


def verify_average(inputs: AverageInput, result: AverageOutput) -> bool:
    numbers = _numbers(inputs.value)
    return (
        result.count == len(numbers)
        and isfinite(result.average)
        and result.average == float(mean(numbers))
    )


AVERAGE = Instruction(
    instruction_id=INSTRUCTION_ID,
    name="计算平均数",
    version="0.1.0",
    description="计算单个数字或英文逗号分隔数字的算术平均数；空项、非数字和非有限数明确报错。",
    input_model=AverageInput,
    output_model=AverageOutput,
    handler=average,
    verifier=verify_average,
)
