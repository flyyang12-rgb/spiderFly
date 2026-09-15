import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app import collection_progress as progress, database, maintenance_runtime, dp_runtime
from app.api import executions
from tests import test_app_api


def line(**values):
    return progress.PREFIX + json.dumps({'event': 'progress', **values}, ensure_ascii=False) + '\n'


class ProgressTests(unittest.TestCase):
    def test_chunk_boundaries_unknown_total_and_bounded_history(self):
        text = line(page=3, collected=80, message='正在保存')
        state = '{}'
        for char in text:
            state = progress.feed_progress(state, char, '2026-09-15T00:00:00+00:00')
        self.assertEqual(progress.progress_view(state)['latest']['collected'], 80)
        self.assertNotIn('total', progress.progress_view(state)['latest'])
        for i in range(70):
            state = progress.feed_progress(state, line(collected=i), 'now')
        view = progress.progress_view(state)
        self.assertEqual(len(view['events']), 40)
        self.assertEqual(view['event_count'], 71)
        self.assertNotIn('pending', view)

    def test_invalid_or_incomplete_messages_do_not_replace_last_progress(self):
        state = progress.feed_progress('{}', line(collected=8), 'now')
        for text in [line(collected=-1), line(collected=True), line(total=1.5),
                     line(event='unknown'), progress.PREFIX + '{invalid}\n',
                     line(message='x' * 5000), line(completeness='complete')]:
            state = progress.feed_progress(state, text, 'later')
        state = progress.feed_progress(state, progress.PREFIX + '{"event":', 'later')
        self.assertEqual(progress.progress_view(state)['latest']['collected'], 8)
        self.assertEqual(progress.progress_view(state)['event_count'], 1)

    def test_long_split_line_cannot_inject_an_event_mid_line(self):
        state = progress.feed_progress('{}', 'x' * 5000, 'now')
        state = progress.feed_progress(state, line(collected=999), 'now')
        self.assertIsNone(progress.progress_view(state))
        state = progress.feed_progress(state, line(collected=3), 'now')
        self.assertEqual(progress.progress_view(state)['latest']['collected'], 3)

    def test_summary_does_not_change_execution_status_and_survives_log_truncation(self):
        helper = test_app_api.ManagedAppApiTests()
        with helper.fixture() as item:
            task = helper.create_task(item['app_id'], item['user'])
            execution_id = database.execute(
                "INSERT INTO executions(task_id,status,created_at) VALUES (?,'running',?)",
                (task['id'], database.utc_now()))
            database.append_execution_output(execution_id, 'stdout', line(collected=80))
            database.append_execution_output(execution_id, 'stderr', line(collected=999))
            database.append_execution_output(execution_id, 'stdout', line(event='summary', collected=80, completeness='complete'))
            with patch.object(database, 'MAX_OUTPUT_CHARS', 100):
                database.append_execution_output(execution_id, 'stdout', 'ordinary log\n' * 100)
            database.execute("UPDATE executions SET status='failed' WHERE id=?", (execution_id,))
            with patch.object(executions.maintenance, 'execution_note', return_value=None), patch.object(executions, 'list_artifacts', return_value={}):
                result = executions.get_execution(execution_id, item['user'])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['collection_progress']['latest']['collected'], 80)
            self.assertEqual(result['collection_progress']['event_count'], 2)
            self.assertNotIn('SPIDERFLY_PROGRESS', result['stdout'])
            database.init_db()  # Migration is repeatable and retains progress.
            self.assertIn('80', database.fetch_one('SELECT collection_progress FROM executions WHERE id=?', (execution_id,))['collection_progress'])


class LiveBridgeTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.name == 'nt', 'native Windows process bridge')
    async def test_dp_streams_before_result_without_starting_a_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'fake_driver.py'
            script.write_text(
                "import json,sys,time\n"
                "sys.stderr.write('SPIDERFLY_PROGRESS '+json.dumps({'event':'progress','collected':9})+'\\n');sys.stderr.flush()\n"
                "time.sleep(0.6)\n"
                "print(json.dumps({'exit_code':0,'log':'done','artifacts':[],'result':''}))\n", encoding='utf-8')
            chunks, observed = [], asyncio.Event()
            async def on_log(text):
                chunks.append(text)
                if '\n' in text:
                    observed.set()
            with patch.object(dp_runtime, 'ready'), patch.object(dp_runtime, 'DRIVER', script), patch.object(dp_runtime, 'DATA_DIR', Path(directory)), patch('app.host_runtime.check_host_busy', return_value=SimpleNamespace(busy=False)):
                running = asyncio.create_task(dp_runtime.execute('', hosts=set(), on_log=on_log))
                await asyncio.wait_for(observed.wait(), 5)
                self.assertFalse(running.done())
                result = await running
            self.assertIn('"collected": 9', ''.join(chunks))
            self.assertEqual(result['log'], 'done')

    async def test_profile_streams_before_process_finishes_and_preserves_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'bridge.py'
            script.write_text(
                "import json,sys,time\n"
                "sys.stdin.read()\n"
                "data='SPIDERFLY_PROGRESS {\\\"event\\\":\\\"progress\\\",\\\"collected\\\":3,\\\"stage\\\":\\\"采集\\\"}\\n'.encode()\n"
                "for b in data:\n sys.stderr.buffer.write(bytes([b]));sys.stderr.buffer.flush()\n"
                "time.sleep(0.6)\n"
                "print(json.dumps({'exit_code':0,'log':'done','artifacts':[],'result':''}))\n", encoding='utf-8')
            chunks, observed = [], asyncio.Event()
            async def on_log(text):
                chunks.append(text)
                if '\n' in text:
                    observed.set()
            with patch.object(maintenance_runtime, 'command', return_value=[sys.executable, str(script)]):
                running = asyncio.create_task(maintenance_runtime.execute_source('', pages={}, on_log=on_log))
                await asyncio.wait_for(observed.wait(), 5)
                self.assertFalse(running.done())
                result = await running
            self.assertIn('采集', ''.join(chunks))
            self.assertEqual(result['log'], 'done')
