from __future__ import annotations

import asyncio
import io
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app import config, database, environments, project_packages, runner, security
from app.main import app
from app.scheduling import scheduler_loop
from app.services import cron_runs, execution_queue, host_dispatch
from tests import test_app_api


def project_zip(files):
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class ProjectPackageTests(unittest.TestCase):
    def test_rejects_unsafe_and_colliding_paths(self):
        for name in ('../outside', 'C:/outside', 'reports/CON.py', 'reports/folder./x',
                     'sibling', 'REPORTS/main.py'):
            files = {'reports/main.py': 'pass', 'sibling/main.py': 'pass', name: 'bad'}
            with self.subTest(path=name), self.assertRaises(ValueError):
                project_packages.inspect(project_zip(files), 'reports/main.py')

    def test_missing_entry_and_invalid_python(self):
        content = project_zip({'report/main.py': 'def:'})
        for entry in ('report/absent.py', 'report/main.py'):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                project_packages.inspect(content, entry)


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object requires Windows')
class ProjectSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        item = self.enterContext(test_app_api.ManagedAppApiTests().fixture())
        work = database.DATA_DIR / 'private-test-work'
        work.mkdir()
        self.enterContext(patch.object(config, 'RPA_APPS_DIR', database.RPA_APPS_DIR))
        self.enterContext(patch.object(config, 'WORK_DIR', work))
        self.enterContext(patch.object(runner, 'RPA_APPS_DIR', database.RPA_APPS_DIR))
        self.enterContext(patch.object(runner, 'RPA_ENVS_DIR', database.RPA_ENVS_DIR))
        self.enterContext(patch.object(runner, 'WORK_DIR', work))
        self.client = TestClient(app, cookies={
            security.SESSION_COOKIE_NAME: security.create_session(item['user']['id'])[0]
        })
        self.addCleanup(self.client.close)
        self.content = project_zip({
            'report/jobs/start.py': (
                'import os, subprocess\n'
                'from pathlib import Path\n'
                'from total import rows\n'
                'root = Path.cwd()\n'
                'assert root.joinpath("data/seed.txt").read_text() == "shop-1"\n'
                'assert root.joinpath("assets/calc.js").is_file()\n'
                'assert root.joinpath("data/empty").is_dir()\n'
                'print(f"DAILY:{rows}")\n'
                'if root.joinpath("fail.flag").exists(): raise RuntimeError("demonstration failure")\n'
                'Path(os.environ["SPIDERFLY_ARTIFACT_DIR"], "report.txt").write_text(str(rows))\n'
            ),
            'report/jobs/total.py': 'from settings import rows\n',
            'report/settings.py': 'rows = 12\n',
            'report/assets/calc.js': 'process.stdout.write("12")\n',
            'report/data/seed.txt': 'shop-1',
            'report/data/empty/': '',
            'report/requirements.txt': '# standard library only\n',
        })

    def upload(self, name, key, trigger_type='interval', config=None, content=None):
        return self.client.post('/api/apps', data={
            'name': name, 'project_entry': 'report/jobs/start.py', 'trigger_type': trigger_type,
            'trigger_config': json.dumps(config or {'value': 1, 'unit': 'seconds'}),
            'request_id': key, 'notify_on_success': 'true'
        }, files={'project_archive': ('report.zip', content or self.content, 'application/zip')})

    def read_execution(self, execution):
        response = self.client.get(f'/api/executions/{execution}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def wait_until(self, predicate, seconds=12):
        async with asyncio.timeout(seconds):
            while not predicate():
                await asyncio.sleep(0.15)

    async def test_missing_project_dependency_names_package_and_blocks_schedule(self):
        package = 'spiderfly-qa-nonexistent-lib'
        archive = project_zip({
            'report/jobs/start.py': 'print("ready")',
            'report/requirements.txt': package + '==1.0',
        })
        response = self.upload('依赖缺失', 'project-dependency-001', content=archive)
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        with patch.dict(os.environ, {'PIP_NO_INDEX': '1', 'PIP_FIND_LINKS': ''}):
            await environments.build_environment(created['id'])
        task = next(item for item in self.client.get('/api/tasks').json()
                    if item['id'] == created['task']['id'])
        self.assertEqual(task['environment_status'], 'failed')
        self.assertIn(package, task['environment_error'])
        self.assertFalse(task['first_environment_ready'])
        self.assertIsNone(task['next_run_at'])

    async def test_import_plan_actual_process_and_logs(self):
        # Directly invoke app routes through HTTP without the production lifespan.
        preview = self.client.post('/api/apps/project-entries', files={
            'project_archive': ('report.zip', self.content, 'application/zip')})
        self.assertEqual(preview.json()['entries'], ['report/jobs/start.py', 'report/jobs/total.py', 'report/settings.py'])
        result = self.upload('项目定时', 'project-scheduled-001')
        self.assertEqual(result.status_code, 201, result.text)
        created = result.json()
        app_id, task = created['id'], created['task']
        task_id = task['id']
        self.assertFalse(task['notify_on_success'])
        self.assertEqual(task['project_entry'], 'report/jobs/start.py')
        self.assertEqual(self.upload('项目定时', 'project-scheduled-001').json()['task']['id'], task_id)
        await environments.build_environment(app_id)
        state = database.fetch_one("SELECT environment_status, environment_error FROM rpa_apps WHERE id=?", (app_id,))
        self.assertEqual(state['environment_status'], 'ready', state)

        async def enqueue(task_id, source):
            return await asyncio.to_thread(host_dispatch.dispatch_scheduled_task, task_id, source)

        scheduler = asyncio.create_task(scheduler_loop(enqueue))
        try:
            await self.wait_until(lambda: execution_queue._next_pending_execution_id() is not None)
        finally:
            scheduler.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await scheduler
        execution = execution_queue._claim_next_execution()
        await runner.run_execution(execution)
        detail = self.read_execution(execution)
        self.assertEqual((detail['trigger_source'], detail['status']), ('schedule', 'success'), detail)
        self.assertEqual(detail['project_entry'], task['project_entry'])
        self.assertTrue(detail['project_version'])
        self.assertEqual(detail['notification_status'], 'disabled')
        self.assertIn('DAILY:12', detail['stdout'])
        self.assertEqual(detail['artifacts']['files'][0]['path'], 'report.txt')

        with database.transaction() as conn:
            package = project_packages.for_app(conn, app_id)
        # Damage the accepted package inside the isolated fixture. The worker must
        # record a launch error instead of running the single-file entry sidecar.
        project_packages.archive_path(database.RPA_APPS_DIR / str(app_id), package['digest']).write_bytes(b'changed')
        execution = execution_queue._enqueue_task_sync(task_id)
        self.assertEqual(execution_queue._claim_next_execution(), execution)
        await runner.run_execution(execution)
        failed = self.read_execution(execution)
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('项目包校验失败', failed['stderr'])
        self.assertEqual(self.read_execution(detail['id'])['status'], 'success')

    async def test_fixed_point_persists_overlap_as_skipped(self):
        result = self.upload('固定时间项目', 'project-scheduled-002', 'daily', {'time': '09:00'})
        self.assertEqual(result.status_code, 201, result.text)
        created = result.json()
        task_id = created['task']['id']
        await environments.build_environment(created['id'])
        row = database.fetch_one("SELECT next_run_at,first_environment_ready FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(row['first_environment_ready'], 1, row)
        due = datetime.fromisoformat(row['next_run_at'])
        first = cron_runs.register_due(task_id, due)
        self.assertEqual(first['state'], 'pending')
        cron_runs.dispatch_pending(execution_queue._enqueue_task_in_transaction)
        later = cron_runs.register_due(task_id, due + timedelta(days=1))
        self.assertEqual(later['state'], 'skipped')
        self.assertIn('上一轮', later['reason'])
        response = self.client.get('/api/schedules/cron-runs', params={'task_id': task_id})
        self.assertEqual(response.status_code, 200, response.text)
        last = response.json()['items'][0]
        self.assertEqual(last['task']['trigger_type'], 'daily')
        self.assertEqual(last['state'], 'skipped')
