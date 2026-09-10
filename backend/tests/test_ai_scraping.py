import asyncio
import copy
import json
import os
import unittest
from urllib.parse import parse_qs
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import ai_agent as agent, ai_browser as browser, ai_scraping as native, ai_settings
import test_ai_agent


class NativeScrapingTests(unittest.TestCase):
    setUp = test_ai_agent.AgentTests.setUp
    turn = test_ai_agent.AgentTests.turn

    def test_native_snapshot_extracts_without_interactive_browser(self):
        html = '<html><title>合成岗位</title><body><ul><li class="job"><a class="name" href="/job?id=1">RPA工程师</a><span>杭州</span></li></ul></body></html>'
        snapshot = {'url': 'https://example.com/jobs', 'html': html, 'engine': 'StealthyFetcher', 'status': 200, 'trace': []}
        async def scenario():
            service = browser.BrowserService(headless=True)
            with patch.object(browser, 'check_public', new_callable=AsyncMock), patch.object(native, 'request', return_value=snapshot):
                result = await service.run(self.thread['id'], 'scrape_page', {'url': snapshot['url'], 'mode': 'auto', 'wait_for': ''})
            self.assertEqual(result['engine'], 'StealthyFetcher')
            self.assertFalse(service.sessions)
            self.assertIsNone(service.engine)
            result = await service.run(self.thread['id'], 'scrape_inspect', {'selector': '.job'})
            self.assertIn('RPA工程师', result['text'])
            extracted = await service.run(self.thread['id'], 'scrape_extract', {'rows': '.job', 'fields': '{"name":"a::text","link":"a::attr(href)"}', 'unique_by': '["link"]', 'limit': '100'})
            self.assertEqual(extracted['row_count'], 1)
            self.assertEqual(browser.state(self.thread['id'])['status'], 'native')
            await service.close(self.thread['id'])
            self.assertEqual(browser.state(self.thread['id'])['row_count'], 1)
            with self.assertRaisesRegex(ValueError, '快照'):
                await service.run(self.thread['id'], 'scrape_inspect', {'selector': '.job'})
        asyncio.run(scenario())

    def test_http_static_success_does_not_launch_browser(self):
        from scrapling import Selector
        from scrapling.fetchers import Fetcher, StealthyFetcher
        html = '<html><body><p>' + '公开职位数据' * 100 + '</p></body></html>'
        selector = Selector(html)
        page = SimpleNamespace(html_content=html, url='https://example.com', status=200, headers={},
                               css=selector.css, get_all_text=selector.get_all_text)
        with patch.object(native, 'public'), patch.object(Fetcher, 'get', return_value=page) as get, patch.object(StealthyFetcher, 'fetch') as stealth:
            result = native.request(page.url, 'auto')
        self.assertEqual(result['engine'], 'Fetcher')
        stealth.assert_not_called()
        options = get.call_args.kwargs
        self.assertEqual(options['impersonate'], 'chrome')
        self.assertTrue(options['stealthy_headers'])
        self.assertIsInstance(options['verify'], str)
        self.assertFalse(options['follow_redirects'])

    def test_record_discovery_survives_large_filter_menu(self):
        menu = '<a href="javascript:;">筛选项</a>' * 100
        card = '<li class="record"><a href="/job/1?token=private">杭州 RPA 工程师 职位内容</a></li>'
        result = native.inspect({'html': '<body>' + menu + '<ul>' + card * 2 + '</ul></body>',
                                 'url': 'https://example.com', 'engine': 'Fetcher', 'status': 200, 'trace': []})
        self.assertEqual(len(result['links']), 2)
        self.assertEqual(result['regions'][0]['selector'], 'li.record')
        self.assertNotIn('private', json.dumps(result))

    def test_reextract_fills_missing_fields_even_at_target(self):
        fields = ['link', 'company', 'title']
        browser.append_rows(self.thread['id'], fields, [{'link': 'one', 'company': '', 'title': 'RPA'}], unique_by=['link'], limit=1)
        result = browser.append_rows(self.thread['id'], fields, [{'link': 'one', 'company': '真实公司', 'title': 'different'}], unique_by=['link'], limit=1)
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(result['sample'][0]['company'], '真实公司')
        self.assertEqual(result['sample'][0]['title'], 'RPA')
        self.assertEqual(result['enriched_fields'], 1)

    def test_api_evidence_keeps_pagination_not_credentials_or_records(self):
        result = native.json_evidence({'code': 0, 'zpData': {'hasMore': True,
            'jobList': [{'title': 'private-record-value', 'securityId': 'private-token'}]}},
            'page=2&city=101210100&query=RPA&token=private-query&password=private-password')
        self.assertEqual(result['lists'][0]['path'], 'zpData.jobList')
        self.assertEqual(result['lists'][0]['count'], 1)
        self.assertEqual(result['parameters']['page'], '2')
        self.assertNotIn('private-', json.dumps(result))

    def test_json_extraction_keeps_business_filters_and_safe_job_links(self):
        data = {'jobs': [{'id': '1', 'title': 'RPA工程师', 'city': '杭州'},
                         {'id': '2', 'title': 'RPA实习生', 'city': '杭州'},
                         {'id': '3', 'title': 'RPA工程师', 'city': '上海'}]}
        args = {'rows': 'jobs', 'fields': json.dumps({'岗位': 'title', '城市': 'city', '链接': 'https://example.com/job/{id}.html'}),
                'filters': json.dumps({'城市': {'equals': '杭州'}, '岗位': {'contains_any': ['rpa'], 'excludes_any': ['实习']}})}
        fields, rows = native.extract_json(data, args, base_url='https://example.com/search')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['链接'], 'https://example.com/job/1.html')
        with self.assertRaisesRegex(ValueError, '同站'):
            native.extract_json(data, args, base_url='https://other.example')
        args['fields'] = '{"secret":"securityId"}'
        args['filters'] = '{}'
        with self.assertRaisesRegex(ValueError, '凭据'):
            native.extract_json(data, args)

    def test_replay_changes_only_observed_pagination_and_preserves_conditions(self):
        captured = {'request_url': 'https://example.com/search/list.json', 'method': 'POST',
                    'content_type': 'application/x-www-form-urlencoded', 'body': 'page=1&pageSize=15&city=hangzhou&query=RPA'}
        result = native.prepare_replay(captured, {'page': 2})
        self.assertEqual(parse_qs(result['body']), {'page': ['2'], 'pageSize': ['15'], 'city': ['hangzhou'], 'query': ['RPA']})
        for changes in ({'city': 'shanghai'}, {'query': 'other'}, {'offset': 3}, {'page': True}):
            with self.assertRaises(ValueError): native.prepare_replay(captured, changes)
        with self.assertRaises(ValueError):
            native.prepare_replay({**captured, 'request_url': 'https://example.com/search/delete'}, {'page': 2})
        with self.assertRaises(ValueError):
            native.prepare_replay({**captured, 'body': 'page=1&city=a&city=b'}, {'page': 2})

    def test_expanded_budget_configuration_remains_bounded_and_keeps_defaults(self):
        result = ai_settings.save_settings({'max_tokens': 1000000, 'max_calls': 96, 'max_seconds': 3600})
        self.assertEqual(result['max_tokens'], 1000000)
        self.assertEqual(ai_settings.DEFAULTS['max_tokens'], 100000)
        with self.assertRaises(ValueError):
            ai_settings.save_settings({'max_tokens': 2000001})

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_BROWSER') == '1', '需要独立环境 Chromium')
    def test_native_live_session_keeps_context_filters_and_api_evidence(self):
        async def scenario():
            service = browser.BrowserService(headless=True)
            key = self.thread['id']
            html = '''<html><body><input id="password" type="password">
              <button id="next" onclick="fetch('/search/list.json',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'page=2&query=RPA'}).then(r=>r.json()).then(()=>document.querySelector('.job').textContent='RPA B')">筛选</button>
              <div class="job">RPA A</div><div id="menu">区域</div>
              <div id="options"><button onclick="pick('reset')">不限</button><button onclick="pick('A')">A</button><button onclick="pick('B')">B</button><button onclick="pick('Bad')">Bad</button></div>
              <script>function pick(area){fetch('/search/list.json',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'page=1&query='+(area==='Bad'?'Other':'RPA')+'&area='+area})}</script>
              </body></html>'''
            async def fulfill(route):
                if route.request.url.endswith('/search/list.json'):
                    from urllib.parse import parse_qs
                    params = parse_qs(route.request.post_data or '')
                    title = params.get('area', ['hidden-payload'])[0]
                    await route.fulfill(status=200, content_type='application/json', body=json.dumps({'code': 0, 'data': {'hasMore': True, 'rows': [{'title': title}]}}))
                else:
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
            try:
                session = await service.create(key, native=True)
                engine = session.native_engine
                await session.context.route('https://fixture.example/**', fulfill)
                with patch.object(browser, 'check_public', new_callable=AsyncMock):
                    result = await service.run(key, 'scrape_live_open', {'url': 'https://fixture.example/'})
                    self.assertEqual(result['engine'], 'AsyncStealthySession')
                    await session.context.add_cookies([{'name': 'continuity', 'value': 'synthetic', 'url': 'https://fixture.example'}])
                    result = await service.run(key, 'scrape_live_act', {'action': 'click', 'selector': '#next', 'value': ''})
                    self.assertIn('RPA B', result['text'])
                    self.assertTrue(any(r.get('json', {}).get('parameters', {}).get('page') == '2' for r in session.network))
                    self.assertNotIn('hidden-payload', json.dumps(result))
                    response_id = next(r['json']['response_id'] for r in session.network if r.get('json', {}).get('response_id'))
                    replay = await service.run(key, 'scrape_api_page', {'response_id': response_id, 'pagination': '{"page":3}'})
                    self.assertEqual(replay['parameters']['page'], '3')
                    self.assertEqual(replay['lists'][0]['count'], 1)
                    extracted = await service.run(key, 'scrape_json_extract', {'response_id': replay['response_id'], 'rows': 'data.rows',
                        'fields': '{"岗位":"title"}', 'filters': '{}', 'unique_by': '["岗位"]', 'limit': '100'})
                    self.assertEqual(extracted['sample'][0]['岗位'], 'hidden-payload')
                    batch_args = {'menu': '#menu', 'options': '#options button', 'values': '["A","B","Bad"]',
                        'reset_text': '不限', 'dismiss': '', 'response_id': replay['response_id'], 'rows': 'data.rows',
                        'fields': '{"岗位":"title"}', 'filters': '{}', 'unique_by': '["岗位"]', 'limit': '100'}
                    with self.assertRaisesRegex(ValueError, '不在当前菜单'):
                        await service.run(key, 'scrape_collect_options', {**batch_args, 'values': '["Unknown"]'})
                    batch = await service.run(key, 'scrape_collect_options', batch_args)
                    self.assertEqual(batch['row_count'], 3)
                    self.assertEqual([r['option'] for r in batch['completed']], ['A', 'B'])
                    self.assertEqual(batch['remaining'], ['Bad'])
                    self.assertIn('关键词发生变化', batch['error'])
                    self.assertNotIn('Bad', browser.export_csv(key).decode('utf-8-sig'))
                    stopped_turn = self.turn('测试停止批量采集')
                    test_ai_agent.database.execute('UPDATE ai_turns SET stop_requested=1 WHERE id=?', (stopped_turn['id'],))
                    stopped = await service.run(key, 'scrape_collect_options', {**batch_args, 'values': '["A"]'})
                    self.assertEqual(stopped['completed'], [])
                    self.assertEqual(stopped['remaining'], ['A'])
                    self.assertIn('用户已停止', stopped['error'])
                    self.assertEqual(browser.state(key)['row_count'], 3)
                    with self.assertRaisesRegex(ValueError, '密码'):
                        await service.run(key, 'scrape_live_act', {'action': 'fill', 'selector': '#password', 'value': 'x'})
                    await service.run(key, 'scrape_live_open', {'url': 'https://fixture.example/again'})
                    self.assertIs(service.sessions[key], session)
                    self.assertEqual((await session.context.cookies())[0]['name'], 'continuity')
                    observed = await service.run(key, 'browser_open', {'url': 'https://fixture.example/normal-navigation'})
                    self.assertIs(service.sessions[key].native_engine, engine)
                    self.assertEqual(observed['engine'], 'AsyncStealthySession')
                    snapshot = await service.run(key, 'scrape_inspect', {'selector': '.job'})
                    self.assertIn('RPA A', snapshot['text'])
                await service.close(key)
                self.assertFalse(engine._is_alive)
            finally:
                await service.shutdown()
        asyncio.run(scenario())

    def test_http_denial_does_not_automatically_retry_with_stealth(self):
        from scrapling.fetchers import Fetcher, StealthyFetcher
        page = SimpleNamespace(status=429)
        with patch.object(native, 'public'), patch.object(Fetcher, 'get', return_value=page), patch.object(StealthyFetcher, 'fetch') as stealth:
            with self.assertRaisesRegex(ValueError, '429'):
                native.request('https://example.com', 'auto')
            stealth.assert_not_called()

    def test_context_compaction_keeps_intent_latest_dom_and_errors(self):
        request = '杭州 RPA 100条，岗位链接去重，不改变筛选条件'
        messages = [{'role': 'user', 'content': request}]
        for i in range(3):
            messages += [{'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'scrape_page'}}]},
                         {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'text': str(i) * 12000, 'structure': 'html' * 10000, 'url': f'https://example.com/{i}', 'error': '保留真实错误'})}]
        agent.compact_observations(messages)
        self.assertEqual(messages[0]['content'], request)
        self.assertEqual(len(messages), 7)
        old = json.loads(messages[2]['content'])
        latest = json.loads(messages[-1]['content'])
        self.assertNotIn('structure', old)
        self.assertEqual(old['error'], '保留真实错误')
        self.assertEqual(len(latest['structure']), 6500)
        self.assertEqual(latest['url'], 'https://example.com/2')

    def test_compaction_keeps_latest_api_evidence_among_analytics(self):
        captured = {'url': 'https://example.com/search/list.json', 'json': {'response_id': 'response-7', 'lists': [{'path': 'data.jobs', 'count': 15}]}}
        records = [{'url': 'https://example.com/analytics'} for _ in range(40)] + [captured]
        messages = [{'role': 'assistant', 'tool_calls': [{'id': 'a', 'function': {'name': 'browser_network'}}]},
                    {'role': 'tool', 'tool_call_id': 'a', 'content': json.dumps({'requests': records})}]
        agent.compact_observations(messages)
        agent.compact_observations(messages)
        self.assertEqual(json.loads(messages[1]['content'])['requests'][0], captured)
        messages.extend([{'role': 'assistant', 'tool_calls': [{'id': 'b', 'function': {'name': 'scrape_inspect'}}]},
                         {'role': 'tool', 'tool_call_id': 'b', 'content': '{"text":"menu"}'}])
        agent.compact_observations(messages)
        self.assertEqual(json.loads(messages[-1]['content'])['api_evidence'][0], captured)
        self.assertNotIn('api_evidence', json.loads(messages[1]['content']))
        agent.compact_observations(messages)
        self.assertEqual(json.loads(messages[-1]['content'])['api_evidence'][0], captured)

    def test_exploration_event_explains_observation_without_page_payload(self):
        result = {'engine': 'StealthyFetcher', 'status': 200, 'text': 'unneeded-page-payload',
                  'trace': [{'reason': '静态响应缺少内容，切换无头采集'}],
                  'network': [{'json': {'response_id': '1'}}], 'row_count': 3,
                  'completed': [{'option': 'A'}], 'remaining': ['B'], 'error': '菜单被遮挡'}
        summary = agent.tool_result_summary('scrape_collect_options', result)
        self.assertIn('HTTP 200', summary)
        self.assertIn('静态响应缺少内容', summary)
        self.assertIn('累计保存 3 条', summary)
        self.assertIn('剩余 1 个', summary)
        self.assertNotIn('unneeded-page-payload', summary)

    def test_large_repeated_observations_no_longer_stop_after_three_steps(self):
        turn = self.turn('采集杭州RPA工程师100条')
        calls = []
        def model(messages, *args, **kwargs):
            calls.append(copy.deepcopy(messages))
            i = len(calls)
            reply = {'role': 'assistant', 'content': '已观察六个页面，尚未宣称采集完成。'} if i == 7 else {
                'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'browser_observe', 'arguments': '{}'}}]}
            return {'usage': {'prompt_tokens': 4000, 'completion_tokens': 100}, 'choices': [{'message': reply}]}
        result = {'url': 'https://example.com', 'text': '网页内容' * 3000, 'structure': 'div ' * 4500,
                  'elements': [{'ref': str(i), 'text': '职位入口'} for i in range(100)]}
        with patch.object(ai_settings, 'model_request', side_effect=model), patch.object(browser.host, 'call', new_callable=AsyncMock, return_value=result):
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(len(calls), 7)
        self.assertLess(len(json.dumps(calls[-1], ensure_ascii=False).encode()), 50000)
