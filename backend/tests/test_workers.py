from __future__ import annotations

import asyncio
import unittest
from app.services import execution_queue, host_dispatch, runtime
from unittest.mock import AsyncMock, patch


class WorkerIsolationTests(unittest.TestCase):
    def test_collection_trial_is_checked_before_claiming_execution(self) -> None:
        from app import collection

        trial = AsyncMock(return_value=False)
        with (
            patch.object(execution_queue.maintenance, "reconcile_reruns"),
            patch.object(execution_queue.task_versions, "notify_next"),
            patch.object(host_dispatch, "notify_next"),
            patch.object(execution_queue.maintenance, "run_next_trial", new=AsyncMock(return_value=False)),
            patch.object(execution_queue.task_versions, "run_next", new=AsyncMock(return_value=False)),
            patch.object(collection, "run_next_trial", new=trial),
            patch.object(execution_queue, "_next_pending_execution_id", side_effect=asyncio.CancelledError),
            self.assertNoLogs(execution_queue.logger, level="ERROR"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(execution_queue._queue_worker_loop())
        trial.assert_awaited_once_with()

    def test_one_environment_failure_does_not_stop_the_next_build(self) -> None:
        build = AsyncMock(side_effect=[RuntimeError("first failed"), asyncio.CancelledError()])
        with (
            patch.object(runtime, "pending_environment_ids", return_value=[1, 2]),
            patch.object(runtime, "build_environment", new=build),
            self.assertLogs(runtime.logger, level="ERROR"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(runtime._environment_worker_loop())

        self.assertEqual([call.args[0] for call in build.await_args_list], [1, 2])

    def test_one_execution_failure_does_not_stop_the_serial_queue(self) -> None:
        run = AsyncMock(side_effect=[RuntimeError("first failed"), asyncio.CancelledError()])
        with (
            patch.object(host_dispatch, "notify_next"),
            patch.object(execution_queue, "_next_pending_execution_id", return_value=9),
            patch.object(execution_queue, "_host_waiting_reason", return_value=""),
            patch.object(execution_queue, "_claim_next_execution", return_value=9),
            patch.object(execution_queue, "run_execution", new=run),
            patch.object(execution_queue, "_mark_execution_worker_failure"),
            patch.object(execution_queue.asyncio, "sleep", new=AsyncMock()),
            self.assertLogs(execution_queue.logger, level="ERROR"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(execution_queue._queue_worker_loop())

        self.assertEqual(run.await_count, 2)

    def test_busy_host_keeps_execution_pending_without_claiming_it(self) -> None:
        next_pending = unittest.mock.MagicMock(side_effect=[9, asyncio.CancelledError()])
        set_waiting = unittest.mock.MagicMock()
        claim = unittest.mock.MagicMock()
        run = AsyncMock()
        with (
            patch.object(host_dispatch, "notify_next"),
            patch.object(execution_queue, "_next_pending_execution_id", new=next_pending),
            patch.object(
                execution_queue,
                "_host_waiting_reason",
                return_value="等待宿主机空闲：Excel 正在运行",
            ),
            patch.object(execution_queue, "_set_execution_waiting", new=set_waiting),
            patch.object(execution_queue, "_claim_next_execution", new=claim),
            patch.object(execution_queue, "run_execution", new=run),
            patch.object(execution_queue.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(execution_queue._queue_worker_loop())

        set_waiting.assert_called_once_with(9, "等待宿主机空闲：Excel 正在运行")
        claim.assert_not_called()
        run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
