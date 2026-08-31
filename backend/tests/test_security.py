from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch


try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.Depends = lambda dependency=None: dependency
    fastapi_stub.HTTPException = type("HTTPException", (Exception,), {})
    fastapi_stub.Request = type("Request", (), {})
    fastapi_stub.Response = type("Response", (), {})
    fastapi_stub.status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401)
    sys.modules["fastapi"] = fastapi_stub

from app import security


class PasswordHashTests(unittest.TestCase):
    def test_hash_round_trip_and_wrong_password(self) -> None:
        with patch.object(security, "PASSWORD_ITERATIONS", 1_000):
            encoded = security._hash_password("correct horse battery staple")

        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        self.assertEqual(algorithm, "pbkdf2_sha256")
        self.assertEqual(iterations, "1000")
        self.assertEqual(len(bytes.fromhex(salt_hex)), 16)
        self.assertEqual(len(bytes.fromhex(digest_hex)), 32)
        self.assertTrue(security.verify_password("correct horse battery staple", encoded))
        self.assertFalse(security.verify_password("wrong password", encoded))

    def test_explicit_salt_produces_a_repeatable_hash(self) -> None:
        salt = bytes(range(16))
        with patch.object(security, "PASSWORD_ITERATIONS", 1_000):
            first = security._hash_password("repeatable password", salt=salt)
            second = security._hash_password("repeatable password", salt=salt)

        self.assertEqual(first, second)
        self.assertTrue(security.verify_password("repeatable password", first))

    def test_random_salts_produce_different_hashes(self) -> None:
        with patch.object(security, "PASSWORD_ITERATIONS", 1_000):
            first = security._hash_password("same password")
            second = security._hash_password("same password")

        self.assertNotEqual(first, second)
        self.assertTrue(security.verify_password("same password", first))
        self.assertTrue(security.verify_password("same password", second))

    def test_malformed_or_unsupported_hashes_are_rejected(self) -> None:
        malformed_values = (
            "",
            "plain-text-password",
            "bcrypt$1000$00$00",
            "pbkdf2_sha256$not-a-number$00$00",
            "pbkdf2_sha256$1000$not-hex$00",
            "pbkdf2_sha256$1000$00$not-hex",
        )

        for encoded in malformed_values:
            with self.subTest(encoded=encoded):
                self.assertFalse(security.verify_password("password", encoded))


class PasswordStrengthTests(unittest.TestCase):
    def test_accepts_password_at_minimum_and_maximum_lengths(self) -> None:
        self.assertIsNone(security.validate_password_strength("abcdefghij"))
        self.assertIsNone(security.validate_password_strength("x" * 200))

    def test_rejects_password_shorter_than_ten_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少需要 10 个字符"):
            security.validate_password_strength("abcdefghi")

    def test_rejects_password_longer_than_two_hundred_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能超过 200 个字符"):
            security.validate_password_strength("x" * 201)

    def test_rejects_whitespace_only_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能只包含空格"):
            security.validate_password_strength(" \t\n" * 4)


if __name__ == "__main__":
    unittest.main()
