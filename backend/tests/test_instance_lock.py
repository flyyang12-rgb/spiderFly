from __future__ import annotations

import unittest
from app.instance_lock import AlreadyRunningError, acquire_instance_lock
from app.services import runtime
from unittest.mock import Mock, patch
from uuid import uuid4


class InstanceLockTests(unittest.TestCase):
    def test_second_scheduler_is_rejected_until_first_releases(self) -> None:
        key = f"spiderfly-test-{uuid4()}"
        first = acquire_instance_lock(key=key)
        try:
            with self.assertRaises(AlreadyRunningError):
                acquire_instance_lock(key=key)
        finally:
            first.close()

        replacement = acquire_instance_lock(key=key)
        replacement.close()
        replacement.close()


class StartupGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_start_stops_before_database_recovery(self) -> None:
        with (
            patch.object(
                runtime,
                "acquire_instance_lock",
                side_effect=AlreadyRunningError("already running"),
            ),
            patch.object(runtime, "init_db") as init_db,
            patch.object(runtime, "ensure_bootstrap_admin") as ensure_admin,
            patch.object(runtime, "reconcile_schedules") as reconcile,
        ):
            with self.assertRaises(AlreadyRunningError):
                await runtime.startup()

        init_db.assert_not_called()
        ensure_admin.assert_not_called()
        reconcile.assert_not_called()

    async def test_startup_failure_releases_the_lock(self) -> None:
        fake_lock = Mock()
        with (
            patch.object(runtime, "acquire_instance_lock", return_value=fake_lock),
            patch.object(runtime, "init_db", side_effect=RuntimeError("boom")),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await runtime.startup()

        fake_lock.close.assert_called_once_with()
        self.assertIsNone(runtime._instance_lock)

    async def test_legacy_cleanup_failure_stops_before_scheduler_and_releases_lock(self) -> None:
        fake_lock = Mock()
        with (
            patch.object(runtime, "acquire_instance_lock", return_value=fake_lock),
            patch.object(runtime, "init_db"),
            patch.object(
                runtime,
                "cleanup_legacy_task_program_model",
                side_effect=RuntimeError("cleanup blocked"),
            ),
            patch.object(runtime, "ensure_bootstrap_admin") as ensure_admin,
            patch.object(runtime, "reconcile_schedules") as reconcile,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup blocked"):
                await runtime.startup()

        ensure_admin.assert_not_called()
        reconcile.assert_not_called()
        fake_lock.close.assert_called_once_with()
        self.assertIsNone(runtime._instance_lock)


if __name__ == "__main__":
    unittest.main()
