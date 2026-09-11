import asyncio
import json
import os
import socket
import unittest
from unittest.mock import AsyncMock, patch

from app import collection, dp_runtime, maintenance, maintenance_runtime, database as db
from app.host_runtime import HostBusyStatus
from tests import test_ai_agent, test_maintenance, test_task_versions

CONTRACT = {'effects': 'artifacts_only', 'file': 'rows.csv', 'format': 'csv',
    'min_rows': 5, 'max_rows': 5, 'required': ['title', 'url'], 'unique_by': ['url'],
    'urls': ['https://www.zhihu.com/knowledge-plan/hot-question/hot/0/week']}
SOURCE = dp_runtime.MARKER + '\nSPIDERFLY_ACCEPTANCE = ' + repr(CONTRACT) + '''
import os, csv
from pathlib import Path
from DrissionPage import Chromium, ChromiumOptions
co = ChromiumOptions(read_file=False).set_address(os.environ['SPIDERFLY_BROWSER_ADDRESS']).existing_only().headless()
page = Chromium(co).latest_tab
page.get(SPIDERFLY_ACCEPTANCE['urls'][0])
page.wait.eles_loaded('css:a.css-2ietpx', timeout=15)
rows = [{'title': item.text, 'url': item.attr('href')} for item in page.eles('css:a.css-2ietpx')[:5]]
with (Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'rows.csv').open('w', newline='', encoding='utf-8-sig') as stream:
    writer = csv.DictWriter(stream, fieldnames=['title', 'url'])
    writer.writeheader()
    writer.writerows(rows)
'''
REQUIREMENTS = 'DrissionPage==4.1.1.4'


class DPTests(unittest.TestCase):
    turn = test_ai_agent.AgentTests.turn

    def setUp(self):
        from app import execution_results
        test_ai_agent.AgentTests.setUp(self)
        self.stack.enter_context(patch.object(execution_results, 'EXECUTIONS_DIR', self.root / 'executions'))
        self.stack.enter_context(patch.object(maintenance, 'JOB_ROOT', self.root / 'maintenance'))

    def test_wrong_port_and_lifecycle_configuration_rejected(self):
        collection.validate(SOURCE, REQUIREMENTS)
        for extra in ("\nco.auto_port()", "\nco.set_local_port(9222)", "\npage.quit()"):
            with self.assertRaisesRegex(ValueError, '平台管理'):
                collection.validate(SOURCE + extra, REQUIREMENTS)
        with self.assertRaises(ValueError):
            collection.validate(SOURCE.replace('.existing_only().headless()', ''), REQUIREMENTS)
        with self.assertRaises(ValueError):
            collection.validate(SOURCE, 'DrissionPage==4.1.1.2')
        with self.assertRaises(ValueError):
            collection.validate(SOURCE.replace("os.environ['SPIDERFLY_BROWSER_ADDRESS']", "'127.0.0.1:9222'") + '\n# SPIDERFLY_BROWSER_ADDRESS', REQUIREMENTS)
        with self.assertRaises(ValueError):
            collection.validate(SOURCE.replace('Chromium(co)', 'Chromium(9222)'), REQUIREMENTS)

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_DP') == '1', 'requires native DP runtime')
    def test_real_stop_releases_configured_port(self):
        async def scenario():
            stop = asyncio.Event()
            running = asyncio.create_task(dp_runtime.execute('import time\ntime.sleep(60)', hosts=set(), stop=stop))
            await asyncio.sleep(3)
            stop.set()
            with self.assertRaises(InterruptedError):
                await running
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', dp_runtime.MANAGED_BROWSER_PORT))
        asyncio.run(scenario())

    def test_occupied_port_does_not_launch_or_attach(self):
        with patch.object(dp_runtime, 'ready'), patch('app.host_runtime.check_host_busy',
                return_value=HostBusyStatus(True, 'browser', '端口占用')), patch.object(asyncio, 'create_subprocess_exec') as launch:
            with self.assertRaisesRegex(ValueError, '没有接管'):
                asyncio.run(dp_runtime.execute(SOURCE, hosts={'www.zhihu.com'}))
            launch.assert_not_called()

    def test_trial_and_managed_run_dispatch_to_same_executor(self):
        result = {'exit_code': 0, 'files': {}, 'log': '', 'result': '', 'network': {'requests': 1}}
        async def scenario():
            with patch.object(dp_runtime, 'execute', new=AsyncMock(return_value=result)) as run:
                await collection.execute(SOURCE, REQUIREMENTS, CONTRACT)
                await maintenance.execute_profile({'policy': {'runtime': dp_runtime.PROFILE},
                    'requirements': REQUIREMENTS, 'contract': CONTRACT}, SOURCE, pages={})
                self.assertEqual(run.await_count, 2)
                self.assertEqual(run.await_args_list[0], run.await_args_list[1])
        asyncio.run(scenario())

    def test_dp_observation_waits_for_queue_and_uses_native_executor(self):
        from app import dp_probe
        turn = self.turn(content='DP ' + CONTRACT['urls'][0])
        result = {'exit_code': 0, 'files': {'probe.json': b'{"title":"observed"}'},
                  'log': '', 'network': {'requests': 1, 'browser_address': '127.0.0.1:9123'}}
        async def scenario():
            with patch.object(dp_runtime, 'execute', new=AsyncMock(return_value=result)) as run:
                waiter = asyncio.create_task(dp_probe.request(CONTRACT['urls'][0], 'css:body', turn['id'], {'www.zhihu.com'}))
                await asyncio.sleep(.02)
                run.assert_not_called()
                self.assertTrue(await dp_probe.run_next())
                report = await waiter
                self.assertEqual(report['title'], 'observed')
                self.assertEqual(report['status'], 'success')
                self.assertIn('Chromium(co).latest_tab', run.call_args.args[0])
        asyncio.run(scenario())

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_DP') == '1', 'requires native DP runtime')
    def test_real_dp_probe_observes_question_links(self):
        from app import dp_probe
        turn = self.turn(content='DP ' + CONTRACT['urls'][0])
        async def scenario():
            waiter = asyncio.create_task(dp_probe.request(CONTRACT['urls'][0], 'css:body', turn['id'], {'www.zhihu.com'}))
            await asyncio.sleep(.03)
            await dp_probe.run_next()
            result = await waiter
            self.assertEqual(result['status'], 'success', result)
            self.assertTrue(any('/question/' in (link['href'] or '') for link in result['links']))
        asyncio.run(scenario())

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_DP') == '1', 'requires Windows DP and a free configured task port')
    def test_real_queue_trial_and_execution_same_port(self):
        from app import ai_agent, runner
        from app.services import execution_queue
        # Use independent synthetic task data; no formal data or notifications.
        turn = self.turn(content='用DP创建知乎周榜前五条任务 ' + CONTRACT['urls'][0])
        draft = ai_agent.save_draft(self.thread['id'], turn['id'], {'name': 'DP test',
            'description': 'isolated verification', 'source': SOURCE, 'requirements': REQUIREMENTS})
        async def scenario():
            waiter = asyncio.create_task(collection.request_trial(draft['draft_id'], self.thread, turn['id'], {'www.zhihu.com'}))
            await asyncio.sleep(.05)
            self.assertEqual(db.fetch_one('SELECT status FROM ai_collection_trials')['status'], 'pending')
            await collection.run_next_trial()
            trial = await waiter
            self.assertEqual(trial['status'], 'success', trial)
            self.assertEqual(trial['rows'], 5)
            self.assertEqual(trial['network']['browser_address'], f'127.0.0.1:{dp_runtime.MANAGED_BROWSER_PORT}')
            # Full runner path, including snapshot selection and independent acceptance.
            task_id = db.execute("INSERT INTO tasks(name,script_path,enabled,created_by,notify_on_success,notify_on_failure,created_at,updated_at) VALUES(?,'unused.py',1,?,0,0,?,?)",
                ('DP test', self.user['id'], db.utc_now(), db.utc_now()))
            execution_id = db.execute("INSERT INTO executions(task_id,status,trigger_source,created_at) VALUES(?,'running','manual',?)", (task_id, db.utc_now()))
            snapshot = json.dumps({'task_id': task_id, 'policy': {'runtime': dp_runtime.PROFILE},
                'source': SOURCE, 'requirements': REQUIREMENTS, 'contract': maintenance_runtime.validate_contract(CONTRACT), 'template': ''})
            task = {'id': task_id, 'name': 'DP test', 'maintenance_snapshot': snapshot}
            control = runner.ExecutionControl()
            await maintenance.run_managed_execution(execution_id, task, control)
            execution = db.fetch_one('SELECT * FROM executions WHERE id=?', (execution_id,))
            self.assertEqual(execution['status'], 'success', execution['stderr'])
            self.assertIn(f'127.0.0.1:{dp_runtime.MANAGED_BROWSER_PORT}', execution['stdout'])
            print('DP real trial + managed execution:', trial['network']['browser_address'], trial['rows'])
        asyncio.run(scenario())


class DPVersionTests(unittest.TestCase):
    setUp = test_task_versions.VersionTests.setUp

    def test_candidate_preserves_dp_profile_after_validation_and_publish(self):
        from app import task_versions as v
        update = v.submit(self.task_id, SOURCE, REQUIREMENTS, {'acceptance': CONTRACT}, '改为DP采集知乎周榜', self.user['id'], base_id=self.base)
        detail = db.fetch_one('SELECT * FROM task_version_details WHERE version_id=(SELECT version_id FROM task_version_updates WHERE id=?)', (update['id'],))
        self.assertEqual(detail['runtime'], dp_runtime.PROFILE)
        result = {'exit_code': 0, 'log': 'verified', 'result': '',
            'files': {'rows.csv': b'title,url\na,1\nb,2\nc,3\nd,4\ne,5\n'}, 'network': {'requests': 1}}
        with patch.object(dp_runtime, 'execute', new=AsyncMock(return_value=result)):
            asyncio.run(v.run_next())
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates WHERE id=?', (update['id'],))['status'], 'ready')
        from starlette.requests import Request
        v.activate_update(update['id'], Request({'type': 'http', 'headers': []}), self.user)
        self.assertEqual(db.fetch_one('SELECT runtime FROM maintenance_policies WHERE task_id=?', (self.task_id,))['runtime'], dp_runtime.PROFILE)

    def test_first_snapshot_routes_dp_to_managed_executor(self):
        # Delete only this fixture's policy so it follows initial task creation.
        db.execute('DELETE FROM maintenance_policies WHERE task_id=?', (self.task_id,))
        self.path.write_text(SOURCE, encoding='utf-8')
        db.execute('UPDATE rpa_apps SET requirements_text=? WHERE id=(SELECT app_id FROM tasks WHERE id=?)', (REQUIREMENTS, self.task_id))
        with db.transaction() as conn:
            snapshot = json.loads(maintenance.capture_snapshot(conn, self.task_id))
        self.assertEqual(snapshot['policy']['runtime'], dp_runtime.PROFILE)
