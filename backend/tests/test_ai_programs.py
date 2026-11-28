import asyncio
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import ai_agent as agent, ai_programs as programs, ai_settings, database, environments, maintenance, security
from app.services import host_dispatch
from tests import test_ai_agent


def archive(files):
    result = io.BytesIO()
    with ZipFile(result, 'w') as z:
        for name, content in files.items():
            z.writestr(name, content)
    return result.getvalue()


class AiProgramTests(unittest.TestCase):
    turn = test_ai_agent.AgentTests.turn
    draft = test_ai_agent.AgentTests.draft

    def setUp(self):
        test_ai_agent.AgentTests.setUp(self)
        self.enterContext(patch.object(environments, 'RPA_APPS_DIR', self.root / 'apps'))
        self.enterContext(patch.object(environments, 'RPA_ENVS_DIR', self.root / 'envs'))
        self.enterContext(patch.object(maintenance, 'RPA_APPS_DIR', self.root / 'apps'))
        self.enterContext(patch.object(host_dispatch, 'DATA_DIR', self.root))
        maintenance.init_tables()
        host_dispatch.init_tables()
        self.http_user = self.user
        app = FastAPI()
        app.include_router(agent.router)
        app.dependency_overrides[security.ready_user] = lambda: self.http_user
        self.client = self.enterContext(TestClient(app))
        self.url = f'/api/ai/threads/{self.thread["id"]}/program'

    def upload(self, filename='打印.py', content=b"print('hello world')\nprint('success')\n"):
        response = self.client.post(self.url, files={'program': (filename, content)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def arguments(self, source, kind='upload', quote='帮我创建这个程序，每天早上8点半运行'):
        return dict(source_kind=kind, source_id=str(source), name='普通打印任务', description='普通 Python，无采集条件',
                    trigger_type='daily', trigger_config='{"time":"08:30"}', target_host_id='', project_entry='', request_quote=quote)

    def create(self, arguments, turn):
        messages = database.fetch_all('SELECT role,content FROM ai_messages WHERE thread_id=? ORDER BY id', (self.thread['id'],))
        return programs.create_task(arguments, self.thread['id'], turn['id'], messages)

    def test_upload_readback_replace_remove_and_ownership(self):
        first = self.upload()
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['program']['filename'], '打印.py')
        self.assertNotIn('content', first)
        self.assertNotIn('source', first)
        self.assertEqual(self.upload()['id'], first['id'])
        second = self.upload('other.py', b"print('other')")
        self.assertNotEqual(second['id'], first['id'])
        self.http_user = self.users[1]
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, files={'program': ('a.py', b'pass')}).status_code, 404)
        self.http_user = self.users[3]
        self.assertEqual(self.client.post(self.url, files={'program': ('a.py', b'pass')}).status_code, 403)
        self.http_user = self.user
        self.assertEqual(self.client.delete(self.url).status_code, 200)
        self.assertIsNone(agent.get_thread(self.thread['id'], self.user)['program'])

    def test_invalid_uploads_do_not_replace_good_program(self):
        good = self.upload()
        for filename, content in [('a.txt', b'pass'), ('a.py', b'def :'), ('large.py', b' ' * (2 * 1024 * 1024 + 1)),
                                  ('broken.zip', b'not a zip'), ('unsafe.zip', archive({'../a.py': 'pass'}))]:
            with self.subTest(filename=filename):
                self.assertEqual(self.client.post(self.url, files={'program': (filename, content)}).status_code, 400)
                self.assertEqual(programs.program_view(self.thread['id'])['id'], good['id'])

    def test_active_turn_prevents_replacing_or_removing_upload(self):
        good = self.upload()
        self.turn()
        self.assertEqual(self.client.post(self.url, files={'program': ('new.py', b'pass')}).status_code, 409)
        self.assertEqual(self.client.delete(self.url).status_code, 409)
        self.assertEqual(programs.program_view(self.thread['id'])['id'], good['id'])

    def test_uploaded_py_creates_daily_task_without_collection_or_execution(self):
        upload = self.upload()
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        result = self.create(args, turn)
        self.assertTrue(result['created'])
        task = result['task']
        self.assertEqual((task['trigger_type'], task['trigger_config']), ('daily', {'time': '08:30'}))
        self.assertEqual(task['host_name'], '主控部署机')
        saved = database.fetch_one('SELECT * FROM tasks WHERE id=?', (task['id'],))
        self.assertEqual(Path(saved['script_path']).read_bytes(), b"print('hello world')\nprint('success')\n")
        self.assertFalse(saved['notify_on_success'])
        self.assertFalse(saved['first_environment_ready'])
        self.assertIsNone(saved['next_run_at'])
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['created_task']['id'], task['id'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM executions')['n'], 0)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM ai_collection_trials')['n'], 0)

    def test_identical_retry_is_deduplicated_and_changed_plan_is_rejected(self):
        upload = self.upload()
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        first = self.create(args, turn)
        second = self.create(args, turn)
        self.assertTrue(second['replayed'])
        self.assertEqual(first['task']['id'], second['task']['id'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'], 1)
        self.assertEqual(database.fetch_one("SELECT COUNT(*) n FROM audit_logs WHERE action='ai_create_task'")['n'], 1)
        with self.assertRaisesRegex(ValueError, '不会覆盖原计划'):
            self.create({**args, 'trigger_config': '{"time":"09:00"}'}, turn)

    def test_other_normal_plans_use_the_existing_schedule_validation(self):
        cases = [
            ('weekly', {'weekdays': [3, 1, 1], 'time': '09:05'}, '帮我安排每周一和三9点05运行', {'weekdays': [1, 3], 'time': '09:05'}),
            ('monthly', {'day': 'last', 'time': '23:59'}, '帮我安排每月最后一天23点59运行', {'day': 'last', 'time': '23:59'}),
            ('interval', {'value': 10, 'unit': 'minutes'}, '帮我设置每隔10分钟运行', {'value': 10, 'unit': 'minutes'}),
            ('manual', {}, '帮我创建一个手动任务', {}),
        ]
        for trigger, config, quote, expected in cases:
            with self.subTest(trigger=trigger):
                self.thread = agent.create_thread(agent.NewThread(), self.user)
                self.url = f'/api/ai/threads/{self.thread["id"]}/program'
                upload = self.upload()
                args = {**self.arguments(upload['id'], quote=quote), 'name': trigger + '任务', 'trigger_type': trigger, 'trigger_config': json.dumps(config)}
                turn = self.turn(quote)
                self.assertEqual(self.create(args, turn)['task']['trigger_config'], expected)

    def test_unapproved_target_cannot_create_a_task(self):
        upload = self.upload()
        args = {**self.arguments(upload['id']), 'target_host_id': '999999'}
        turn = self.turn(args['request_quote'])
        with self.assertRaises(HTTPException):
            self.create(args, turn)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'], 0)

    def test_zip_keeps_project_entry_and_automatically_resolves_submodule_dependency(self):
        content = archive({'项目/main.py': 'from worker import run\nrun()\n', '项目/worker.py': 'import yaml\ndef run(): print(yaml.safe_load("value: 42"))\n'})
        upload = self.upload('项目.zip', content)
        self.assertEqual(upload['suggested_entry'], '项目/main.py')
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        result = self.create(args, turn)
        app = database.fetch_one('SELECT * FROM rpa_apps WHERE id=(SELECT app_id FROM tasks WHERE id=?)', (result['task']['id'],))
        self.assertEqual(app['requirements_text'], 'PyYAML')
        project = database.fetch_one('SELECT * FROM project_packages WHERE app_id=?', (app['id'],))
        self.assertEqual(project['entry'], '项目/main.py')
        with ZipFile(io.BytesIO(content)) as original:
            self.assertEqual(Path(app['script_path']).read_bytes(), original.read('项目/main.py'))

    def test_ambiguous_zip_entry_requires_selection(self):
        upload = self.upload('multiple.zip', archive({'one.py': 'pass', 'two.py': 'pass'}))
        self.assertEqual(upload['suggested_entry'], '')
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        with self.assertRaisesRegex(ValueError, '多个入口'):
            self.create(args, turn)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'], 0)

    def test_user_quote_and_specific_clock_are_required(self):
        upload = self.upload()
        args = self.arguments(upload['id'])
        turn = self.turn('解释这个文件，不要创建任务')
        with self.assertRaisesRegex(ValueError, '用户本人'):
            self.create(args, turn)
        with self.assertRaisesRegex(ValueError, '取消创建'):
            self.create({**args, 'request_quote': '不要创建任务'}, turn)
        database.execute("UPDATE ai_turns SET status='completed' WHERE id=?", (turn['id'],))
        turn = self.turn('帮我创建任务，每天运行')
        with self.assertRaisesRegex(ValueError, '具体执行时间'):
            self.create({**args, 'request_quote': '帮我创建任务，每天运行'}, turn)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'], 0)

    def test_stopped_turn_and_permission_revocation_block_creation(self):
        upload = self.upload()
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        agent.stop_turn(self.thread['id'], self.user)
        with self.assertRaises(InterruptedError):
            self.create(args, turn)
        database.execute('UPDATE ai_turns SET stop_requested=0 WHERE id=?', (turn['id'],))
        database.execute('UPDATE users SET active=0 WHERE id=?', (self.user['id'],))
        with self.assertRaisesRegex(ValueError, '权限'):
            self.create(args, turn)

    def test_cancellation_at_commit_rolls_back_task_program_and_receipt(self):
        upload = self.upload()
        args = self.arguments(upload['id'])
        turn = self.turn(args['request_quote'])
        cancelled = threading.Event()
        real = environments.create_managed_task_bundle

        def stop_at_commit(*a, **kw):
            callback = kw['on_created']
            def stopped(conn, task_id):
                cancelled.set()
                callback(conn, task_id)
            return real(*a, **{**kw, 'on_created': stopped})

        with patch.object(environments, 'create_managed_task_bundle', side_effect=stop_at_commit), self.assertRaises(InterruptedError):
            programs.create_task(args, self.thread['id'], turn['id'], [{'role': 'user', 'content': args['request_quote']}], cancelled)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'], 0)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM rpa_apps')['n'], 0)
        self.assertIsNone(database.fetch_one('SELECT task_id FROM ai_threads WHERE id=?', (self.thread['id'],))['task_id'])

    def test_generated_print_draft_can_be_scheduled_without_a_collection_trial(self):
        quote = '生成只打印 hello world 和“成功了 天才”的 PY，帮我创建每天9点的任务'
        turn = self.turn(quote)
        source = 'print("hello world")\nprint("成功了 天才")\n'
        draft = self.draft(turn, source)
        args = self.arguments(draft['draft_id'], 'draft', quote)
        args['trigger_config'] = '{"time":"09:00"}'
        result = asyncio.run(agent.dispatch('create_task_from_program', args, self.thread, turn['id'], [{'role': 'user', 'content': quote}]))
        saved = database.fetch_one('SELECT script_path FROM tasks WHERE id=?', (result['task']['id'],))
        self.assertEqual(Path(saved['script_path']).read_text('utf-8'), source)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM ai_collection_trials')['n'], 0)

    def test_collection_draft_still_requires_its_real_trial(self):
        args_quote = '帮我创建每天9点的采集任务'
        turn = self.turn(args_quote)
        draft = self.draft(turn, '# spiderfly-runtime: collection-v1\nprint("synthetic")\n')
        with self.assertRaisesRegex(ValueError, '真实采集试跑'):
            self.create(self.arguments(draft['draft_id'], 'draft', args_quote), turn)

    def test_tool_only_reads_upload_owned_by_this_thread(self):
        upload = self.upload()
        other = agent.create_thread(agent.NewThread(), self.user)
        with self.assertRaisesRegex(ValueError, '不属于'):
            programs.read_upload(other['id'], upload['id'], '')
        self.assertIn("print('hello world')", programs.read_upload(self.thread['id'], upload['id'], '')['source'])

    def test_model_can_save_print_draft_then_create_a_real_daily_task(self):
        quote = '帮我写一个打印 hello world 和“成功了 天才”的 PY，创建每天9点执行的任务'
        turn = self.turn(quote)
        counter = [0]

        def model(messages, tool_definitions, config, **kwargs):
            counter[0] += 1
            self.assertIn('普通脚本不要求网址', messages[0]['content'])
            if counter[0] == 1:
                name, args = 'save_draft', dict(name='普通打印', description='两行日志', source='print("hello world")\nprint("成功了 天才")\n', requirements='')
            elif counter[0] == 2:
                result = json.loads(messages[-1]['content'])
                name, args = 'create_task_from_program', self.arguments(result['draft_id'], 'draft', quote)
                args['trigger_config'] = '{"time":"09:00"}'
            else:
                return {'choices': [{'message': {'role': 'assistant', 'content': '已创建每天 09:00 运行的打印任务。'}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(counter[0]), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}

        with patch.object(ai_settings, 'model_request', side_effect=model):
            asyncio.run(agent.process_turn(turn))
        result = agent.get_thread(self.thread['id'], self.user)
        self.assertEqual(result['created_task']['trigger_config'], {'time': '09:00'})
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM ai_collection_trials')['n'], 0)
