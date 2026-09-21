import asyncio
import json
from pathlib import Path
from unittest.mock import patch,AsyncMock
import unittest
from tests import test_maintenance as fixtures
GOOD,CONTRACT=fixtures.GOOD,fixtures.CONTRACT
from app import task_versions as v, database as db, maintenance as m, ai_settings
from starlette.requests import Request

class VersionTests(unittest.TestCase):
    def setUp(self):
        fixtures.MaintenanceTests.setUp(self)
        self.path.write_text('# spiderfly-acceptance: '+json.dumps(CONTRACT)+'\n'+GOOD,encoding='utf-8')
        with db.transaction() as conn:self.original=m.capture_snapshot(conn,self.task_id)
        self.base=v.context(self.task_id)['version_id']

    def submit_version(self,source=None,**kw):
        return v.submit(self.task_id,source or self.path.read_text('utf-8')+'\n# update',kw.pop('requirements',''),kw.pop('spec',{}),kw.pop('evidence',''),self.user['id'],base_id=v.context(self.task_id)['version_id'],**kw)

    def run_update(self,result=None):
        result=result or {'exit_code':0,'log':'real validation','result':'','files':{'rows.csv':b'name,value\na,2\nb,4\n'}}
        with patch.object(v,'validate_candidate',new=AsyncMock(return_value=result)):
            return asyncio.run(v.run_next())

    def activate(self, update):
        return v.activate_update(update['id'],Request({'type':'http','headers':[]}),self.user)

    def test_upload_publish_download_and_monotonic_after_rollback(self):
        first=self.submit_version();self.run_update();self.activate(first)
        second=self.submit_version(source=self.path.read_text('utf-8')+'\n# second');self.run_update();self.activate(second)
        first_id=db.fetch_one('SELECT version_id FROM task_version_updates WHERE id=?',(first['id'],))['version_id']
        with db.transaction() as conn:v.publish(conn,self.task_id,first_id)
        last=self.submit_version(source=self.path.read_text('utf-8')+'\n# third')
        self.assertEqual([first['version'],second['version'],last['version']],[2,3,4])
        self.assertIn(b'# update',v.download(first_id,self.user).body)
        self.assertNotIn('# update',self.path.read_text('utf-8'))

    def test_manual_candidate_is_not_tested_or_runnable_until_admin_confirms(self):
        bad = "if True print('saved without syntax preflight')\n"
        candidate = v.submit(
            self.task_id, bad, '', {}, '', self.user['id'],
            base_id=self.base, origin='manual_candidate'
        )
        self.assertEqual(candidate['status'], 'candidate')
        self.assertFalse(asyncio.run(v.run_next()))
        row = db.fetch_one('SELECT * FROM task_code_versions WHERE sequence=2')
        self.assertEqual((row['source'], row['approved']), (bad, 0))
        self.activate(candidate)
        active = v.context(self.task_id)
        self.assertEqual(active['version'], 2)
        self.assertEqual(db.fetch_one('SELECT approved FROM task_code_versions WHERE id=?',(row['id'],))['approved'],1)

    def test_pending_switches_full_snapshot_running_and_history_do_not(self):
        ids=[]
        for status in ('running','pending','success'):
            ids.append(db.execute('INSERT INTO executions(task_id,status,maintenance_snapshot,created_at,trigger_source) VALUES(?,?,?,?,?)',(self.task_id,status,self.original,db.utc_now(),'schedule')))
        update=self.submit_version(spec={'summary':'新的明确用途'},evidence='改用途说明')
        self.run_update()
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.activate(update)
        rows=db.fetch_all('SELECT * FROM executions ORDER BY id')
        self.assertEqual(rows[0]['maintenance_snapshot'],self.original)
        self.assertEqual(rows[2]['maintenance_snapshot'],self.original)
        self.assertEqual(json.loads(rows[1]['maintenance_snapshot'])['task_requirements']['summary']['value'],'新的明确用途')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) AS n FROM executions')['n'],3)

    def test_conflicting_contract_waits_for_specific_resolution(self):
        new={**CONTRACT,'min_rows':1}
        update=self.submit_version(source='# spiderfly-acceptance: '+json.dumps(new)+'\n'+GOOD)
        self.assertEqual(update['status'],'conflict')
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.assertFalse(asyncio.run(v.run_next()))
        resolved=v.resolve(update['id'],Request({'type':'http','headers':[]}),self.user)
        self.run_update();self.activate(resolved)
        self.assertNotEqual(v.context(self.task_id)['version_id'],self.base)

    def test_unsupported_dependency_keeps_current_version_and_does_not_call_model(self):
        self.submit_version(requirements='unknown-package==1.0')
        with patch.object(ai_settings,'model_request') as model:asyncio.run(v.run_next())
        model.assert_not_called()
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates')['status'],'failed')

    def test_uploaded_bug_is_repaired_into_separate_version(self):
        bad=self.path.read_text('utf-8').replace("['a',2]","['a',missing_value]")
        self.submit_version(source=bad)
        fixed=self.path.read_text('utf-8')
        response={'usage':{'prompt_tokens':20,'completion_tokens':30},'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_fix','arguments':json.dumps({'source':fixed,'explanation':'修正未定义变量'})}}]}}]}
        failed={'exit_code':1,'log':'NameError: missing_value','files':{},'result':''}
        passed={'exit_code':0,'log':'done','files':{'rows.csv':b'name,value\na,2\nb,4\n'},'result':''}
        with patch.object(v,'validate_candidate',new=AsyncMock(side_effect=[failed,passed])),patch.object(ai_settings,'model_request',return_value=response):
            asyncio.run(v.run_next())
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.activate(db.fetch_one('SELECT id FROM task_version_updates ORDER BY id DESC LIMIT 1'))
        self.assertEqual(v.context(self.task_id)['version'],3)
        self.assertEqual(db.fetch_one('SELECT source FROM task_code_versions WHERE sequence=2')['source'],bad)

    def test_failed_repair_preserves_failure_and_schedule(self):
        self.submit_version()
        db.execute("UPDATE tasks SET trigger_type='daily',enabled=1 WHERE id=?",(self.task_id,))
        with patch.object(v,'validate_candidate',new=AsyncMock(return_value={'exit_code':1,'log':'broken','files':{}})),patch.object(ai_settings,'model_request',side_effect=ValueError('repair failed')):
            asyncio.run(v.run_next())
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.assertIn('broken',db.fetch_one('SELECT log FROM task_version_updates')['log'])
        self.assertEqual(db.fetch_one('SELECT enabled FROM tasks')['enabled'],1)

    def test_notification_one_attempt_and_failure_not_marked_delivered(self):
        from app.feishu import FeishuNotifier
        self.submit_version(requirements='unknown-package==1.0');asyncio.run(v.run_next())
        db.execute('UPDATE tasks SET notify_on_failure=1')
        with patch.object(FeishuNotifier,'send_maintenance_result',side_effect=ValueError('send failed')) as send,patch('app.feishu.FeishuSettings.from_env') as settings:
            settings.return_value.webhook_url='synthetic-url'
            self.assertTrue(v.notify_next());self.assertFalse(v.notify_next());send.assert_called_once()
        self.assertEqual(db.fetch_one('SELECT status FROM maintenance_delivery')['status'],'failed')

    def test_requirements_source_and_unspecified_do_not_invent_contract(self):
        value=v.inferred('CITY="杭州"\nprint("hello")')
        self.assertEqual(value['scope'],{'value':'杭州','origin':'inferred'})
        self.assertEqual(value['summary']['origin'],'unspecified')
        self.assertIsNone(value['acceptance']['value'])

    def test_stale_update_cannot_replace_newer_submission(self):
        self.submit_version(source=self.path.read_text('utf-8')+'\n# first')
        newer=self.submit_version(source=self.path.read_text('utf-8')+'\n# newer')
        self.assertEqual(db.fetch_all('SELECT status FROM task_version_updates ORDER BY id')[0]['status'],'cancelled')
        self.run_update();self.activate(newer)
        self.assertEqual(v.context(self.task_id)['version'],3)

    def test_unrelated_requirement_change_cannot_weaken_acceptance(self):
        new={**CONTRACT,'min_rows':1}
        update=self.submit_version(source='# spiderfly-acceptance: '+json.dumps(new)+'\n'+GOOD,spec={'summary':'改说明'},evidence='只改说明')
        self.assertEqual(update['status'],'conflict')
        detail=db.fetch_one('SELECT contract FROM task_version_details ORDER BY version_id DESC LIMIT 1')
        self.assertEqual(json.loads(detail['contract'])['min_rows'],CONTRACT['min_rows'])

    def test_explicit_quantity_change_preserves_other_conditions(self):
        new={**CONTRACT,'min_rows':3,'max_rows':3}
        update=self.submit_version(source='# spiderfly-acceptance: '+json.dumps(new)+'\n'+GOOD,spec={'quantity':3},evidence='数量改成三条')
        self.assertEqual(update['status'],'pending')
        detail=db.fetch_one('SELECT contract FROM task_version_details ORDER BY version_id DESC LIMIT 1')
        self.assertEqual(json.loads(detail['contract'])['required'],CONTRACT['required'])

    def test_windows_uploaded_newlines_publish_successfully(self):
        update=self.submit_version(source=self.path.read_text('utf-8').replace('\n','\r\n'))
        self.run_update()
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates')['status'],'ready')
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.activate(update)
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates')['status'],'activated')

    def test_user_can_decline_validated_candidate_without_switching_version(self):
        update=self.submit_version()
        self.run_update()
        v.stop_update(update['id'],self.user)
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates')['status'],'cancelled')
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)

    def test_operator_cannot_upload_or_download_versions(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import security
        app=FastAPI();app.include_router(v.router)
        app.dependency_overrides[security.ready_user]=lambda:self.users[1]
        with TestClient(app) as client:
            self.assertEqual(client.get(f'/api/task-versions/versions/{self.base}/download').status_code,403)
            self.assertEqual(client.post(f'/api/task-versions/tasks/{self.task_id}/upload',files={'script':('test.py',b'print(1)')},data={'base_version_id':self.base}).status_code,403)

    def test_stop_signals_actual_trial_and_keeps_current_version(self):
        update=self.submit_version()
        async def trial(job,item,detail,stop):
            db.execute('UPDATE task_version_updates SET stop_requested=1 WHERE id=?',(update['id'],))
            await asyncio.wait_for(stop.wait(),2)
            return {'exit_code':0,'log':'stopped','files':{}}
        with patch.object(v,'_execute_candidate',new=trial):asyncio.run(v.run_next())
        self.assertEqual(db.fetch_one('SELECT status FROM task_version_updates')['status'],'cancelled')
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)

    def test_ai_submission_and_button_use_same_existing_task(self):
        from app import ai_agent as agent
        agent.init_tables()
        thread=agent.create_thread(agent.NewThread(task_id=self.task_id),self.user)
        turn=db.execute("INSERT INTO ai_turns(thread_id,status,config,created_at) VALUES(?,'running','{}',?)",(thread['id'],db.utc_now()))
        draft=agent.save_draft(thread['id'],turn,{'source':self.path.read_text('utf-8')+'\n# AI update','requirements':'','name':'版本测试','description':''})
        result=asyncio.run(agent.dispatch('submit_task_update',{'draft_id':str(draft['draft_id']),'base_version_id':str(self.base),'spec_patch':json.dumps({'summary':'保留这条明确要求'})},thread,turn,[{'role':'user','content':'用途改为保留这条明确要求'}]))
        self.assertEqual(result['status'],'candidate')
        self.assertFalse(asyncio.run(v.run_next()))
        self.assertEqual(v.context(self.task_id)['version_id'],self.base)
        self.activate(result)
        self.assertEqual(v.context(self.task_id)['requirements']['summary']['value'],'保留这条明确要求')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM tasks')['n'],1)
        latest=agent.save_draft(thread['id'],turn,{'source':self.path.read_text('utf-8')+'\n# button update','requirements':'','name':'版本测试','description':''})
        result=v.apply_draft(self.task_id,latest['draft_id'],self.user)
        self.assertEqual(result['status'],'candidate')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM tasks')['n'],1)
