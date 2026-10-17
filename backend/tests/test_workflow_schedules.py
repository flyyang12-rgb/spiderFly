"""Isolated workflow plan acceptance, races, restart and timetable evidence."""
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.services import workflows, workflow_schedules as plans
from tests import test_workflows


class WorkflowScheduleTests(unittest.TestCase):
    def setUp(self):
        self.h = test_workflows.WorkflowTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.conn = self.h.conn
        self.clock = '2026-10-14T01:00:00+00:00'
        self.enterContext(patch.object(plans, 'transaction', self.h.transaction))
        self.enterContext(patch.object(workflows, 'utc_now', lambda: self.clock))
        self.enterContext(patch.object(plans, 'utc_now', lambda: self.clock))
        self.flow = self.h.definition()

    def enable(self, trigger='cron', config=None, now='2026-10-14T00:59:00+00:00'):
        result = plans.update_plan(self.conn, self.flow['id'], trigger,
            config or {'expression': '0 9 * * *'}, True, self.flow['revision'], 7, now)
        self.conn.commit()
        self.flow = result
        return result

    def due(self, now=None, **kwargs):
        return plans.register_due(self.flow['id'], now or self.clock, **kwargs)

    def test_occurrence_and_complete_three_step_snapshot_are_atomic_and_unique(self):
        self.enable()
        first = self.due()
        run = workflows.get_run(self.conn, first['run_id'])
        self.assertEqual(run['source'], 'schedule')
        self.assertEqual(run['scheduled_for'], self.clock)
        self.assertEqual(len(run['steps']), 3)
        self.assertEqual(self.h.count_executions(), 1)
        self.assertIsNone(self.due())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_triggers').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 1)
        frozen = json.loads(self.conn.execute('SELECT snapshot FROM workflow_steps WHERE run_id=? AND position=3', (run['id'],)).fetchone()[0])
        (self.h.apps/'3/main.py').write_text("print('later edit')", encoding='utf-8')
        self.assertIn('synthetic-3', __import__('pathlib').Path(frozen['script_path_snapshot']).read_text())

    def test_active_round_skips_following_plan_points_without_backlog(self):
        self.enable(config={'expression': '* * * * *'})
        first = self.due()
        second = self.due('2026-10-14T01:01:00+00:00')
        self.assertEqual(second['state'], 'skipped')
        self.assertIsNone(second['run_id'])
        self.assertIn('上一轮', second['reason'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 1)
        self.assertEqual(first['state'], 'active')

    def test_offline_accepts_only_latest_slot_with_merge_range(self):
        self.enable(config={'expression': '* * * * *'})
        record = self.due('2026-10-14T01:10:45+00:00')
        self.assertEqual(record['scheduled_for'], '2026-10-14T01:10:00+00:00')
        self.assertEqual(record['coalesced_from'], '2026-10-14T01:00:00+00:00')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 1)
        self.assertEqual(workflows.get_definition(self.conn, self.flow['id'])['next_run_at'], '2026-10-14T01:11:00+00:00')

    def test_daily_weekly_and_cron_share_preview_and_latest_semantics(self):
        for kind, config in [('daily', {'time':'09:00'}), ('weekly', {'time':'09:00','weekdays':[3]}), ('cron', {'expression':'0 9 * * WED'})]:
            result = plans.preview(kind, config, '2026-10-14T00:59:00+00:00')
            self.assertEqual(result['times'][0], self.clock)
            self.assertEqual(len(result['times']), 5)
            self.assertEqual(plans.latest_slot(kind, config, datetime(2026,10,14,1,0,tzinfo=timezone.utc)), self.clock)

    def test_preview_captures_one_clock_baseline_when_after_is_omitted(self):
        with patch.object(plans, '_time', wraps=plans._time) as clock:
            result = plans.preview('daily', {'time':'09:00'})
        self.assertEqual(sum(call.args == (None,) for call in clock.call_args_list), 1)
        self.assertGreater(result['times'][0], result['after'])

    def test_two_way_conflict_disabled_member_is_not_a_workaround(self):
        self.conn.execute("UPDATE tasks SET trigger_type='daily',enabled=0 WHERE id=2")
        with self.assertRaises(HTTPException) as caught:
            self.enable()
        self.assertIn('任务2', caught.exception.detail)
        self.conn.execute("UPDATE tasks SET trigger_type='manual',enabled=1 WHERE id=2")
        self.enable()
        with self.assertRaises(HTTPException) as caught:
            plans.assert_task_plan_allowed(self.conn, 2, 'cron')
        self.assertIn('合成流程', caught.exception.detail)
        plans.assert_task_plan_allowed(self.conn, 2, 'manual')

    def test_editing_members_of_enabled_flow_revalidates_plan_conflicts(self):
        self.enable()
        self.conn.execute("UPDATE tasks SET trigger_type='weekly' WHERE id=3")
        with self.assertRaises(HTTPException):
            workflows.update_definition(self.conn, self.flow['id'], 'updated', '', [1,3], 7, expected_revision=self.flow['revision'])
        self.assertEqual([row['task_id'] for row in workflows.get_definition(self.conn, self.flow['id'])['steps']], [1,2,3])

    def test_close_does_not_cancel_accepted_round_and_reenable_ignores_closed_gap(self):
        self.enable()
        first = self.due()
        closed = plans.update_plan(self.conn, self.flow['id'], 'cron', {'expression':'0 9 * * *'}, False, self.flow['revision'], 7, self.clock)
        self.conn.commit()
        self.assertEqual(workflows.get_run(self.conn, first['run_id'])['status'], 'running')
        self.assertIsNone(self.due('2026-10-16T02:00:00+00:00'))
        reopened = plans.update_plan(self.conn, self.flow['id'], 'cron', {'expression':'0 9 * * *'}, True, closed['revision'], 7, '2026-10-16T02:00:00+00:00')
        self.assertEqual(reopened['next_run_at'], '2026-10-17T01:00:00+00:00')
        self.assertTrue(all(row[0]==1 for row in self.conn.execute('SELECT enabled FROM tasks')))

    def test_stale_scanner_cannot_accept_after_schedule_edit(self):
        old = self.enable()
        changed = plans.update_plan(self.conn, self.flow['id'], 'daily', {'time':'10:00'}, True, old['revision'], 7, '2026-10-14T00:59:00+00:00')
        self.conn.commit()
        self.assertIsNone(self.due(expected_next=old['next_run_at'], expected_revision=old['schedule_revision']))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 0)
        self.assertEqual(changed['next_run_at'], '2026-10-14T02:00:00+00:00')

    def test_freeze_error_is_visible_and_no_partial_execution_or_snapshot_remains(self):
        self.enable()
        (self.h.apps/'3/template.xlsx').unlink()
        record = self.due()
        self.assertEqual(record['state'], 'error')
        self.assertIn('模板', record['reason'])
        self.assertIsNone(record['run_id'])
        self.assertEqual(self.h.count_executions(), 0)
        self.assertEqual(list(self.h.snapshots.iterdir()), [])
        self.assertGreater(workflows.get_definition(self.conn, self.flow['id'])['next_run_at'], self.clock)

    def test_rolled_back_plan_advance_does_not_leave_round_or_snapshot(self):
        self.enable()
        self.conn.execute("CREATE TRIGGER reject_advance BEFORE UPDATE OF last_triggered_at ON workflows BEGIN SELECT RAISE(ABORT,'synthetic crash'); END")
        self.conn.commit()
        with self.assertRaises(__import__('sqlite3').IntegrityError):
            self.due()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM workflow_triggers').fetchone()[0], 0)
        self.assertEqual(self.h.count_executions(), 0)
        self.assertEqual(list(self.h.snapshots.iterdir()), [])
        self.assertEqual(workflows.get_definition(self.conn, self.flow['id'])['next_run_at'], self.clock)

    def test_timetable_uses_ledger_for_past_and_distinguishes_workflow(self):
        self.enable(config={'expression':'0 9,14 * * *'})
        record = self.due()
        table = plans.timetable(self.conn, '2026-10-13T16:00:00+00:00', '2026-10-14T16:00:00+00:00', now='2026-10-14T01:01:00+00:00')
        self.assertEqual(len(table['entries']), 2)
        self.assertTrue(all(row['object_type']=='workflow' for row in table['entries']))
        self.assertEqual(table['entries'][0]['run_id'], record['run_id'])
        self.assertEqual(table['entries'][1]['plannedAt'], '2026-10-14T06:00:00+00:00')
        closed = plans.update_plan(self.conn, self.flow['id'], 'daily', {'time':'15:00'}, False, self.flow['revision'], 7)
        history = plans.history(self.conn, self.flow['id'])
        self.assertEqual(history['items'][0]['plannedAt'], self.clock)
        self.assertFalse(closed['schedule_enabled'])

    def test_terminal_without_cleanup_does_not_release_scheduled_round(self):
        self.enable(config={'expression':'* * * * *'})
        record = self.due()
        run = workflows.get_run(self.conn, record['run_id'])
        self.h.complete(run['steps'][0]['execution_id'], confirm=False)
        workflows.reconcile(self.conn)
        plans.reconcile(self.conn)
        self.assertEqual(self.conn.execute('SELECT state FROM workflow_triggers WHERE id=?', (record['id'],)).fetchone()[0], 'active')
        self.assertEqual(self.due('2026-10-14T01:01:00+00:00')['state'], 'skipped')

    def test_restart_reuses_pending_execution_and_registered_slot(self):
        self.enable()
        record = self.due()
        workflows.reconcile(self.conn, recover=True)
        plans.reconcile(self.conn)
        self.assertIsNone(self.due())
        self.assertEqual(self.h.count_executions(), 1)
        self.assertEqual(workflows.get_run(self.conn, record['run_id'])['steps'][0]['execution_id'], 1)

    def test_archive_closes_future_plan_and_preserves_trigger_history(self):
        self.enable()
        record = self.due()
        with self.assertRaises(HTTPException):
            workflows.archive_definition(self.conn, self.flow['id'], 7)
        workflows.request_stop(record['run_id'], 7, conn=self.conn)
        workflows.reconcile(self.conn)
        plans.reconcile(self.conn)
        archived = workflows.archive_definition(self.conn, self.flow['id'], 7)
        self.assertFalse(archived['schedule_enabled'])
        self.assertEqual(plans.history(self.conn, self.flow['id'])['items'][0]['state'], 'cancelled')
