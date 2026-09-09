from __future__ import annotations
import asyncio
import base64
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from app import database as db, maintenance as m, maintenance_runtime as runtime, ai_settings, security, runner, execution_results
from app import environments

CONTRACT = {'effects':'artifacts_only','file':'rows.csv','format':'csv','min_rows':2,'max_rows':2,'required':['name','value'],'unique_by':['name'],'positive':['value'],'urls':[]}
GOOD = "import os,csv\nfrom pathlib import Path\nwith (Path(os.environ['SPIDERFLY_ARTIFACT_DIR'])/'rows.csv').open('w',newline='') as f:\n w=csv.writer(f); w.writerow(['name','value']); w.writerows([['a',2],['b',4]])\n"

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module, values in ((db, {'DATA_DIR':self.root,'DB_PATH':self.root/'test.db','RPA_APPS_DIR':self.root/'apps','RPA_ENVS_DIR':self.root/'envs'}),
            (m, {'JOB_ROOT':self.root/'maintenance','RPA_APPS_DIR':self.root/'apps'}),
            (ai_settings, {'AI_DIR':self.root/'ai','SECRET_PATH':self.root/'secret'}),
            (execution_results, {'EXECUTIONS_DIR':self.root/'executions'})):
            for key,value in values.items(): self.stack.enter_context(patch.object(module,key,value))
        db.init_db(); m.init_tables()
        now=db.utc_now()
        self.users=[]
        for role in ('admin','operator','super_admin'):
            uid=db.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,updated_at) VALUES(?,?,'unused',?,?,?)",(role,role,role,now,now))
            self.users.append({'id':uid,'role':role,'username':role})
        self.user=self.users[0]
        self.app_id=db.execute("INSERT INTO rpa_apps(name,created_at,updated_at) VALUES('维护测试',?,?)",(now,now))
        folder=self.root/'apps'/str(self.app_id); folder.mkdir()
        self.path=folder/'original.py'
        self.path.write_text('# spiderfly-acceptance: '+json.dumps(CONTRACT)+'\n'+GOOD.replace("['a',2]","['a',missing_value]"),'utf-8')
        db.execute('UPDATE rpa_apps SET script_path=? WHERE id=?',(str(self.path),self.app_id))
        self.task_id=db.execute("INSERT INTO tasks(name,app_id,script_path,created_by,notify_on_success,notify_on_failure,created_at,updated_at) VALUES('维护测试',?,?,?,0,0,?,?)",(self.app_id,str(self.path),self.user['id'],now,now))

    def fail_execution(self, error='NameError: missing_value', status='failed'):
        with db.transaction() as conn: snapshot=m.capture_snapshot(conn,self.task_id)
        eid=db.execute('INSERT INTO executions(task_id,status,error_message,stderr,maintenance_snapshot,created_at) VALUES(?,?,?,?,?,?)',(self.task_id,status,error,error,snapshot,db.utc_now()))
        m.record_failure(eid)
        return eid

    def job(self): return db.fetch_one('SELECT * FROM maintenance_jobs ORDER BY id DESC LIMIT 1')

    def generate(self, source=GOOD):
        response={'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_fix','arguments':json.dumps({'source':source,'explanation':'修正未定义数值'})}}]}}]}
        with patch.object(ai_settings,'model_request',return_value=response) as model:
            self.assertTrue(asyncio.run(m.generate_next()))
            return model

    def trial(self, raw=b'name,value\na,2\nb,4\n'):
        with patch.object(runtime,'execute_source',new=AsyncMock(return_value={'exit_code':0,'log':'done','files':{'rows.csv':raw},'result':''})):
            self.assertTrue(asyncio.run(m.run_next_trial()))

    def complete_rerun(self, status='success'):
        eid = self.job()['rerun_execution_id']
        db.execute('UPDATE executions SET status=?,ended_at=?,duration_ms=250 WHERE id=?', (status, db.utc_now(), eid))
        m.record_failure(eid)
        return eid

    def test_default_auto_snapshot_deduplicates_code_without_changing_native_path(self):
        self.fail_execution(); self.fail_execution()
        policy=m.policy_view(self.task_id)['policy']
        self.assertEqual(policy['mode'],'auto'); self.assertEqual(policy['runtime'],'native')
        self.assertEqual(len(m.policy_view(self.task_id)['versions']),1)
        snapshots=db.fetch_all('SELECT maintenance_snapshot FROM executions')
        self.assertNotIn('source',json.loads(snapshots[0]['maintenance_snapshot']))
        self.assertEqual(m.read_snapshot(snapshots[0]['maintenance_snapshot'])['source'],self.path.read_text('utf-8'))
        self.assertEqual(db.fetch_one('SELECT script_path FROM rpa_apps')['script_path'],str(self.path))
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],1)

    def test_automatic_candidate_validation_activation_and_rollback(self):
        self.fail_execution(); self.generate(); self.assertEqual(self.job()['status'],'ready'); self.trial()
        self.assertEqual(self.job()['status'],'activated')
        view=m.policy_view(self.task_id); original=view['versions'][-1]; candidate=view['versions'][0]
        self.assertTrue(candidate['approved']); self.assertEqual(view['policy']['runtime'],'readonly-v1')
        self.assertIn('missing_value',self.path.read_text('utf-8'))
        req=Request({'type':'http','headers':[],'client':('127.0.0.1',123)})
        m.rollback(self.task_id,original['id'],req,self.user)
        self.complete_rerun()
        m.rollback(self.task_id,original['id'],req,self.user)
        self.assertEqual(m.policy_view(self.task_id)['policy']['runtime'],'native')
        self.assertEqual(db.fetch_one('SELECT script_path FROM rpa_apps')['script_path'],str(self.path))

    def test_model_cannot_relax_frozen_acceptance_to_pass_empty_output(self):
        self.fail_execution(); self.generate("SPIDERFLY_ACCEPTANCE = {'min_rows':0}\nprint('success')")
        self.trial(b'name,value\n')
        self.assertEqual(self.job()['status'],'failed')
        self.assertEqual(m.policy_view(self.task_id)['policy']['runtime'],'native')

    def test_missing_contract_rejects_replacing_script_with_unrelated_logic(self):
        self.path.write_text("print(missing_value)")
        self.fail_execution(); self.generate(); self.trial()
        self.assertEqual(self.job()['status'],'failed')
        self.assertFalse(m.policy_view(self.task_id)['versions'][0]['approved'])

    def test_unknown_business_effects_do_not_auto_activate(self):
        value={k:v for k,v in CONTRACT.items() if k!='effects'}
        self.path.write_text('# spiderfly-acceptance: '+json.dumps(value)+'\nprint(missing)')
        self.fail_execution(); self.generate(); self.trial()
        self.assertEqual(self.job()['status'],'review'); self.assertIn('其他业务操作',self.job()['note'])

    def test_unsupported_dependencies_preserve_candidate_without_execution(self):
        db.execute("UPDATE rpa_apps SET requirements_text='pywin32'")
        self.fail_execution(); self.generate()
        with patch.object(runtime,'execute_source') as run: asyncio.run(m.run_next_trial()); run.assert_not_called()
        self.assertEqual(self.job()['status'],'review')

    def test_pending_runs_rebind_to_repair_without_losing_parameters_or_edits(self):
        self.fail_execution(); self.generate()
        with db.transaction() as conn: snapshot = m.capture_snapshot(conn, self.task_id)
        eid=db.execute("INSERT INTO executions(task_id,status,maintenance_snapshot,trigger_source,requested_by,created_at) VALUES(?,'pending',?,'schedule',?,?)",(self.task_id,snapshot,self.user['id'],db.utc_now()))
        db.execute("UPDATE tasks SET name='改名后',description='新的说明',version=version+1 WHERE id=?", (self.task_id,))
        self.trial(); self.assertEqual(self.job()['status'],'activated')
        queued=db.fetch_one('SELECT * FROM executions WHERE id=?',(eid,))
        saved=m.read_snapshot(queued['maintenance_snapshot'])
        self.assertEqual(saved['source'],GOOD)
        self.assertEqual(saved['policy']['runtime'],'readonly-v1')
        self.assertEqual(queued['trigger_source'],'schedule'); self.assertEqual(queued['requested_by'],self.user['id'])
        self.assertEqual(self.job()['rerun_execution_id'],eid)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE status='pending'")['n'],1)
        self.assertEqual(db.fetch_one('SELECT name FROM tasks')['name'],'改名后')
        self.assertEqual(m.execution_note(self.job()['execution_id'])['status'],'activated')
        self.assertIn('原参数保留',str(m.execution_note(self.job()['execution_id'])['events']))

    def test_running_task_defers_trial_and_finishes_automatically_at_boundary(self):
        self.fail_execution(); self.generate()
        eid=db.execute("INSERT INTO executions(task_id,status,created_at) VALUES(?,'running',?)",(self.task_id,db.utc_now()))
        with patch.object(runtime,'execute_source') as run:
            self.assertFalse(asyncio.run(m.run_next_trial())); run.assert_not_called()
        self.assertEqual(self.job()['status'],'ready')
        db.execute("UPDATE executions SET status='success' WHERE id=?",(eid,))
        self.trial(); self.assertEqual(self.job()['status'],'activated')

    def test_changed_source_ends_old_repair_without_overwriting_latest_code(self):
        self.fail_execution(); self.generate(); self.path.write_text('print("new user code")')
        self.trial(); self.assertEqual(self.job()['status'],'cancelled')
        self.assertEqual(self.path.read_text(),'print("new user code")')

    def test_plain_type_error_auto_publishes_and_next_execution_succeeds(self):
        broken='print("hello")\ndef greet(name):\n    return f"Hi, {3 + name}!"\ngreet("Amy")\n'
        fixed=broken.replace('3 + name','name')
        self.path.write_text(broken,'utf-8'); self.fail_execution('TypeError: unsupported operand'); self.generate(fixed)
        good={'exit_code':0,'log':'hello\n','files':{},'result':''}
        bad={**good,'exit_code':1,'log':'hello\nTypeError: unsupported operand'}
        with patch.object(runtime,'execute_source',new=AsyncMock(side_effect=[bad,good])) as run:
            self.assertTrue(asyncio.run(m.run_next_trial())); self.assertEqual(run.await_count,2)
        self.assertEqual(self.job()['status'],'activated')
        self.assertIn('修复后运行通过',self.job()['note'])
        eid=self.job()['rerun_execution_id']
        task={'id':self.task_id,'maintenance_snapshot':db.fetch_one('SELECT maintenance_snapshot FROM executions WHERE id=?',(eid,))['maintenance_snapshot']}
        with patch.object(runtime,'execute_source',new=AsyncMock(return_value=good)),patch.object(runner,'_send_notification',new=AsyncMock()):
            asyncio.run(m.run_managed_execution(eid,task,runner.ExecutionControl()))
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?',(eid,))['status'],'success')

    def test_plain_repair_must_reproduce_the_same_error(self):
        self.path.write_text('print(missing_value)'); self.fail_execution('NameError: missing_value'); self.generate('missing_value=1\nprint(missing_value)')
        with patch.object(runtime,'execute_source',new=AsyncMock(return_value={'exit_code':1,'log':'ImportError: different error','files':{},'result':''})):
            asyncio.run(m.run_next_trial())
        self.assertEqual(self.job()['status'],'failed'); self.assertIn('不同错误',self.job()['note'])

    def test_stop_during_generation_preserves_usage_without_saving_candidate(self):
        self.fail_execution()
        def model(*args,**kwargs):
            m.stop_job(self.job()['id'],self.user)
            return {'usage':{'prompt_tokens':20,'completion_tokens':5},'choices':[]}
        with patch.object(ai_settings,'model_request',side_effect=model): asyncio.run(m.generate_next())
        self.assertEqual(self.job()['status'],'cancelled'); self.assertEqual(self.job()['input_tokens'],20)
        self.assertIsNone(self.job()['candidate_id'])

    def test_unknown_model_usage_and_restart_keep_conservative_reservations(self):
        self.fail_execution()
        with patch.object(ai_settings,'model_request',side_effect=TimeoutError('unknown response')): asyncio.run(m.generate_next())
        self.assertEqual(self.job()['status'],'failed'); self.assertEqual(self.job()['input_tokens'],0)
        self.assertGreater(self.job()['reserved_tokens'],0)
        db.execute("UPDATE maintenance_jobs SET status='generating',reserved_seconds=2700")
        m.init_tables(); self.assertEqual(self.job()['status'],'interrupted')
        self.assertGreater(self.job()['reserved_tokens'],0); self.assertEqual(self.job()['elapsed_seconds'],2700)

    def test_global_budget_blocks_without_calling_model(self):
        self.fail_execution()
        with patch.object(m,'settings',return_value={'daily_seconds':7200,'daily_tokens':1000}),patch.object(ai_settings,'model_request') as model:
            asyncio.run(m.generate_next()); model.assert_not_called()
        self.assertEqual(self.job()['status'],'budget')

    def test_notices_survive_restart_with_independent_receipts_and_no_switch_route(self):
        self.fail_execution(); m.finish(self.job()['id'],'failed','原版本保留')
        app=FastAPI(); app.include_router(m.router); user=[self.user]
        app.dependency_overrides[security.ready_user]=lambda:user[0]
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/maintenance/notices').json()['unread_count'],1)
            user[0]=self.users[1]
            self.assertEqual(client.get('/api/maintenance/notices').json()['unread_count'],0)
            self.assertEqual(client.post(f"/api/maintenance/notices/{self.job()['id']}/read").status_code,404)
            self.assertEqual(client.get(f'/api/maintenance/tasks/{self.task_id}').status_code,403)
            user[0]=self.user
            self.assertEqual(client.put(f'/api/maintenance/tasks/{self.task_id}',json={'mode':'off'}).status_code,405)
            self.assertEqual(client.get('/api/maintenance/tasks/999').status_code,404)
            client.post(f"/api/maintenance/notices/{self.job()['id']}/read")
            m.init_tables(); self.assertEqual(client.get('/api/maintenance/notices').json()['unread_count'],0)
            user[0]=self.users[2]; self.assertEqual(client.get('/api/maintenance/notices').json()['unread_count'],1)

    def test_source_change_blocks_rollback(self):
        self.fail_execution(); self.generate(); self.trial()
        self.complete_rerun()
        original=m.policy_view(self.task_id)['versions'][-1]
        self.path.write_text('print("changed outside platform")')
        with self.assertRaises(HTTPException): m.rollback(self.task_id,original['id'],Request({'type':'http','headers':[]}),self.user)

    def test_task_deletion_blocks_active_maintenance_and_cleans_finished_files(self):
        eid=self.fail_execution()
        with self.assertRaisesRegex(RuntimeError,'自动维护'): environments.delete_task_bundle(self.task_id)
        self.generate(); self.trial()
        folder=m.JOB_ROOT/'results'/str(self.job()['id']); self.assertTrue(folder.exists())
        db.execute("UPDATE rpa_apps SET environment_status='ready'")
        with patch.object(environments,'remove_execution_workspaces',return_value=()),patch.object(environments,'remove_managed_app_storage',return_value=()):
            environments.delete_task_bundle(self.task_id)
        self.assertFalse(folder.exists()); self.assertFalse((m.JOB_ROOT/'inputs'/f'{eid}.json').exists())
        self.assertIsNone(self.job())

    def test_managed_execution_enforces_contract_and_records_result(self):
        self.fail_execution(); self.generate(); self.trial()
        eid=self.job()['rerun_execution_id']
        task={'id':self.task_id,'maintenance_snapshot':db.fetch_one('SELECT maintenance_snapshot FROM executions WHERE id=?',(eid,))['maintenance_snapshot']}
        control=runner.ExecutionControl()
        with patch.object(runtime,'execute_source',new=AsyncMock(return_value={'exit_code':0,'log':'actual execution','files':{'rows.csv':b'name,value\na,2\nb,4\n'},'result':''})),patch.object(runner,'_send_notification',new=AsyncMock()):
            asyncio.run(m.run_managed_execution(eid,task,control))
        self.assertEqual(db.fetch_one('SELECT result_code FROM executions WHERE id=?',(eid,))['result_code'],'ACCEPTANCE_PASSED')

    def test_rerun_is_durable_once_and_preserves_original_inputs_and_schedule(self):
        # A frozen template and parameters survive changes to task descriptions.
        template=self.path.parent/'input.xlsx'; template.write_bytes(b'frozen synthetic input')
        db.execute('UPDATE rpa_apps SET template_path=?',(str(template),))
        db.execute("UPDATE tasks SET next_run_at='2030-01-01T00:00:00+00:00',description='原参数'")
        original=self.fail_execution()
        db.execute('UPDATE executions SET requested_by=? WHERE id=?',(self.users[1]['id'],original))
        self.generate(); self.trial()
        job=self.job(); eid=job['rerun_execution_id']
        self.assertIsNotNone(eid); self.assertIsNone(job['ended_at'])
        queued=db.fetch_one('SELECT * FROM executions WHERE id=?',(eid,))
        saved=m.read_snapshot(queued['maintenance_snapshot'])
        self.assertEqual(saved['source'],GOOD); self.assertEqual(saved['description'],'原参数')
        self.assertEqual(base64.b64decode(saved['template']),b'frozen synthetic input')
        self.assertEqual(queued['requested_by'],self.users[1]['id'])
        self.assertEqual(queued['trigger_source'],'maintenance')
        self.assertEqual(db.fetch_one('SELECT next_run_at FROM tasks')['next_run_at'],'2030-01-01T00:00:00+00:00')
        self.assertEqual(db.fetch_one('SELECT last_status FROM tasks')['last_status'],'pending')
        for _ in range(2):
            db.init_db(); m.init_tables(); m.record_failure(original)
            self.assertFalse(asyncio.run(m.run_next_trial()))
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE status='pending'")['n'],1)
        self.assertEqual(self.job()['rerun_execution_id'],eid)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],1)

    def test_rerun_notifies_after_result_once_and_cannot_trigger_another_repair(self):
        original=self.fail_execution(); self.generate(); self.trial()
        self.assertEqual(m.notices(self.user)['unread_count'],0)
        before=self.job()['elapsed_seconds']; eid=self.complete_rerun('failed')
        for _ in range(3): m.reconcile_reruns(); m.record_failure(eid)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],1)
        self.assertAlmostEqual(self.job()['elapsed_seconds'],before+.25)
        self.assertEqual(self.job()['reserved_seconds'],0)
        notice=m.notices(self.user)['items'][0]
        self.assertEqual(notice['rerun_status'],'failed'); self.assertEqual(notice['rerun_execution_id'],eid)
        self.assertEqual(m.execution_note(original)['rerun_status'],'failed')
        self.assertEqual(m.execution_note(eid)['execution_id'],original)
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?',(original,))['status'],'failed')
        self.assertEqual(sum('自动重跑失败' in e['message'] for e in m.execution_note(eid)['events']),1)
        m.read_notice(self.job()['id'],self.user); m.init_tables()
        self.assertEqual(m.notices(self.user)['unread_count'],0)

    def test_rerun_and_publication_roll_back_together_on_enqueue_failure(self):
        self.fail_execution(); self.generate()
        with patch.object(m,'_enqueue_repaired_run',side_effect=ValueError('模拟入队失败')): self.trial()
        self.assertEqual(self.job()['status'],'failed')
        self.assertIsNone(self.job()['rerun_execution_id'])
        self.assertEqual(db.fetch_one('SELECT script_path FROM rpa_apps')['script_path'],str(self.path))
        self.assertEqual(m.policy_view(self.task_id)['policy']['runtime'],'native')
        self.assertEqual(db.fetch_one('SELECT approved FROM task_code_versions WHERE id=?',(self.job()['candidate_id'],))['approved'],0)

    def test_disabled_task_is_repaired_but_not_executed(self):
        self.fail_execution(); self.generate(); db.execute('UPDATE tasks SET enabled=0')
        self.trial()
        self.assertEqual(self.job()['status'],'activated'); self.assertIsNotNone(self.job()['ended_at'])
        self.assertIsNone(self.job()['rerun_execution_id']); self.assertIn('已停用',self.job()['rerun_reason'])
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE status='pending'")['n'],0)

    def test_cancelled_and_interrupted_reruns_settle_without_replaying(self):
        self.fail_execution(); self.generate(); self.trial(); eid=self.job()['rerun_execution_id']
        db.execute("UPDATE executions SET status='running',started_at=? WHERE id=?",(db.utc_now(),eid))
        reserved=self.job()['reserved_seconds']
        db.init_db(); m.init_tables()
        self.assertEqual(self.job()['elapsed_seconds'],reserved)
        self.assertEqual(m.execution_note(eid)['rerun_status'],'failed')
        self.assertIsNotNone(self.job()['ended_at'])
        m.record_failure(eid); self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM maintenance_jobs')['n'],1)

    def test_cancelled_pending_rerun_settles_without_execution_hook(self):
        self.fail_execution(); self.generate(); self.trial(); eid=self.job()['rerun_execution_id']
        db.execute("UPDATE executions SET status='cancelled' WHERE id=?",(eid,))
        m.reconcile_reruns()
        self.assertEqual(m.notices(self.user)['items'][0]['rerun_status'],'cancelled')
        self.assertEqual(self.job()['reserved_seconds'],0)

    def test_legacy_activated_records_are_not_replayed_on_upgrade(self):
        self.fail_execution(); self.generate()
        m.finish(self.job()['id'],'activated','旧版本已验证修复')
        db.init_db(); m.init_tables(); m.reconcile_reruns()
        self.assertIsNone(self.job()['rerun_execution_id'])
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'],1)

    def test_unrelated_manual_queue_does_not_block_or_duplicate_repair_rerun(self):
        self.fail_execution(); self.generate()
        manual=db.execute("INSERT INTO executions(task_id,status,created_at) VALUES(?,'pending',?)",(self.task_id,db.utc_now()))
        self.trial(); self.assertEqual(self.job()['status'],'activated')
        self.assertNotEqual(self.job()['rerun_execution_id'],manual)
        db.init_db(); m.init_tables()
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE status='pending'")['n'],2)
        self.assertEqual(db.fetch_one('SELECT maintenance_snapshot FROM executions WHERE id=?',(manual,))['maintenance_snapshot'],'')

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_SANDBOX')=='1','requires prepared WSL runtime')
    def test_serial_worker_really_reruns_repair_and_keeps_business_artifact(self):
        from app import main
        original=self.fail_execution(); self.generate()
        async def drive():
            worker=asyncio.create_task(main._queue_worker_loop())
            try:
                async with asyncio.timeout(25):
                    while not self.job()['ended_at']:
                        await asyncio.sleep(.05)
            finally:
                worker.cancel()
                await asyncio.gather(worker,return_exceptions=True)
        with patch.object(main,'_host_waiting_reason',return_value=''),patch.object(runner,'_send_notification',new=AsyncMock()):
            asyncio.run(drive())
        job=self.job(); eid=job['rerun_execution_id']
        self.assertEqual(job['status'],'activated',job['note'])
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?',(eid,))['status'],'success')
        self.assertEqual(db.fetch_one('SELECT status FROM executions WHERE id=?',(original,))['status'],'failed')
        self.assertEqual(db.fetch_one('SELECT last_status FROM tasks')['last_status'],'success')
        artifacts=list((self.root/'executions').rglob('rows.csv'))
        self.assertEqual(len(artifacts),1)
        self.assertEqual(artifacts[0].read_text().splitlines(),['name,value','a,2','b,4'])
        self.assertEqual(m.notices(self.user)['items'][0]['rerun_status'],'success')

class RuntimeTests(unittest.TestCase):
    def test_plain_repair_cannot_delete_calls_returns_or_swallow_errors(self):
        original='def greet(name):\n    return f"Hi {3 + name}"\ngreet("Amy")\n'
        for candidate in (original.replace('greet("Amy")',''), original.replace('return f"Hi {3 + name}"','pass'), original.replace('greet("Amy")','try:\n    greet("Amy")\nexcept Exception:\n    pass')):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                runtime.validate_repair_structure(original,candidate)
        runtime.validate_repair_structure(original,original.replace('3 + name','name'))

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_SANDBOX')=='1','requires prepared WSL runtime')
    def test_real_sandbox_reproduces_type_error_and_runs_repaired_script(self):
        original='def greet(name):\n    return f"Hi {3 + name}"\nprint(greet("Amy"))\n'
        bad=asyncio.run(runtime.execute_source(original,pages={},timeout=10))
        good=asyncio.run(runtime.execute_source(original.replace('3 + name','name'),pages={},timeout=10))
        self.assertNotEqual(bad['exit_code'],0); self.assertIn('TypeError:',bad['log'])
        self.assertEqual(good['exit_code'],0); self.assertEqual(good['log'].strip(),'Hi Amy')
    def test_excel_acceptance_and_explicit_failure_receipt(self):
        import openpyxl
        book=openpyxl.Workbook(); book.active.append(['name','value']); book.active.append(['a',2]); book.active.append(['b',4])
        output=io.BytesIO(); book.save(output); book.close()
        contract=runtime.validate_contract({**CONTRACT,'file':'rows.xlsx','format':'xlsx'})
        result={'exit_code':0,'files':{'rows.xlsx':output.getvalue()}}
        self.assertEqual(runtime.validate_result(result,contract)['rows'],2)
        with self.assertRaises(ValueError): runtime.validate_result({**result,'result':'{"outcome":"failure"}'},contract)

    def test_result_names_reject_windows_devices_and_case_aliases(self):
        for names in (['CON'],['aux.csv'],['a.'],['../x'],['A.csv','a.csv']):
            with self.subTest(names=names),self.assertRaises(ValueError):
                runtime.decode_result({'exit_code':0,'artifacts':[{'name':n,'data':'YQ=='} for n in names]})

    def test_acceptance_detects_duplicates_wrong_values_and_nonfinite_numbers(self):
        contract=runtime.validate_contract(CONTRACT)
        for raw in (b'name,value\na,2\na,4\n',b'name,value\na,NaN\nb,4\n',b'name,value\na,\nb,4\n'):
            with self.subTest(raw=raw),self.assertRaises(ValueError): runtime.validate_result({'exit_code':0,'files':{'rows.csv':raw}},contract)

    @unittest.skipUnless(os.getenv('SPIDERFLY_TEST_SANDBOX')=='1','WSL sandbox integration is explicitly enabled on a prepared host')
    def test_real_kernel_sandbox_denies_host_network_and_processes(self):
        source="""import os,socket,subprocess,json
from pathlib import Path
checks = {'host_hidden': not Path('/mnt/c').exists(), 'key_hidden': not os.getenv('DEEPSEEK_API_KEY')}
for name, action in [('network',lambda: socket.create_connection(('1.1.1.1',443),timeout=1)),('fork',os.fork),('exec',lambda: subprocess.run(['/usr/bin/true']))]:
 try: action(); checks[name] = False
 except OSError: checks[name] = True
Path(os.environ['SPIDERFLY_ARTIFACT_DIR']+'/checks.json').write_text(json.dumps(checks))
"""
        result=asyncio.run(runtime.execute_source(source,pages={},timeout=10))
        self.assertEqual(result['exit_code'],0,result['log'])
        self.assertTrue(all(json.loads(result['files']['checks.json']).values()))
        slow=asyncio.run(runtime.execute_source('while True: pass',pages={},timeout=1))
        self.assertNotEqual(slow['exit_code'],0)
