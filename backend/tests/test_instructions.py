from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from typing import Generic, TypeVar
from unittest.mock import Mock

from pydantic import BaseModel, ConfigDict
from pydantic.dataclasses import dataclass as pydantic_dataclass

from spiderfly_instructions import (
    Instruction,
    InstructionError,
    InstructionModel,
    InstructionRegistry,
)
from spiderfly_instructions.demo import JOIN_NONEMPTY


class CountInput(InstructionModel):
    count: int


class CountOutput(InstructionModel):
    total: int


class InstructionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = Mock(return_value={"total": 4})
        self.verifier = Mock(return_value=True)
        self.instruction = Instruction(
            instruction_id="test.count",
            name="计数测试",
            version="0.1.0",
            description="检查指令调用边界",
            input_model=CountInput,
            output_model=CountOutput,
            handler=self.handler,
            verifier=self.verifier,
        )
        self.registry = InstructionRegistry()
        self.registry.register(self.instruction)

    def test_invalid_inputs_never_execute_the_handler(self) -> None:
        for value in ({}, {"count": "4"}, {"count": True}, {"count": 4, "extra": 1}, []):
            with self.subTest(value=value), self.assertRaises(InstructionError) as caught:
                self.registry.execute("test.count", value)
            self.assertEqual(caught.exception.code, "INPUT_INVALID")
        self.handler.assert_not_called()
        self.verifier.assert_not_called()

    def test_constructed_input_models_cannot_bypass_validation(self) -> None:
        forged = CountInput.model_construct(count="not-an-integer")
        with self.assertRaises(InstructionError) as caught:
            self.registry.execute("test.count", forged)
        self.assertEqual(caught.exception.stage, "input")
        self.handler.assert_not_called()

    def test_invalid_outputs_execute_once_and_never_reach_verification(self) -> None:
        for value in ({}, {"total": "4"}, {"total": 4, "extra": 1}, CountOutput(total=4)):
            with self.subTest(value=value):
                self.handler.reset_mock()
                self.handler.return_value = value
                with self.assertRaises(InstructionError) as caught:
                    self.registry.execute("test.count", {"count": 4})
                self.assertEqual(caught.exception.code, "OUTPUT_INVALID")
                self.handler.assert_called_once()
        self.verifier.assert_not_called()

    def test_duplicate_registration_preserves_the_first_implementation(self) -> None:
        replacement = Mock(return_value={"total": 999})
        with self.assertRaises(InstructionError) as caught:
            self.registry.register(replace(self.instruction, handler=replacement))
        self.assertEqual(caught.exception.code, "INSTRUCTION_DUPLICATE")
        self.assertEqual(self.registry.execute("test.count", {"count": 4}).total, 4)
        replacement.assert_not_called()

    def test_unknown_instruction_and_separate_registry_do_not_execute(self) -> None:
        for registry, key in ((self.registry, "missing.command"), (InstructionRegistry(), "test.count")):
            with self.subTest(key=key), self.assertRaises(InstructionError) as caught:
                registry.execute(key, {"count": 4})
            self.assertEqual(caught.exception.code, "INSTRUCTION_NOT_FOUND")
        self.handler.assert_not_called()

    def test_handler_exception_is_not_retried_or_exposed_in_public_error(self) -> None:
        original_error = RuntimeError("credential-value-must-stay-local")
        self.handler.side_effect = original_error
        with self.assertRaises(InstructionError) as caught:
            self.registry.execute("test.count", {"count": 4})
        error = caught.exception
        self.assertEqual(error.code, "EXECUTION_ERROR")
        self.assertEqual(error.stage, "execute")
        self.assertIs(error.__cause__, original_error)
        self.assertNotIn("credential-value", json.dumps(error.to_dict()))
        self.handler.assert_called_once()
        self.verifier.assert_not_called()

    def test_declared_instruction_failure_preserves_its_public_message(self) -> None:
        declared = InstructionError("TEST_MISSING_COLUMN", "test.count", "execute", "缺少必需列：订单号")
        self.handler.side_effect = declared
        with self.assertRaises(InstructionError) as caught:
            self.registry.execute("test.count", {"count": 4})
        self.assertIs(caught.exception, declared)
        self.assertEqual(caught.exception.to_dict()["message"], "缺少必需列：订单号")
        self.handler.assert_called_once()
        self.verifier.assert_not_called()

    def test_only_literal_true_passes_business_verification(self) -> None:
        for value in (False, None, 1, "true"):
            with self.subTest(value=value):
                self.handler.reset_mock()
                self.verifier.reset_mock()
                self.verifier.return_value = value
                with self.assertRaises(InstructionError) as caught:
                    self.registry.execute("test.count", {"count": 4})
                self.assertEqual(caught.exception.code, "VERIFICATION_FAILED")
                self.handler.assert_called_once()
                self.verifier.assert_called_once()

    def test_verifier_exception_is_different_from_a_rejected_result(self) -> None:
        self.verifier.side_effect = ValueError("private verifier details")
        with self.assertRaises(InstructionError) as caught:
            self.registry.execute("test.count", {"count": 4})
        self.assertEqual(caught.exception.code, "VERIFICATION_ERROR")
        self.handler.assert_called_once()
        self.verifier.assert_called_once()

    def test_validation_error_reports_field_location_without_value(self) -> None:
        with self.assertRaises(InstructionError) as caught:
            self.registry.execute("test.count", {"count": "private-input-value"})
        public_error = caught.exception.to_dict()
        self.assertEqual(public_error["fields"], [{"path": ["count"], "type": "int_type"}])
        self.assertNotIn("private-input-value", json.dumps(public_error))

    def test_defaults_are_validated_before_execution(self) -> None:
        class BadDefault(InstructionModel):
            count: int = "4"

        registry = InstructionRegistry()
        registry.register(replace(self.instruction, input_model=BadDefault))
        with self.assertRaises(InstructionError) as caught:
            registry.execute("test.count")
        self.assertEqual(caught.exception.code, "INPUT_INVALID")
        self.handler.assert_not_called()

    def test_models_cannot_silently_ignore_extra_fields(self) -> None:
        class LooseInput(InstructionModel):
            model_config = ConfigDict(extra="ignore")
            count: int

        with self.assertRaises(ValueError):
            replace(self.instruction, input_model=LooseInput)

    def test_nested_plain_models_are_rejected_in_both_contracts(self) -> None:
        class UncheckedChild(BaseModel):
            count: int

        class UncheckedContainer(InstructionModel):
            children: list[UncheckedChild] | None

        for model_role in ("input_model", "output_model"):
            with self.subTest(model_role=model_role), self.assertRaises(ValueError):
                replace(self.instruction, **{model_role: UncheckedContainer})

    def test_nested_dataclasses_cannot_bypass_instance_validation(self) -> None:
        RecordT = TypeVar("RecordT")

        @pydantic_dataclass(config=ConfigDict(revalidate_instances="never"))
        class UncheckedRecord(Generic[RecordT]):
            count: RecordT

        class UncheckedContainer(InstructionModel):
            record: UncheckedRecord

        class GenericContainer(InstructionModel):
            record: UncheckedRecord[int]

        for container in (UncheckedContainer, GenericContainer):
            for model_role in ("input_model", "output_model"):
                with self.subTest(container=container, model_role=model_role):
                    with self.assertRaises(ValueError):
                        replace(self.instruction, **{model_role: container})

    def test_invalid_nested_input_is_rechecked_before_execution(self) -> None:
        class Container(InstructionModel):
            child: CountInput

        registry = InstructionRegistry()
        registry.register(replace(self.instruction, input_model=Container))
        for child in (CountInput.model_construct(count="invalid"), {"count": 4, "extra": 1}):
            with self.subTest(child=child), self.assertRaises(InstructionError) as caught:
                registry.execute("test.count", {"child": child})
            self.assertEqual(caught.exception.code, "INPUT_INVALID")
            self.assertEqual(caught.exception.to_dict()["fields"][0]["path"][0], "child")
        self.handler.assert_not_called()

    def test_invalid_nested_output_is_rechecked_before_verification(self) -> None:
        class Container(InstructionModel):
            child: CountOutput

        registry = InstructionRegistry()
        registry.register(replace(self.instruction, output_model=Container))
        self.handler.return_value = {"child": CountOutput.model_construct(total="invalid")}
        with self.assertRaises(InstructionError) as caught:
            registry.execute("test.count", {"count": 4})
        self.assertEqual(caught.exception.code, "OUTPUT_INVALID")
        self.assertEqual(caught.exception.to_dict()["fields"], [
            {"path": ["child", "total"], "type": "int_type"}
        ])
        self.handler.assert_called_once()
        self.verifier.assert_not_called()

    def test_interrupts_are_not_converted_to_business_errors(self) -> None:
        self.handler.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.registry.execute("test.count", {"count": 4})
        self.handler.assert_called_once()

    def test_async_functions_are_rejected_at_definition(self) -> None:
        async def asynchronous_handler(inputs: CountInput) -> dict[str, int]:
            return {"total": inputs.count}

        with self.assertRaises(ValueError):
            replace(self.instruction, handler=asynchronous_handler)

    def test_nested_mutation_cannot_change_callers_data_or_verification_basis(self) -> None:
        class Values(InstructionModel):
            items: list[str]

        def handler(inputs: Values) -> dict[str, list[str]]:
            inputs.items.append("handler-change")
            return {"items": inputs.items}

        def verifier(inputs: Values, output: Values) -> bool:
            self.assertEqual(inputs.items, ["original"])
            output.items.append("verifier-change")
            return True

        registry = InstructionRegistry()
        registry.register(
            replace(self.instruction, input_model=Values, output_model=Values,
                    handler=handler, verifier=verifier)
        )
        supplied = {"items": ["original"]}
        output = registry.execute("test.count", supplied)
        self.assertEqual(supplied, {"items": ["original"]})
        self.assertEqual(output.items, ["original", "handler-change"])


class InstructionDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstructionRegistry()
        self.registry.register(JOIN_NONEMPTY)

    def test_two_callers_reuse_one_definition_with_different_parameters(self) -> None:
        report = self.registry.execute(
            "text.join_nonempty", {"items": [" 库存日报 ", "", "订单核对"]}
        )
        filenames = self.registry.execute(
            "text.join_nonempty", {"items": [" A ", "A", " B "], "separator": "-"}
        )
        self.assertEqual(report.model_dump(), {"text": "库存日报、订单核对", "count": 2})
        self.assertEqual(filenames.model_dump(), {"text": "A-A-B", "count": 3})

    def test_empty_input_is_a_valid_empty_result(self) -> None:
        result = self.registry.execute("text.join_nonempty", {"items": ["", "  "]})
        self.assertEqual(result.model_dump(), {"text": "", "count": 0})

    def test_catalog_describes_actual_required_fields_defaults_and_outputs(self) -> None:
        entry = self.registry.catalog()[0]
        self.assertEqual(entry["instruction_id"], "text.join_nonempty")
        self.assertEqual(entry["input_schema"]["required"], ["items"])
        self.assertEqual(entry["input_schema"]["properties"]["separator"]["default"], "、")
        self.assertFalse(entry["input_schema"]["additionalProperties"])
        self.assertEqual(set(entry["output_schema"]["required"]), {"text", "count"})
        entry["input_schema"]["required"].clear()
        self.assertEqual(self.registry.catalog()[0]["input_schema"]["required"], ["items"])

    def test_demo_runs_without_importing_application_or_loading_server_state(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-c",
             "import sys; from spiderfly_instructions.demo import main; main(); "
             "assert not any(x == 'app' or x.startswith('app.') for x in sys.modules)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
            timeout=15,
        )
        self.assertEqual(json.loads(completed.stdout), {"text": "库存日报、订单核对", "count": 2})


if __name__ == "__main__":
    unittest.main()
