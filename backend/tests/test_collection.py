import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import AsyncMock, patch

from app import collection, ai_agent as agent, database as db, maintenance_runtime as runtime
from tests import test_ai_agent
from tests import test_maintenance

CONTRACT = {'effects': 'artifacts_only', 'file': 'books.csv', 'format': 'csv',
            'min_rows': 2, 'max_rows': 2, 'required': ['title'], 'unique_by': ['title'],
            'urls': ['https://books.toscrape.com/']}
SOURCE = collection.MARKER + '\nSPIDERFLY_ACCEPTANCE = ' + repr(CONTRACT) + '''
from spiderfly_collection import get
from pathlib import Path
import os, csv
titles = get('https://books.toscrape.com/').css('h3 a::attr(title)').getall()[:2]
with (Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'books.csv').open('w', newline='', encoding='utf-8') as stream:
    writer = csv.writer(stream)
    writer.writerow(['title'])
    writer.writerows([[title] for title in titles])
print('采集完成：', len(titles))
'''


class CollectionTests(unittest.TestCase):
    setUp = test_ai_agent.AgentTests.setUp
    turn = test_ai_agent.AgentTests.turn
    draft = test_ai_agent.AgentTests.draft

    def result(self):
        return {'exit_code': 0, 'log': '2 rows', 'result': '',
                'files': {'books.csv': b'title\na\nb\n'}, 'network': {'requests': 1}}

    async def run_trial(self, result):
        turn = self.turn(content='采集 https://books.toscrape.com/ 两本书')
        draft = self.draft(turn, SOURCE)
        pending = asyncio.create_task(collection.request_trial(draft['draft_id'], self.thread, turn['id'], {'books.toscrape.com'}))
        await asyncio.sleep(.02)
        self.assertEqual(db.fetch_one('SELECT status FROM ai_collection_trials')['status'], 'pending')
        with patch.object(collection, 'execute', new=AsyncMock(return_value=result)):
            self.assertTrue(await collection.run_next_trial())
        return await pending, draft

    def test_trial_runs_only_on_queue_and_returns_downloadable_verified_result(self):
        result, draft = asyncio.run(self.run_trial(self.result()))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['rows'], 2)
        response = agent.download_collection_file(result['id'], 'books.csv', self.user)
        self.assertEqual(response.body, b'title\na\nb\n')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            agent.download_collection_file(result['id'], 'books.csv', self.users[1])
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['drafts'][0]['trial']['status'], 'success')

    def test_missing_rows_fails_even_when_process_exits_zero(self):
        result = self.result()
        result['files']['books.csv'] = b'title\na\n'
        report, _ = asyncio.run(self.run_trial(result))
        self.assertEqual(report['status'], 'failed')
        self.assertIn('行数', report['message'])
        self.assertEqual(report['files'], ['books.csv'])

    def test_synthetic_result_without_network_is_not_verified_collection(self):
        result = self.result()
        result['network'] = {'requests': 0}
        report, _ = asyncio.run(self.run_trial(result))
        self.assertEqual(report['status'], 'failed')

    def test_cannot_expand_authorized_hosts(self):
        turn = self.turn()
        draft = self.draft(turn, SOURCE)
        with self.assertRaisesRegex(ValueError, '用户'):
            asyncio.run(collection.request_trial(draft['draft_id'], self.thread, turn['id'], {'example.com'}))
        self.assertIsNone(db.fetch_one('SELECT * FROM ai_collection_trials'))

    def test_stop_pending_trial_prevents_execution(self):
        async def scenario():
            turn = self.turn()
            draft = self.draft(turn, SOURCE)
            pending = asyncio.create_task(collection.request_trial(draft['draft_id'], self.thread, turn['id'], {'books.toscrape.com'}))
            await asyncio.sleep(.02)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            self.assertFalse(await collection.run_next_trial())
            self.assertEqual(db.fetch_one('SELECT status FROM ai_collection_trials')['status'], 'cancelled')
        asyncio.run(scenario())

    def test_restart_marks_trial_interrupted_without_replay(self):
        turn = self.turn()
        draft = self.draft(turn, SOURCE)
        db.execute("INSERT INTO ai_collection_trials(draft_id,turn_id,status,hosts,deadline,created_at) VALUES(?,?,'running','[]',0,?)", (draft['draft_id'],turn['id'],db.utc_now()))
        agent.init_tables()
        self.assertEqual(db.fetch_one('SELECT status FROM ai_collection_trials')['status'], 'interrupted')

    def test_requirements_and_contract_are_checked(self):
        self.assertEqual(collection.validate(SOURCE, 'scrapling==0.4.15')['min_rows'], 2)
        for source, requirements in [(SOURCE, 'DrissionPage'), (SOURCE, 'scrapling==0.1'), ('print(1)', '')]:
            with self.assertRaises(ValueError):
                collection.validate(source, requirements)


class CollectionNetworkTests(unittest.TestCase):
    def test_public_address_validation(self):
        path = Path(__file__).resolve().parents[1] / 'sandbox/collection_net.py'
        spec = importlib.util.spec_from_file_location('collection_net_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1',443))]):
            with self.assertRaises(ValueError): module.target('https://example.com', {'example.com'})
        for url in ['file:///etc/passwd','https://user:pass@example.com/','http://example.com:8080/','https://evil.com/']:
            with self.assertRaises(ValueError): module.target(url, {'example.com'})

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_COLLECTION') == '1', 'requires prepared collection-v1 runtime')
    def test_real_scrapling_request_and_independent_csv_validation(self):
        result = asyncio.run(collection.execute(SOURCE, 'scrapling==0.4.15', CONTRACT, timeout=40))
        self.assertEqual(runtime.validate_result(result, runtime.validate_contract(CONTRACT))['rows'], 2)
        self.assertIn(b'A Light in the Attic', result['files']['books.csv'])
        self.assertEqual(result['network']['requests'], 1)

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_COLLECTION') == '1', 'requires prepared collection-v1 runtime')
    def test_real_browser_executes_js_and_cannot_read_host_or_direct_network(self):
        source = '''
from spiderfly_collection import browser
from pathlib import Path
import socket
assert not Path('/mnt/c').exists()
assert not Path('/home/doujiao').exists()
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('direct network reachable')
with browser() as page:
    page.set_content('<p id="value"></p><script>document.querySelector("#value").textContent="rendered"</script>')
    assert page.locator('#value').inner_text() == 'rendered'
print('browser and isolation verified')
'''
        result = asyncio.run(runtime.execute_source(source, pages={}, profile='collection-v1', timeout=40))
        self.assertEqual(result['exit_code'], 0, result['log'])
        self.assertIn('browser and isolation verified', result['log'])

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_COLLECTION') == '1', 'requires prepared collection-v1 runtime')
    def test_real_browser_loads_assets_and_clicks_next_page_without_crashing(self):
        source = '''from spiderfly_collection import browser
with browser() as page:
    page.goto('https://books.toscrape.com/', wait_until='domcontentloaded')
    assert page.locator('h3 a').count() == 20
    page.locator('li.next a').click()
    assert 'page-2' in page.url
    assert page.locator('h3 a').count() == 20
    print('second page verified')
'''
        result = asyncio.run(runtime.execute_source(source, pages={}, profile='collection-v1',
                             allowed_hosts=['books.toscrape.com'], timeout=60))
        self.assertEqual(result['exit_code'], 0, result['log'])
        self.assertTrue(any('page-2' in page['url'] for page in result['network']['pages']))


class CollectionScheduledTests(unittest.TestCase):
    setUp = test_maintenance.MaintenanceTests.setUp

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_COLLECTION') == '1', 'requires prepared collection-v1 runtime')
    def test_uploaded_python_uses_same_collection_profile_on_scheduled_run(self):
        from app import maintenance, runner
        self.path.write_text(SOURCE, encoding='utf-8')
        with db.transaction() as conn:
            snapshot = maintenance.capture_snapshot(conn, self.task_id)
        self.assertEqual(json.loads(snapshot)['policy']['runtime'], 'collection-v1')
        eid = db.execute("INSERT INTO executions(task_id,status,trigger_source,maintenance_snapshot,created_at) VALUES(?,'pending','schedule',?,?)",
                         (self.task_id, snapshot, db.utc_now()))
        asyncio.run(runner.run_execution(eid))
        execution = db.fetch_one('SELECT * FROM executions WHERE id=?', (eid,))
        self.assertEqual(execution['status'], 'success', execution['error_message'])
        self.assertIn('2', execution['stdout'])
