from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import ai_agent as agent, ai_settings as settings, ai_tools as tools
from app import database, environments, runner, security


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module, values in (
            (database, {"DATA_DIR": self.root, "DB_PATH": self.root / "test.db", "RPA_APPS_DIR": self.root / "apps", "RPA_ENVS_DIR": self.root / "envs"}),
            (agent, {"RPA_APPS_DIR": self.root / "apps"}),
            (settings, {"AI_DIR": self.root / "ai", "SECRET_PATH": self.root / "secret"}),
        ):
            for name, value in values.items():
                self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}))
        database.init_db()
        agent.init_tables()
        now = database.utc_now()
        self.users = []
        for role in ("admin", "admin", "super_admin", "operator"):
            username = f"test-{len(self.users)}"
            uid = database.execute("INSERT INTO users(username,display_name,password_hash,role,active,must_change_password,created_at,updated_at) VALUES(?,?,'unused',?,1,0,?,?)", (username, username, role, now, now))
            self.users.append({"id": uid, "username": username, "role": role})
        self.user = self.users[0]
        self.thread = agent.create_thread(agent.NewThread(), self.user)

    def turn(self, content="生成一个合成数据的 CSV 处理脚本", **config):
        turn = agent.send_message(self.thread["id"], agent.Message(content=content), self.user)
        database.execute("UPDATE ai_turns SET status='running',config=? WHERE id=?", (json.dumps({**settings.DEFAULTS, **config}), turn["id"]))
        return database.fetch_one("SELECT * FROM ai_turns WHERE id=?", (turn["id"],))

    def draft(self, turn, source="import os, csv\nprint('synthetic')\n"):
        return agent.save_draft(self.thread["id"], turn["id"], {"name": "合成测试", "description": "尚未执行", "source": source, "requirements": ""})

    def test_ownership_pending_conflict_and_stop(self):
        turn = agent.send_message(self.thread["id"], agent.Message(content="需求 A"), self.user)
        with self.assertRaises(HTTPException) as duplicate:
            agent.send_message(self.thread["id"], agent.Message(content="需求 B"), self.user)
        self.assertEqual(duplicate.exception.status_code, 409)
        with self.assertRaises(HTTPException) as unauthorized:
            agent.get_thread(self.thread["id"], self.users[1])
        self.assertEqual(unauthorized.exception.status_code, 404)
        agent.stop_turn(self.thread["id"], self.user)
        row = database.fetch_one("SELECT * FROM ai_turns WHERE id=?", (turn["id"],))
        self.assertEqual(row["status"], "cancelled")
        self.assertTrue(row["ended_at"])
        with self.assertRaises(InterruptedError):
            self.draft(turn)
        self.assertEqual(len(agent.get_thread(self.thread["id"], self.users[2])["messages"]), 1)

    def test_delete_thread_permissions_and_active_conflict(self):
        app = FastAPI()
        app.include_router(agent.router)
        active_user = [self.users[1]]
        app.dependency_overrides[security.ready_user] = lambda: active_user[0]
        with TestClient(app) as client:
            url = f'/api/ai/threads/{self.thread["id"]}'
            self.assertEqual(client.delete(url).status_code, 404)
            active_user[0] = self.users[3]
            self.assertEqual(client.delete(url).status_code, 403)
            active_user[0] = self.user
            self.turn()
            self.assertEqual(client.delete(url).status_code, 409)
            self.assertIsNotNone(database.fetch_one('SELECT id FROM ai_threads WHERE id=?', (self.thread['id'],)))

    def test_delete_thread_cascades_only_selected_history(self):
        turn = self.turn()
        self.draft(turn)
        agent.event(turn['id'], 'test', '合成事件')
        database.execute("UPDATE ai_turns SET status='completed' WHERE id=?", (turn['id'],))
        agent.ai_browser.remember(self.thread['id'], 'closed')
        database.execute("INSERT INTO ai_browser_rows(thread_id,fields,records,updated_at) VALUES(?,'[]','[]',?)", (self.thread['id'], database.utc_now()))
        other = agent.create_thread(agent.NewThread(), self.user)
        app = FastAPI()
        app.include_router(agent.router)
        app.dependency_overrides[security.ready_user] = lambda: self.user
        with TestClient(app) as client:
            url = f'/api/ai/threads/{self.thread["id"]}'
            self.assertEqual(client.delete(url).status_code, 200)
            self.assertEqual(client.get(url).status_code, 404)
            self.assertEqual(client.delete(url).status_code, 404)
            self.assertEqual([item['id'] for item in client.get('/api/ai/threads').json()], [other['id']])
        for table in ('ai_messages', 'ai_turns', 'ai_drafts', 'ai_browser_state', 'ai_browser_rows'):
            self.assertIsNone(database.fetch_one(f'SELECT * FROM {table} WHERE thread_id=?', (self.thread['id'],)))
        self.assertIsNone(database.fetch_one('SELECT * FROM ai_events WHERE turn_id=?', (turn['id'],)))
        self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='delete_ai_thread'")['n'], 1)

    def test_delete_thread_rejects_collection_trial(self):
        turn = self.turn()
        draft = self.draft(turn)
        database.execute("UPDATE ai_turns SET status='completed' WHERE id=?", (turn['id'],))
        database.execute("INSERT INTO ai_collection_trials(draft_id,turn_id,status,hosts,deadline,created_at) VALUES(?,?,'pending','[]',0,?)", (draft['draft_id'], turn['id'], database.utc_now()))
        app = FastAPI()
        app.include_router(agent.router)
        app.dependency_overrides[security.ready_user] = lambda: self.user
        with TestClient(app) as client:
            self.assertEqual(client.delete(f'/api/ai/threads/{self.thread["id"]}').status_code, 409)

    def test_summary_titles_match_list_and_detail_without_changing_messages(self):
        content = 'https://item.jd.com/123.html?tracking=abc帮我采集这个评论 最新的十条'
        self.turn(content=content)
        title = '京东 · 采集最新十条评论'
        self.assertEqual(agent.list_threads(self.user)[0]['title'], title)
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['title'], title)
        database.execute('UPDATE ai_threads SET title=? WHERE id=?', (content[:50], self.thread['id']))
        self.assertEqual(agent.list_threads(self.user)[0]['title'], title)
        detail = agent.get_thread(self.thread['id'], self.user)
        self.assertEqual(detail['title'], title)
        self.assertEqual(detail['messages'][0]['content'], content)

    def test_drafts_deduplicate_and_latest_source_is_recoverable(self):
        turn = self.turn()
        first = self.draft(turn)
        identical = self.draft(turn)
        second = self.draft(turn, "print('revision two')\n")
        self.assertEqual(first["draft_id"], identical["draft_id"])
        self.assertEqual(second["version"], 2)
        self.assertFalse(second["check"]["business_verified"])
        restored = agent.task_context(self.thread)
        self.assertEqual(restored["latest_draft"]["source"], "print('revision two')\n")
        self.assertEqual(agent.download_draft(second["draft_id"], user=self.user).body, b"print('revision two')\n")
        with self.assertRaises(HTTPException):
            agent.get_draft(first["draft_id"], self.users[1])

    def test_secrets_never_persist_in_messages_config_or_events(self):
        secret = "sk-" + "synthetic" * 5
        turn = self.turn(content="合成密钥 " + secret)
        agent.event(turn["id"], "test", secret)
        self.assertNotIn(secret, json.dumps(agent.get_thread(self.thread["id"], self.user)))
        self.assertNotIn("api_key", turn["config"])
        with self.assertRaises(ValueError):
            self.draft(turn, "KEY=" + repr(secret))
        self.assertNotIn("DEEPSEEK_API_KEY", runner._runtime_environment())
        self.assertNotIn("DEEPSEEK_API_KEY", environments._build_environment_variables())

    def test_restart_preserves_history_and_drafts_without_replaying_running_turn(self):
        turn = self.turn()
        self.draft(turn)
        agent.init_tables()
        restored = agent.get_thread(self.thread["id"], self.user)
        self.assertEqual(restored["turns"][0]["status"], "interrupted")
        self.assertEqual(len(restored["messages"]), 1)
        self.assertEqual(len(restored["drafts"]), 1)

    def test_tool_loop_persists_draft_and_reported_usage(self):
        turn = self.turn()
        calls = []
        def model(messages, declared_tools, config, *, remaining):
            calls.append(copy.deepcopy(messages))
            self.assertNotIn("unit-test-placeholder", json.dumps(messages))
            if len(calls) == 1:
                reply = {"role": "assistant", "content": None, "tool_calls": [{"id": "draft-call", "type": "function", "function": {"name": "save_draft", "arguments": json.dumps({"name": "合成任务", "description": "仅语法检查", "source": "print('generated')", "requirements": ""})}}]}
            else:
                reply = {"role": "assistant", "content": "已保存草稿，未运行。"}
            return {"usage": {"prompt_tokens": 23, "completion_tokens": 7}, "choices": [{"message": reply, "finish_reason": "stop"}]}
        with patch.object(settings, "model_request", side_effect=model):
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(calls[1][-1]["tool_call_id"], "draft-call")
        state = agent.get_thread(self.thread["id"], self.user)
        self.assertEqual(state["turns"][0]["calls"], 2)
        self.assertEqual(state["turns"][0]["input_tokens"], 46)
        self.assertEqual(state["turns"][0]["output_tokens"], 14)
        self.assertEqual(len(state["drafts"]), 1)
        self.assertEqual(state["messages"][-1]["content"], "已保存草稿，未运行。")

    def test_stop_during_model_call_prevents_saving(self):
        turn = self.turn()
        def model(*args, **kwargs):
            agent.stop_turn(self.thread["id"], self.user)
            return {"usage": {"prompt_tokens": 20, "completion_tokens": 5}, "choices": [{"message": {"role": "assistant", "content": "不应保存"}}]}
        with patch.object(settings, "model_request", side_effect=model), self.assertRaises(InterruptedError):
            asyncio.run(agent.process_turn(turn))
        state = agent.get_thread(self.thread["id"], self.user)
        self.assertEqual(len(state["messages"]), 1)
        self.assertEqual(state["turns"][0]["output_tokens"], 5)

    def test_evidence_review_does_not_force_another_collection(self):
        turn = self.turn(content='复核刚才空列表的原因，不要再采集数据。')
        content = '先前响应为空，不能据此断言需要登录；具体原因尚未验证。'
        response = {'usage': {'prompt_tokens': 20, 'completion_tokens': 5},
                    'choices': [{'message': {'role': 'assistant', 'content': content}}]}
        state = {'status': 'closed', 'row_count': 0,
                 'last_observation': {'url': 'https://example.com/jobs', 'status': 200, 'text': '[]'}}
        with patch.object(agent.ai_browser, 'state', return_value=state), patch.object(settings, 'model_request', return_value=response) as model:
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['messages'][-1]['content'], content)

    def test_network_review_counts_as_observation(self):
        turn = self.turn(content='复核现有网络证据，不要再采集。')
        content = '响应为空，不能由此确认分页失败的原因。'
        replies = [{'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'network', 'function': {'name': 'browser_network', 'arguments': '{}'}}]},
            {'role': 'assistant', 'content': content}]
        responses = [{'usage': {'prompt_tokens': 20, 'completion_tokens': 5}, 'choices': [{'message': reply}]} for reply in replies]
        with patch.object(settings, 'model_request', side_effect=responses) as model, patch.object(agent, 'dispatch', return_value={'requests': []}):
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['messages'][-1]['content'], content)

    def test_unobserved_collection_refusal_still_requests_evidence(self):
        turn = self.turn(content='采集示例网站的公开岗位。')
        replies = ['无法采集这个网站。', '当前尚未验证，将继续检查入口。']
        responses = [{'usage': {'prompt_tokens': 20, 'completion_tokens': 5}, 'choices': [
            {'message': {'role': 'assistant', 'content': content}}]} for content in replies]
        with patch.object(settings, 'model_request', side_effect=responses) as model:
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(model.call_count, 2)
        events = database.fetch_all("SELECT * FROM ai_events WHERE turn_id=? AND kind='observation_required'", (turn['id'],))
        self.assertEqual(len(events), 1)

    def test_budget_prevents_model_request_and_unknown_tools(self):
        turn = self.turn(max_tokens=1000)
        with patch.object(settings, "model_request") as model, self.assertRaisesRegex(ValueError, "预算"):
            asyncio.run(agent.process_turn(turn))
        model.assert_not_called()
        with self.assertRaises(ValueError):
            asyncio.run(agent.dispatch("run_shell", {"command": "anything"}, self.thread, turn["id"], []))

    def test_collection_reference_is_grounded_before_model_without_forcing_web(self):
        turn = self.turn(content='Scrapling 支持代理池吗？请解释原生与平台的区别。')
        answer = '原生支持代理机制，当前平台不支持任意代理池配置。'
        captured = []

        def model(messages, *args, **kwargs):
            captured.extend(copy.deepcopy(messages))
            return {'usage': {'prompt_tokens': 20, 'completion_tokens': 5}, 'choices': [
                {'message': {'role': 'assistant', 'content': answer}}]}

        with patch.object(settings, 'model_request', side_effect=model) as request, patch.object(agent, 'dispatch') as dispatch:
            asyncio.run(agent.process_turn(turn))
        request.assert_called_once()
        dispatch.assert_not_called()
        payload = json.loads(next(row['content'] for row in captured if row.get('name') == 'search_knowledge'))
        self.assertIn('scrapling/overview.md', [row['name'] for row in payload['documents']])
        self.assertTrue(any(row['sources'] for row in payload['documents']))
        self.assertEqual(agent.get_thread(self.thread['id'], self.user)['messages'][-1]['content'], answer)
        self.assertEqual(database.fetch_all("SELECT id FROM ai_events WHERE turn_id=? AND kind='observation_required'", (turn['id'],)), [])

    def test_drissionpage_reference_reaches_model_with_current_runtime_scope(self):
        turn = self.turn(content='DP 浏览器怎么用？')
        captured = []
        def model(messages, *args, **kwargs):
            captured.extend(copy.deepcopy(messages))
            return {'usage': {'prompt_tokens': 20, 'completion_tokens': 5}, 'choices': [{'message': {'role': 'assistant',
                    'content': 'DrissionPage 可选 drissionpage-v1；需要已准备的原生 Windows 依赖。'}}]}
        with patch.object(settings, 'model_request', side_effect=model) as request, patch.object(agent, 'dispatch') as dispatch:
            asyncio.run(agent.process_turn(turn))
        request.assert_called_once()
        dispatch.assert_not_called()
        payload = json.loads(next(row['content'] for row in captured if row.get('name') == 'search_knowledge'))
        docs = [row for row in payload['documents'] if row['library'] == 'drissionpage']
        self.assertTrue(docs)
        self.assertTrue(any('drissionpage-v1' in row['integration'] for row in docs))
        self.assertTrue(all(row['sources'] for row in docs))

    def test_read_knowledge_dispatch_returns_requested_chapter(self):
        turn = self.turn()
        index = asyncio.run(agent.dispatch('read_knowledge', {'name': 'scrapling/pagination.md', 'section': ''}, self.thread, turn['id'], []))
        section = index['sections'][0]['section']
        result = asyncio.run(agent.dispatch('read_knowledge', {'name': index['name'], 'section': section}, self.thread, turn['id'], []))
        self.assertTrue(result['content'])
        self.assertEqual(result['section'], section)

    def test_knowledge_compaction_preserves_citation_and_section(self):
        document = {'name': 'scrapling/sessions.md', 'section': '2', 'version': '0.4.15',
                    'library': 'drissionpage', 'integration': '仅知识参考；未接入执行',
                    'sources': [{'title': 'Official', 'url': 'https://example.com/reference'}], 'content': 'x' * 5000}
        messages = [
            {'role': 'assistant', 'tool_calls': [{'id': 'k', 'function': {'name': 'read_knowledge'}}]},
            {'role': 'tool', 'tool_call_id': 'k', 'content': json.dumps(document)},
            {'role': 'assistant', 'content': '参考说明'},
            {'role': 'user', 'content': '继续'},
        ]
        agent.compact_observations(messages)
        result = json.loads(messages[1]['content'])['documents'][0]
        self.assertEqual(result['sources'], document['sources'])
        self.assertEqual(result['section'], '2')
        self.assertEqual(result['library'], document['library'])
        self.assertEqual(result['integration'], document['integration'])
        self.assertTrue(result['truncated'])

    def test_http_roles_and_settings_never_echo_secret(self):
        app = FastAPI()
        app.include_router(agent.router)
        active_user = [self.users[3]]
        app.dependency_overrides[security.ready_user] = lambda: active_user[0]
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/ai/threads").status_code, 403)
            self.assertEqual(client.get("/api/ai/settings").status_code, 403)
            active_user[0] = self.user
            self.assertEqual(client.get("/api/ai/threads").status_code, 200)
            self.assertEqual(client.put("/api/ai/settings", json={"max_calls": 4}).status_code, 403)
            active_user[0] = self.users[2]
            secret = "sk-" + "synthetic" * 5
            response = client.put("/api/ai/settings", json={"api_key": secret, "unknown": True})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(secret, response.text)
            response = client.put("/api/ai/settings", json={"max_calls": 4})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["max_calls"], 4)
            self.assertNotIn("api_key", response.json())

    def test_model_call_count_limit_preserves_draft_without_extra_request(self):
        turn = self.turn(max_calls=1)
        response = {"usage": {"prompt_tokens": 20, "completion_tokens": 10}, "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "draft", "function": {"name": "save_draft", "arguments": json.dumps({"name": "预算测试", "description": "未试跑", "source": "print(1)", "requirements": ""})}}]}}]}
        with patch.object(settings, "model_request", return_value=response) as model, self.assertRaisesRegex(ValueError, "次数上限"):
            asyncio.run(agent.process_turn(turn))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(agent.get_thread(self.thread["id"], self.user)["drafts"]), 1)

    def test_legacy_two_hour_turn_stops_at_twenty_minutes(self):
        turn = self.turn(max_seconds=7200)
        with patch.object(agent, "time") as clock, patch.object(settings, "model_request") as model, self.assertRaises(TimeoutError):
            clock.monotonic.side_effect = [0, 1201]
            asyncio.run(agent.process_turn(turn))
        model.assert_not_called()

    def test_saved_time_limit_is_fixed_at_twenty_minutes(self):
        settings.AI_DIR.mkdir(parents=True, exist_ok=True)
        (settings.AI_DIR / "settings.json").write_text(json.dumps({"max_seconds": 7200}), encoding="utf-8")
        self.assertEqual(settings.settings()["max_seconds"], 1200)
        self.assertEqual(settings.save_settings({"max_seconds": 7200})["max_seconds"], 1200)

    def test_time_budget_prevents_dispatch(self):
        turn = self.turn(max_seconds=30)
        with patch.object(agent, "time") as clock, patch.object(settings, "model_request") as model, self.assertRaises(TimeoutError):
            clock.monotonic.side_effect = [0, 31]
            asyncio.run(agent.process_turn(turn))
        model.assert_not_called()

    def test_deleted_conversation_does_not_stop_worker_or_next_turn(self):
        agent.send_message(self.thread["id"], agent.Message(content="first"), self.user)
        next_thread = agent.create_thread(agent.NewThread(), self.user)
        next_turn = agent.send_message(next_thread["id"], agent.Message(content="second"), self.user)
        def model(messages, *args, **kwargs):
            if messages[-1]["content"] == "first":
                database.execute("DELETE FROM ai_threads WHERE id=?", (self.thread["id"],))
            return {"usage": {"prompt_tokens": 20, "completion_tokens": 5}, "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}
        async def run():
            agent.start()
            try:
                async def wait_done():
                    while database.fetch_one("SELECT status FROM ai_turns WHERE id=?", (next_turn["id"],))["status"] != "completed":
                        if not agent.is_running():
                            await agent._worker
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(wait_done(), timeout=3)
                self.assertTrue(agent.is_running())
            finally:
                await agent.stop()
        with patch.object(settings, "model_request", side_effect=model):
            asyncio.run(run())
        self.assertEqual(agent.get_thread(next_thread["id"], self.user)["messages"][-1]["content"], "done")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_dpapi_roundtrip_blank_key_preserves_existing_secret(self):
        secret = "sk-" + "synthetic" * 5
        settings.save_settings({"api_key": secret})
        self.assertNotIn(secret.encode(), settings.SECRET_PATH.read_bytes())
        settings.save_settings({"api_key": "", "max_calls": 3})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertEqual(settings.api_key(), secret)


class PublicPageTests(unittest.TestCase):
    def test_site_authorization_comes_only_from_user_messages(self):
        self.assertEqual(tools.allowed_hosts([{"role": "user", "content": "读取 https://example.com/catalog"}, {"role": "assistant", "content": "https://malicious.example/"}]), {"example.com"})

    def test_private_networks_mixed_dns_and_unapproved_hosts_are_rejected(self):
        for addresses in (("127.0.0.1",), ("10.1.1.1",), ("169.254.169.254",), ("::1",), ("93.184.216.34", "192.168.0.1")):
            with self.subTest(addresses=addresses), patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]):
                with self.assertRaises(ValueError):
                    tools.public_target("https://example.com", {"example.com"})
        for url in ("http://localhost/", "file:///etc/passwd", "https://user:pass@example.com", "https://example.com:5000/"):
            with self.assertRaises(ValueError):
                tools.public_target(url, {"example.com"})

    def test_redirect_to_private_network_is_rechecked_and_socket_uses_pinned_ip(self):
        with patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]), patch.object(tools.http.client, "HTTPConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 302
            response.getheader.return_value = "http://127.0.0.1/"
            with self.assertRaises(ValueError):
                tools.fetch_page("http://example.com/", {"example.com"})
            connection.close.assert_called_once()
            with patch.object(socket, "create_connection") as connect:
                connection._create_connection(("changed.example", 80), 20)
                connect.assert_called_once_with(("93.184.216.34", 80), 20, None)

    def test_static_inspection_never_executes_code_and_lists_all_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "must-not-exist"
            result = tools.inspect_python(f"import os, csv\nopen({str(target)!r}, 'w').write('bad')")
            self.assertTrue(result["syntax_ok"])
            self.assertFalse(result["business_verified"])
            self.assertEqual(result["imports"], ["csv", "os"])
            self.assertFalse(target.exists())
        self.assertFalse(tools.inspect_python("if :")["syntax_ok"])


if __name__ == "__main__":
    unittest.main()
