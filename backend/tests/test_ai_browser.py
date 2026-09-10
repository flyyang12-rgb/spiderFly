"""Synthetic browser pages only; never uses personal profiles or business accounts."""
import asyncio
import csv
import io
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai_browser as browser, ai_agent as agent, ai_settings, database, security
import test_ai_agent


class BrowserTests(unittest.TestCase):
    setUp = test_ai_agent.AgentTests.setUp
    turn = test_ai_agent.AgentTests.turn

    def test_browser_error_reports_obstruction_without_raw_request_or_input(self):
        error = RuntimeError('Timeout\n<div class="dialog-mask active"> intercepts pointer events\nhttps://example.com/?token=private-input')
        hint = browser.operation_error_hint(error)
        self.assertIn('.dialog-mask.active', hint)
        self.assertNotIn('private-input', hint)
        self.assertNotIn('https://', hint)
        self.assertIn('不可见', browser.operation_error_hint(RuntimeError('element is not visible')))
        self.assertIn('已经关闭', browser.operation_error_hint(RuntimeError('Target page has been closed')))

    def test_refine_saved_results_is_reversible_and_thread_scoped(self):
        key = self.thread['id']
        fields = ['title', 'city', 'link', '_source_url']
        rows = [{'title': title, 'city': city, 'link': str(i), '_source_url': 'https://example.com'}
                for i, (title, city) in enumerate([('RPA工程师', '杭州'), ('RPA实习生', '杭州'), ('RPA工程师', '上海')])]
        browser.append_rows(key, fields, rows, unique_by=['link'])
        result = browser.refine_rows(key, json.dumps({'title': {'excludes_any': ['实习']}, 'city': {'equals': '杭州'}}))
        self.assertEqual(result['row_count'], 1)
        self.assertNotIn('实习', browser.export_csv(key).decode('utf-8-sig'))
        # Later DOM extraction must not reintroduce rejected rows; column order is immaterial.
        appended = browser.append_rows(key, list(reversed(fields)), rows, unique_by=['link'])
        self.assertEqual(appended['row_count'], 1)
        self.assertEqual(browser.state(key, evidence=True)['filters']['city']['equals'], '杭州')
        with self.assertRaises(ValueError):
            browser.refine_rows(key, '{"unknown":{"equals":"x"}}')
        self.assertEqual(browser.state(key)['row_count'], 1)
        browser.refine_rows(key, '{}', restore=True)
        self.assertEqual(browser.state(key)['row_count'], 3)
        self.assertEqual(browser.state(key, evidence=True)['sample'], rows)
        self.assertEqual(browser.state(key, evidence=True)['filters'], {})
        other = agent.create_thread(agent.NewThread(), self.user)['id']
        with self.assertRaises(ValueError):
            browser.refine_rows(other, '{}', restore=True)

    def test_rows_deduplication_export_and_schema_conflict(self):
        key = self.thread['id']
        row = {'岗位': '=CMD()', '_source_url': 'https://example.com/job?id=1'}
        browser.append_rows(key, list(row), [row, row])
        browser.append_rows(key, list(row), [row])
        self.assertEqual(browser.state(key)['row_count'], 1)
        content = browser.export_csv(key).decode('utf-8-sig')
        self.assertEqual(list(csv.DictReader(io.StringIO(content)))[0]['岗位'], "'=CMD()")
        with self.assertRaisesRegex(ValueError, '字段不同'):
            browser.append_rows(key, ['other'], [{'other': 'x'}])
        self.assertEqual(browser.state(key)['row_count'], 1)

    def test_restart_retains_evidence_and_rows_but_not_login_claim(self):
        key = self.thread['id']
        browser.remember(key, 'waiting_user', '请登录', {'url': 'https://example.com', 'text': '登录'})
        browser.append_rows(key, ['name'], [{'name': 'sample'}])
        agent.init_tables()
        result = browser.state(key, evidence=True)
        self.assertEqual(result['status'], 'closed')
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(result['last_observation']['text'], '登录')

    def test_observed_urls_survive_turns_and_restart_without_credentials(self):
        key = self.thread['id']
        browser.remember(key, 'native', observation={'url': 'https://example.com/jobs?city=1&token=private'})
        browser.remember(key, 'native', observation={'url': 'https://example.com/jobs?city=2'})
        browser.remember(key, 'native', observation={'url': 'https://example.com/jobs?city=2'})
        agent.init_tables()
        result = browser.state(key, evidence=True)
        self.assertEqual(result['last_observation']['observed_urls'], ['https://example.com/jobs?city=1', 'https://example.com/jobs?city=2'])

    def test_unique_job_across_pages_and_exact_target_cap(self):
        key = self.thread['id']
        fields = ['title', 'link', '_source_url']
        def row(index, page):
            return {'title': 'RPA', 'link': f'https://example.com/job?id={index}', '_source_url': f'https://example.com/jobs?page={page}'}
        browser.append_rows(key, fields, [row(1, 1)], unique_by=['link'], limit=2)
        result = browser.append_rows(key, fields, [row(1, 2), row(2, 2), row(3, 2)], unique_by=['link'], limit=2)
        self.assertEqual(result['row_count'], 2)
        content = browser.export_csv(key).decode('utf-8-sig')
        self.assertIn('id=2', content)
        self.assertNotIn('id=3', content)
        with self.assertRaisesRegex(ValueError, '去重字段不同'):
            browser.append_rows(key, fields, [row(4, 3)], unique_by=['title'], limit=2)

    def test_discovered_domains_are_thread_scoped_for_script_trials(self):
        first = self.thread['id']
        second = agent.create_thread(agent.NewThread(), self.user)['id']
        database.execute('INSERT INTO ai_browser_sites VALUES(?,?)', (first, 'example.com'))
        self.assertEqual(browser.discovered_hosts(first), {'example.com'})
        self.assertEqual(browser.discovered_hosts(second), set())

    def test_url_metadata_hides_tokens_preserves_job_identity(self):
        url = browser.safe_url('https://example.com/jobs?id=34&city=hangzhou&token=private&code=secret#fragment')
        self.assertIn('id=34', url)
        self.assertNotIn('private', url)
        self.assertNotIn('secret', url)

    def test_private_addresses_and_credentials_are_rejected(self):
        for url in ('file:///C:/Windows/win.ini', 'http://127.0.0.1/', 'http://[::1]/', 'https://user:pass@example.com'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                asyncio.run(browser.check_public(url))

    def test_browser_endpoints_enforce_roles_and_ownership(self):
        key = self.thread['id']
        browser.append_rows(key, ['name'], [{'name': 'sample'}])
        app = FastAPI()
        app.include_router(agent.router)
        user = [self.users[1]]
        app.dependency_overrides[security.ready_user] = lambda: user[0]
        with TestClient(app) as client, patch.object(browser.host, 'call', new_callable=AsyncMock) as call:
            for path in ('resume', 'close'):
                self.assertEqual(client.post(f'/api/ai/threads/{key}/browser/{path}').status_code, 404)
            self.assertEqual(client.get(f'/api/ai/threads/{key}/browser/results.csv').status_code, 404)
            call.assert_not_called()
            user[0] = self.users[3]
            self.assertEqual(client.get(f'/api/ai/threads/{key}/browser/results.csv').status_code, 403)
            user[0] = self.user
            self.assertEqual(client.get(f'/api/ai/threads/{key}/browser/results.csv').status_code, 200)
            browser.remember(key, 'waiting_user', '请登录')
            response = client.post(f'/api/ai/threads/{key}/browser/resume')
            self.assertEqual(response.status_code, 200)
            call.assert_awaited_once_with(key, 'browser_resume', {})
            self.assertEqual(client.post(f'/api/ai/threads/{key}/browser/resume').status_code, 409)

    def test_refusal_without_probe_is_sent_back_to_model(self):
        turn = self.turn('采集 Boss 直聘杭州 RPA 前一百条')
        replies = [
            {'role': 'assistant', 'content': 'BOSS 需要登录，无法采集。'},
            {'role': 'assistant', 'tool_calls': [{'id': 'search', 'function': {'name': 'browser_search', 'arguments': '{"query":"Boss 直聘 官网"}'}}]},
            {'role': 'assistant', 'content': '已找到入口，继续检查。'},
        ]
        calls = []
        def model(messages, *args, **kwargs):
            calls.append(json.dumps(messages, ensure_ascii=False))
            return {'usage': {'prompt_tokens': 20, 'completion_tokens': 10}, 'choices': [{'message': replies.pop(0)}]}
        with patch.object(ai_settings, 'model_request', side_effect=model), patch.object(browser.host, 'call', new_callable=AsyncMock, return_value={'url': 'https://example.com'}):
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(len(calls), 3)
        self.assertIn('没有实际网页探测依据', calls[1])
        self.assertNotIn('无法采集', agent.get_thread(self.thread['id'], self.user)['messages'][-1]['content'])

    def test_wait_tool_ends_turn_without_another_model_call(self):
        turn = self.turn('采集公开数据')
        reply = {'role': 'assistant', 'tool_calls': [{'id': 'wait', 'function': {'name': 'browser_wait_user', 'arguments': '{"reason":"页面要求登录"}'}}]}
        with patch.object(ai_settings, 'model_request', return_value={'usage': {'prompt_tokens': 20, 'completion_tokens': 10}, 'choices': [{'message': reply}]}) as model, patch.object(browser.host, 'call', new_callable=AsyncMock, return_value={'reason': '页面要求登录'}):
            with self.assertRaises(browser.WaitingForUser):
                asyncio.run(agent.process_turn(turn))
            self.assertEqual(model.call_count, 1)

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_BROWSER') == '1', '需要独立环境 Chromium')
    def test_real_browser_observe_click_login_resume_scrapling_and_expiry(self):
        async def scenario():
            service = browser.BrowserService(headless=True)
            key = self.thread['id']
            html = '''<html><title>合成职位</title><body>
              <label>搜索<input id="search"></label><input type="password" id="password">
              <button id="next" onclick="document.querySelector('.name').textContent='RPA B'">下一页</button>
              <div class="job"><span class="name">RPA A</span><span class="city">杭州</span><a href="/job?id=1">详情</a></div>
              </body></html>'''
            async def fulfill(route):
                await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
            try:
                session = await service.create(key)
                await session.context.route('https://fixture.example/**', fulfill)
                # Only this synthetic origin is exempted; test does not contact it.
                original_check = browser.check_public
                async def fixture_check(url):
                    if url.startswith('https://fixture.example/'):
                        return
                    await original_check(url)
                with patch.object(browser, 'check_public', side_effect=fixture_check):
                    observed = await service.run(key, 'browser_open', {'url': 'https://fixture.example/'})
                self.assertIn('RPA A', observed['text'])
                self.assertIn('job', observed['structure'])
                next_ref = next(e['ref'] for e in observed['elements'] if e['text'] == '下一页')
                password_ref = next(e['ref'] for e in observed['elements'] if e.get('type') == 'password')
                with self.assertRaisesRegex(ValueError, '密码'):
                    await service.run(key, 'browser_act', {'action': 'fill', 'ref': password_ref, 'value': 'never-send'})
                extracted = await service.run(key, 'browser_extract', {'rows': '.job', 'fields': json.dumps({'岗位': '.name::text', '城市': '.city::text', '链接': 'a::attr(href)'})})
                self.assertEqual(extracted['row_count'], 1)
                self.assertIn('id=1', extracted['sample'][0]['链接'])
                await service.run(key, 'browser_act', {'action': 'click', 'ref': next_ref, 'value': ''})
                with self.assertRaisesRegex(ValueError, '过期'):
                    await service.run(key, 'browser_act', {'action': 'click', 'ref': next_ref, 'value': ''})
                extracted = await service.run(key, 'browser_extract', {'rows': '.job', 'fields': json.dumps({'岗位': '.name::text', '城市': '.city::text', '链接': 'a::attr(href)'})})
                self.assertEqual(extracted['row_count'], 2)
                await service.run(key, 'browser_wait_user', {'reason': '合成登录'})
                with self.assertRaisesRegex(ValueError, '等待人工'):
                    await service.run(key, 'browser_observe', {})
                # Simulate the human completing login without sending credentials to the model.
                await session.context.add_cookies([{'name': 'synthetic-login', 'value': 'yes', 'domain': 'fixture.example', 'path': '/'}])
                result = await service.run(key, 'browser_resume', {})
                self.assertIn('RPA B', result['text'])
                self.assertEqual((await session.context.cookies())[0]['value'], 'yes')
                self.assertNotIn('synthetic-login', json.dumps(browser.state(key, evidence=True)))
                other = agent.create_thread(agent.NewThread(), self.user)
                isolated = await service.create(other['id'])
                self.assertEqual(await isolated.context.cookies(), [])
                session.touched = time.monotonic() - 1900
                await service.expire()
                self.assertNotIn(key, service.sessions)
                self.assertEqual(browser.state(key)['row_count'], 2)
            finally:
                await service.shutdown()
        asyncio.run(scenario())
