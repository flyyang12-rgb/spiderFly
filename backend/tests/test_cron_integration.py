from datetime import datetime, timezone
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app import database, environments, maintenance, native_process, security, task_versions
from app.api import executions, schedules, tasks
from app.schemas import TaskPatch, TaskPayload
from app.services import cron_runs, execution_queue
from tests import test_app_api


class CronIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.helper = test_app_api.ManagedAppApiTests()

    def task(self, fixture):
        task = self.helper.create_task(fixture['app_id'], fixture['user'])
        database.execute('''UPDATE tasks SET trigger_type='cron',trigger_config='{"expression":"*/10 * * * *"}',
            next_run_at='2026-10-13T01:00:00+00:00',created_at='2026-10-13T00:00:00+00:00' WHERE id=?''', (task['id'],))
        return task

    def test_preview_beijing_and_strict_boundary(self):
        result = schedules.preview(schedules.CronPreview(expression='0 9 * * MON', after=datetime(2026, 10, 19, 1, tzinfo=timezone.utc)), {})
        self.assertEqual(result['times'][0], '2026-10-26T01:00:00+00:00')
        self.assertEqual(len(result['times']), 5)
        for expression in ['0 9 1 * MON', '0 9 31 2 *', '* * * * * *']:
            with self.assertRaises(HTTPException):
                schedules.preview(schedules.CronPreview(expression=expression), {})

    def test_dispatch_retry_has_one_execution_and_one_slot(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            first = cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00')
            self.assertEqual(cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction), 1)
            self.assertEqual(cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction), 0)
            cron_runs.register_due(task['id'], '2026-10-13T01:20:00+00:00')
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)
            skipped = database.fetch_one("SELECT * FROM cron_rounds WHERE state='skipped'")
            self.assertEqual(skipped['coalesced_from'], '2026-10-13T01:10:00+00:00')
            self.assertEqual(skipped['scheduled_for'], '2026-10-13T01:20:00+00:00')
            self.assertEqual(database.fetch_one('SELECT * FROM cron_rounds WHERE id=?', (first['id'],))['state'], 'active')

    def test_changed_revision_and_disabled_scan_are_not_accepted(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            database.execute('UPDATE tasks SET version=version+1 WHERE id=?', (task['id'],))
            self.assertIsNone(cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00',
                '2026-10-13T01:00:00+00:00', task['version']))
            database.execute('UPDATE tasks SET enabled=0 WHERE id=?', (task['id'],))
            self.assertIsNone(cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00'))
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM cron_rounds')['n'], 0)

    def test_cancel_unstarted_releases_and_network_retry_returns_original(self):
        with self.helper.fixture() as item, patch.object(security, 'write_audit'):
            task = self.task(item)
            request_id = 'cron-network-retry-012345'
            execution_id = execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'], request_id)
            with self.assertRaises(HTTPException):
                execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'], 'cron-new-request-012345')
            executions.cancel_execution(execution_id, object(), item['user'])
            cron_runs.reconcile(now='2026-10-13T01:00:00+00:00')
            database.execute('UPDATE tasks SET enabled=0 WHERE id=?', (task['id'],))
            response = asyncio.run(tasks.run_task(task['id'], object(), tasks.ManualRunPayload(request_id=request_id), item['user']))
            self.assertEqual(response['execution_id'], execution_id)
            self.assertEqual(response['status'], 'cancelled')
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 1)

    def test_terminal_status_without_cleanup_still_protects_delete(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            execution_id = execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'])
            database.execute("UPDATE executions SET status='failed' WHERE id=?", (execution_id,))
            cron_runs.decide_maintenance(execution_id)
            self.assertEqual(cron_runs.reconcile(), 0)
            with self.assertRaisesRegex(RuntimeError, '收尾'):
                environments.delete_task_bundle(task['id'])

    def test_timetable_uses_exact_slots_and_shows_disabled_history(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00')
            cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction)
            cron_runs.register_due(task['id'], '2026-10-13T01:10:00+00:00')
            database.execute('UPDATE tasks SET enabled=0 WHERE id=?', (task['id'],))
            result = schedules.cron_timetable(datetime(2026,10,13,1,tzinfo=timezone.utc), datetime(2026,10,13,2,tzinfo=timezone.utc), {})
            self.assertEqual(len(result['entries']), 2)
            self.assertIsNotNone(result['entries'][0]['execution'])
            self.assertEqual(result['entries'][1]['state'], 'skipped')
            self.assertIsNone(result['entries'][1]['execution'])

    def test_remote_cron_configuration_is_rejected(self):
        with self.helper.fixture() as item, patch.object(security, 'write_audit'), patch.object(tasks, 'validate_target_host'):
            with self.assertRaisesRegex(HTTPException, '主控本机'):
                tasks.create_task(TaskPayload(name='remote-cron', app_id=item['app_id'], trigger_type='cron',
                    trigger_config={'expression':'0 9 * * *'}, target_host_id=1), object(), item['user'])

    def test_pending_round_protects_configuration_before_dispatch(self):
        with self.helper.fixture() as item, patch.object(security, 'write_audit'):
            task = self.task(item)
            cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00')
            with self.assertRaisesRegex(HTTPException, '已登记'):
                tasks.update_task(task['id'], TaskPatch(name='changed'), object(), item['user'])
            tasks.update_task(task['id'], TaskPatch(enabled=False), object(), item['user'])
            cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction)
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 0)

    def test_edited_schedule_does_not_invent_past_missed_points(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            database.execute("UPDATE tasks SET next_run_at='2090-10-14T01:00:00+00:00' WHERE id=?", (task['id'],))
            result = schedules.cron_timetable(datetime(2090,10,13,tzinfo=timezone.utc), datetime(2090,10,14,tzinfo=timezone.utc), {})
            self.assertEqual(result['entries'], [])

    def test_cleanup_check_exception_blocks_worker_claim(self):
        with (
            patch.object(native_process, 'recover_settlements', side_effect=RuntimeError('synthetic check failure')),
            patch.object(execution_queue, '_next_pending_execution_id', return_value=42),
            patch.object(execution_queue, '_set_execution_waiting') as waiting,
            patch.object(execution_queue, '_claim_next_execution') as claim,
            patch.object(execution_queue.maintenance, 'run_next_trial', new=AsyncMock()) as trial,
            patch.object(execution_queue.asyncio, 'sleep', new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(execution_queue._queue_worker_loop())
            claim.assert_not_called()
            trial.assert_not_called()
            self.assertIn('收尾核对失败', waiting.call_args.args[1])

    def test_unsupported_runtime_blocks_save_and_dispatch_but_allows_disable(self):
        with self.helper.fixture() as item, patch.object(security, 'write_audit'):
            task = self.helper.create_task(item['app_id'], item['user'])
            with database.transaction() as conn:
                maintenance.capture_snapshot(conn, task['id'])
            database.execute("UPDATE maintenance_policies SET runtime='drissionpage-v1' WHERE task_id=?", (task['id'],))
            with self.assertRaisesRegex(HTTPException, '暂不支持'):
                tasks.update_task(task['id'], TaskPatch(trigger_type='cron', trigger_config={'expression':'0 9 * * *'}), object(), item['user'])
            self.assertEqual(database.fetch_one('SELECT trigger_type FROM tasks WHERE id=?', (task['id'],))['trigger_type'], 'manual')
            database.execute("UPDATE tasks SET trigger_type='cron',trigger_config='{\"expression\":\"0 9 * * *\"}' WHERE id=?", (task['id'],))
            with self.assertRaisesRegex(HTTPException, '暂不支持'):
                execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'])
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'], 0)
            self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM cron_rounds')['n'], 0)
            tasks.update_task(task['id'], TaskPatch(enabled=False), object(), item['user'])

    def test_version_publish_cannot_rewrite_accepted_cron_execution(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            execution_id = execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'])
            before = database.fetch_one('SELECT maintenance_snapshot,script_path_snapshot FROM executions WHERE id=?', (execution_id,))
            with database.transaction() as conn:
                saved = maintenance.task_record(conn, task['id'])
                candidate = maintenance.version(conn, saved, "print('new version')\n", approved=True, kind='upload')
                task_versions.ensure_details(conn, saved, candidate, profile='native')
                task_versions.publish(conn, task['id'], candidate['id'])
            after = database.fetch_one('SELECT maintenance_snapshot,script_path_snapshot FROM executions WHERE id=?', (execution_id,))
            self.assertEqual(after, before)
            self.assertNotEqual(database.fetch_one('SELECT script_path FROM tasks WHERE id=?', (task['id'],))['script_path'], before['script_path_snapshot'])

    def test_version_publish_waits_for_registered_but_not_dispatched_round(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            cron_runs.register_due(task['id'], '2026-10-13T01:00:00+00:00')
            with database.transaction() as conn:
                with self.assertRaisesRegex(ValueError, '已登记'):
                    task_versions.publish(conn, task['id'], 9999)

    def test_deleted_execution_preserves_settled_result_in_timetable(self):
        with self.helper.fixture() as item:
            task = self.task(item)
            execution_id = execution_queue._enqueue_task_sync(task['id'], 'manual', item['user']['id'])
            database.execute("UPDATE executions SET status='success',result_message='合成成功',ended_at=? WHERE id=?", (database.utc_now(), execution_id))
            with database.transaction() as conn:
                native_process.ensure_schema(conn)
                conn.execute('''INSERT INTO execution_settlements(execution_id,job_name,processes_gone,workspace_cleanup_confirmed,runner_returned,cleanup_confirmed,created_at,updated_at)
                    VALUES(?, ?,1,1,1,1,?,?)''', (execution_id,'synthetic-settlement',database.utc_now(),database.utc_now()))
            cron_runs.reconcile()
            database.execute('DELETE FROM executions WHERE id=?', (execution_id,))
            result = next(row for row in schedules.cron_history(task['id'], None, {})['items'] if row['execution'] is None and row['state'] == 'success')
            self.assertEqual(result['state'], 'success')
            self.assertIsNone(result['execution'])


if __name__ == '__main__':
    unittest.main()
