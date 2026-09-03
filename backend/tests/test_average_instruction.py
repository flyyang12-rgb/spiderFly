from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.average import AVERAGE


class AverageInstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InstructionRegistry()
        self.registry.register(AVERAGE)

    def execute(self, value):
        return self.registry.execute("math.average", {"value": value})

    def assert_value_error(self, value) -> None:
        with self.assertRaises(InstructionError) as caught:
            self.execute(value)
        self.assertEqual(caught.exception.code, "MATH_VALUE_INVALID")
        self.assertEqual(caught.exception.instruction_id, "math.average")
        self.assertEqual(caught.exception.stage, "execute")

    def test_single_numbers_and_numeric_text_return_float_and_count(self) -> None:
        for value, expected in ((5.6, 5.6), (5, 5.0), (0, 0.0), (-3, -3.0), (" 5.6 ", 5.6)):
            with self.subTest(value=value):
                result = self.execute(value)
                self.assertEqual(result.model_dump(), {"average": expected, "count": 1})
                self.assertIs(type(result.average), float)
                self.assertIs(type(result.count), int)

    def test_comma_values_include_spaces_negative_zero_and_duplicates(self) -> None:
        cases = (
            ("5,2", 3.5, 2),
            (" 5 , 2 ", 3.5, 2),
            (" -3, 0, 6 ", 1.0, 3),
            ("2,2,8", 4.0, 3),
            ("1,2,3,4", 2.5, 4),
            ("0,0", 0.0, 2),
        )
        for value, expected, count in cases:
            with self.subTest(value=value):
                payload = {"value": value}
                result = self.registry.execute("math.average", payload)
                self.assertEqual(result.model_dump(), {"average": expected, "count": count})
                self.assertEqual(payload, {"value": value})

    def test_comma_is_always_a_separator_not_a_decimal_or_thousands_mark(self) -> None:
        self.assertEqual(self.execute("1,000").model_dump(), {"average": 0.5, "count": 2})
        self.assertEqual(self.execute("5,6").model_dump(), {"average": 5.5, "count": 2})
        for value in ("5，2", "5;2", "5 2"):
            with self.subTest(value=value):
                self.assert_value_error(value)

    def test_empty_values_items_and_nonnumeric_text_fail(self) -> None:
        for value in ("", " \t ", ",", ",5", "5,", "5,,2", "5, ,2", "five", "5,two", "True", "1+2"):
            with self.subTest(value=value):
                self.assert_value_error(value)

    def test_missing_extra_or_wrong_types_fail_before_handler(self) -> None:
        handler = Mock(wraps=AVERAGE.handler)
        registry = InstructionRegistry()
        registry.register(replace(AVERAGE, handler=handler))
        payloads = [{}, {"value": 1, "extra": 2}]
        payloads.extend({"value": value} for value in (True, False, None, [], [5, 2], {}, b"5,2", Decimal("5.6")))
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(InstructionError) as caught:
                    registry.execute("math.average", payload)
                self.assertEqual(caught.exception.code, "INPUT_INVALID")
                self.assertEqual(caught.exception.stage, "input")
        handler.assert_not_called()

    def test_nonfinite_and_out_of_float_range_inputs_fail(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf"), "NaN", "inf", "5,-Infinity", "1e309", 10 ** 400):
            with self.subTest(value=value):
                self.assert_value_error(value)

    def test_finite_large_numbers_do_not_overflow_or_lose_cancelled_terms(self) -> None:
        for value, expected in (
            ("1e308,1e308", 1e308),
            ("-1e308,1e308", 0.0),
            ("10000000000000000,1,-10000000000000000", 1.0 / 3.0),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.execute(value).average, expected)

    def test_verifier_rejects_wrong_average_or_count_without_retry(self) -> None:
        for payload in ({"average": 7.0, "count": 2}, {"average": 3.5, "count": 1}):
            with self.subTest(payload=payload):
                handler = Mock(return_value=payload)
                registry = InstructionRegistry()
                registry.register(replace(AVERAGE, handler=handler))
                with self.assertRaises(InstructionError) as caught:
                    registry.execute("math.average", {"value": "5,2"})
                self.assertEqual(caught.exception.code, "VERIFICATION_FAILED")
                self.assertEqual(caught.exception.stage, "verify")
                handler.assert_called_once()

    def test_invalid_handler_outputs_cannot_escape_output_validation(self) -> None:
        outputs = (
            {"average": float("nan"), "count": 2},
            {"average": float("inf"), "count": 2},
            {"average": True, "count": 2},
            {"average": 3.5, "count": True},
            {"average": "3.5", "count": 2},
            {"average": 3.5, "count": 0},
        )
        for output in outputs:
            with self.subTest(output=output):
                registry = InstructionRegistry()
                registry.register(replace(AVERAGE, handler=lambda _, value=output: value))
                with self.assertRaises(InstructionError) as caught:
                    registry.execute("math.average", {"value": "5,2"})
                self.assertEqual(caught.exception.code, "OUTPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
