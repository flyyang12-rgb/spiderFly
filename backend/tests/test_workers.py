from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import main


class WorkerIsolationTests(unittest.TestCase):
    def test_one_environment_failure_does_not_stop_the_next_build(self) -> None:
        build = AsyncMock(side_effect=[RuntimeError("first failed"), asyncio.CancelledError()])
        with (
            patch.object(main, "pending_environment_ids", return_value=[1, 2]),
            patch.object(main, "build_environment", new=build),
            self.assertLogs(main.logger, level="ERROR"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main._environment_worker_loop())

        self.assertEqual([call.args[0] for call in build.await_args_list], [1, 2])

    def test_one_execution_failure_does_not_stop_the_serial_queue(self) -> None:
        run = AsyncMock(side_effect=[RuntimeError("first failed"), asyncio.CancelledError()])
        with (
            patch.object(main, "_next_pending_execution_id", return_value=9),
            patch.object(main, "_host_waiting_reason", return_value=""),
            patch.object(main, "_claim_next_execution", return_value=9),
            patch.object(main, "run_execution", new=run),
            patch.object(main, "_mark_execution_worker_failure"),
            patch.object(main.asyncio, "sleep", new=AsyncMock()),
            self.assertLogs(main.logger, level="ERROR"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main._queue_worker_loop())

        self.assertEqual(run.await_count, 2)

    def test_busy_host_keeps_execution_pending_without_claiming_it(self) -> None:
        next_pending = unittest.mock.MagicMock(side_effect=[9, asyncio.CancelledError()])
        set_waiting = unittest.mock.MagicMock()
        claim = unittest.mock.MagicMock()
        run = AsyncMock()
        with (
            patch.object(main, "_next_pending_execution_id", new=next_pending),
            patch.object(
                main,
                "_host_waiting_reason",
                return_value="等待宿主机空闲：Excel 正在运行",
            ),
            patch.object(main, "_set_execution_waiting", new=set_waiting),
            patch.object(main, "_claim_next_execution", new=claim),
            patch.object(main, "run_execution", new=run),
            patch.object(main.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main._queue_worker_loop())

        set_waiting.assert_called_once_with(9, "等待宿主机空闲：Excel 正在运行")
        claim.assert_not_called()
        run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
