"""Run with: python -m spiderfly_instructions.demo (from backend/)."""

from __future__ import annotations

import json

from pydantic import Field

from .core import Instruction, InstructionModel, InstructionRegistry


class JoinTextInput(InstructionModel):
    items: list[str] = Field(description="要合并的文字；空白项会跳过")
    separator: str = Field(default="、", description="文字之间使用的分隔符")


class JoinTextOutput(InstructionModel):
    text: str = Field(description="合并后的文字")
    count: int = Field(ge=0, description="保留的非空文字数量")


def join_nonempty(inputs: JoinTextInput) -> dict[str, object]:
    values = [item.strip() for item in inputs.items if item.strip()]
    return {"text": inputs.separator.join(values), "count": len(values)}


def verify_join(inputs: JoinTextInput, result: JoinTextOutput) -> bool:
    values = tuple(filter(None, map(str.strip, inputs.items)))
    return result.count == len(values) and result.text == inputs.separator.join(values)


JOIN_NONEMPTY = Instruction(
    instruction_id="text.join_nonempty",
    name="合并非空文字",
    version="0.1.0",
    description="去掉各项首尾空白，跳过空白项，按原顺序合并；不去重。",
    input_model=JoinTextInput,
    output_model=JoinTextOutput,
    handler=join_nonempty,
    verifier=verify_join,
)


def main() -> None:
    registry = InstructionRegistry()
    registry.register(JOIN_NONEMPTY)
    result = registry.execute(
        "text.join_nonempty", {"items": [" 库存日报 ", "", "订单核对"]}
    )
    print(json.dumps(result.model_dump(), ensure_ascii=False))


if __name__ == "__main__":
    main()
