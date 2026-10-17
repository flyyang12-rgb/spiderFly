"""Static source, real HTTP/frozen SQLite and first-environment lifecycle checks."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from app import database, dependency_analysis as d, environments, main, security, task_versions
from app.services import cron_runs, execution_queue
from tests import test_app_api, test_member_management


class StaticDependenciesTests(unittest.TestCase):
    def analyze(self, source, requirements='', decisions=None, filename='main.py'):
        return d.analyze_source(source, filename, requirements, decisions)

    def test_required_catalog_and_aliases(self):
        source = ('import requests,httpx,pandas,numpy,openpyxl,lxml,yaml,dateutil,DrissionPage\n'
                  'from PIL import Image\nfrom bs4 import BeautifulSoup\nimport win32com.client,win32api,pythoncom\n')
        value=self.analyze(source)
        self.assertTrue(value['ready'])
        self.assertEqual(set(value['requirements'].splitlines()), {'requests','httpx','pandas','numpy','openpyxl','lxml','PyYAML','python-dateutil','DrissionPage','Pillow','beautifulsoup4','pywin32'})

    def test_alias_submodule_from_duplicate_and_line_numbers(self):
        value=self.analyze('import requests as r, requests.sessions\nfrom requests import get\n# import invented\ns="import invented"\n')
        self.assertEqual(value['requirements'], 'requests')
        self.assertEqual(value['items'][0]['lines'],[1,2])
        self.assertEqual(len(value['items']),1)

    def test_stdlib_is_pinned_catalog_not_installed_package_scan(self):
        with patch('importlib.metadata.distributions',side_effect=AssertionError('must not inspect controller packages')):
            value=self.analyze('import os,json,sys,tomllib,sqlite3,winreg\n')
        self.assertEqual(value['requirements'],'')
        self.assertTrue(all(i['kind']=='stdlib' for i in value['items']))

    def test_type_alias_excluded_but_else_import_requires_decision(self):
        value=self.analyze('from typing import TYPE_CHECKING as TC\nif TC:\n import fake_types\nelse:\n import requests\n')
        self.assertEqual(next(i for i in value['items'] if i['module']=='fake_types')['kind'],'type_only')
        self.assertEqual(value['unresolved'],['conditional:requests'])

    def test_shadowed_type_name_is_not_treated_as_pure_typing(self):
        value=self.analyze('from typing import TYPE_CHECKING\nTYPE_CHECKING=True\nif TYPE_CHECKING:\n import pandas\n')
        self.assertEqual(value['unresolved'],['conditional:pandas'])

    def test_try_fallback_and_platform_conditions_are_never_evaluated(self):
        value=self.analyze('try:\n import requests\nexcept ImportError:\n import httpx\nimport sys\nif sys.platform=="win32":\n import win32api\n')
        self.assertEqual(set(value['unresolved']),{'conditional:requests','conditional:httpx','conditional:win32api'})

    def test_unknown_relative_and_self_not_blindly_installed(self):
        value=self.analyze('from . import helper\nimport main\nimport invented_business_module\n')
        self.assertEqual(set(i['kind'] for i in value['items']),{'relative','self','unknown'})
        self.assertEqual(value['requirements'],'')

    def test_dynamic_literal_variable_alias_and_exec_are_reviewed(self):
        value=self.analyze('from importlib import import_module as load\nload("PIL.Image")\n__import__("requests")\nload(prefix+suffix)\nexec("import hidden")\n')
        self.assertEqual(len(value['unresolved']),4)
        self.assertNotIn('hidden', [i['module'] for i in value['items']])

    def test_main_guard_does_not_make_runtime_import_optional(self):
        self.assertEqual(self.analyze('if __name__=="__main__":\n import requests\n')['requirements'],'requests')

    def test_user_constraints_and_implicit_packages_are_retained(self):
        value=self.analyze('import pandas,requests\n','pandas==2.2.3\nopenpyxl>=3.1\nrequests[socks]>=2.31')
        self.assertEqual(set(value['requirements'].splitlines()),{'pandas==2.2.3','openpyxl>=3.1','requests[socks]>=2.31'})

    def test_normalized_duplicate_and_conflicting_lines(self):
        self.assertEqual(len(d.parse_requirements('My_Package==1\nmy-package==1')),1)
        for value in ('pandas==1\npandas==2','pandas\nPandas>=2','pandas==1,==2','pandas==1,!=1','pandas>=3,<2'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'冲突'): d.parse_requirements(value)

    def test_unsafe_requirements_are_rejected(self):
        for value in ('--index-url x','git+https://example.test/pkg','x @ https://example.test/x','./local','C:\\local','${PACKAGE}','x\\\n>=1','requests;python_version>="3"','pkg===*','spiderfly-runtime'):
            with self.subTest(value=value),self.assertRaises(ValueError): d.parse_requirements(value)

    def test_explicit_resolution_and_exclusion_reasons_are_frozen(self):
        choices=[{'id':'unknown:biz','action':'package','requirement':'my-business==1'},
                 {'id':'conditional:PIL','action':'exclude','reason':'不执行图像分支'}]
        value=self.analyze('import biz\nif image:\n from PIL import Image\n',decisions=choices)
        self.assertTrue(value['ready']);self.assertEqual(value['requirements'],'my-business==1')
        self.assertEqual(value['exclusions'][0]['reason'],'不执行图像分支')

    def test_incomplete_stale_or_forged_resolution_rejected(self):
        for choice in ({'id':'unknown:biz','action':'exclude','reason':''},
                       {'id':'unknown:biz','action':'package','requirement':'-r x'},
                       {'id':'unknown:old','action':'exclude','reason':'stale'},
                       {'id':'unknown:biz','action':'anything'}):
            with self.subTest(choice=choice),self.assertRaises(ValueError):self.analyze('import biz',decisions=[choice])

    def test_user_constraint_wins_and_conflicting_resolution_rejected(self):
        self.assertEqual(self.analyze('import biz','requests==2.32.5',[{'id':'unknown:biz','action':'package','requirement':'requests'}])['requirements'],'requests==2.32.5')
        with self.assertRaisesRegex(ValueError,'冲突'):
            self.analyze('import biz','requests==2.32.5',[{'id':'unknown:biz','action':'package','requirement':'requests==1'}])

    def test_syntax_errors_report_line_and_unsupported_syntax(self):
        for source,line in [('x=1\nif True print(1)',2),('return 1',1)]:
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,f'第 {line} 行'):self.analyze(source)

    def test_limits_and_non_utf8_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError,'2MB'):self.analyze('x'*(d.MAX_SOURCE+1))
        with self.assertRaisesRegex(ValueError,'UTF-8'):environments.validate_python_upload('main.py',b'\xff')
        with patch.object(d,'MAX_NODES',4),self.assertRaisesRegex(ValueError,'资源限制'):self.analyze('a=1\nb=2')

    def test_subprocess_timeout_and_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'must-not-exist'
            source=f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\nraise RuntimeError("must not run")\n'
            value=d.analyze(source,'main.py')
            self.assertTrue(value['ready']);self.assertFalse(marker.exists())
        with patch.object(d.subprocess,'run',side_effect=subprocess.TimeoutExpired('worker',5)),self.assertRaisesRegex(ValueError,'超时'):
            d.analyze('pass','main.py')

    def test_real_target_validator_rejects_32_bit_or_wrong_python(self):
        for version,bits in [('3.12.10',32),('3.13.0',64)]:
            with self.subTest(version=version,bits=bits),patch.object(d.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps({'version':version,'bits':bits}))),self.assertRaisesRegex(ValueError,'64 位 Python 3.12'):
                d.local_python_info('synthetic-unsupported-python')


class DependencyLifecycleTests(unittest.TestCase):
    request=test_member_management.MemberManagementTests.request
    def setUp(self):
        helper=test_app_api.ManagedAppApiTests()
        self.fixture=self.enterContext(helper.fixture())
        self.user=self.fixture['user'];self.token=security.create_session(self.user['id'])[0]
        self.index=0

    def multipart(self,path,source='import requests\n',values=None,token=True,filename='main.py'):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,raise_app_exceptions=False),base_url='http://testserver',
                 cookies={security.SESSION_COOKIE_NAME:self.token} if token else {}) as client:
                response=await client.post(path,data=values or {},files={'script':(filename,source.encode('utf-8'),'text/x-python')})
                try:body=response.json()
                except ValueError:body={'detail':response.text[:500]}
                return response.status_code,body
        return asyncio.run(run())

    def create(self,source='import requests\n',**values):
        self.index+=1
        code,item=self.multipart('/api/apps',source,{'name':f'dependency-{self.index}',
            'notify_on_success':'false','notify_on_failure':'false',**values})
        self.assertEqual(code,201,item);return item

    def preparing(self,**values):
        item=self.create('import json\n',trigger_type='daily',trigger_config='{"time":"09:00"}',**values)
        app=database.fetch_one('SELECT * FROM rpa_apps WHERE id=?',(item['id'],))
        database.execute("UPDATE rpa_apps SET environment_status='building' WHERE id=?",(item['id'],))
        version=database.fetch_one('SELECT active_version_id FROM maintenance_policies WHERE task_id=?',(item['task']['id'],))['active_version_id']
        return item,app,version

    def publish(self,item,app,version,now='2026-10-16T02:00:00+00:00'):
        env=database.RPA_ENVS_DIR/f'app_{app["id"]}_r1_87654321'
        python=environments._environment_python(env);python.parent.mkdir(parents=True,exist_ok=True);python.write_bytes(b'synthetic')
        with patch.object(environments,'utc_now',return_value=now):
            return environments._publish_environment(app,version,env,app['requirements_text'],{'python':'3.12.10','bits':64,'packages':[]},['[检查依赖]\nOK'])

    def test_analysis_requires_admin_and_leaves_no_business_rows(self):
        before=database.fetch_one('SELECT COUNT(*) n FROM rpa_apps')['n']
        self.assertEqual(self.multipart('/api/apps/analyze-dependencies',token=False)[0],401)
        database.execute("UPDATE users SET role='operator' WHERE id=?",(self.user['id'],))
        self.assertEqual(self.multipart('/api/apps/analyze-dependencies')[0],403)
        database.execute("UPDATE users SET role='admin' WHERE id=?",(self.user['id'],))
        code,value=self.multipart('/api/apps/analyze-dependencies')
        self.assertEqual(code,200,value);self.assertEqual(value['requirements'],'requests')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM rpa_apps')['n'],before)

    def test_upload_freezes_source_final_manifest_and_analysis(self):
        item=self.create('import requests\nfrom PIL import Image\n',requirements_text='requests==2.32.5\nopenpyxl')
        row=database.fetch_one('SELECT d.analysis,v.source,v.requirements FROM dependency_analyses d JOIN task_code_versions v ON v.id=d.version_id WHERE v.task_id=?',(item['task']['id'],))
        frozen=json.loads(row['analysis']);self.assertEqual(frozen['requirements'],row['requirements'])
        self.assertEqual(frozen['source_hash'],d.digest(row['source']))
        self.assertEqual(set(row['requirements'].splitlines()),{'requests==2.32.5','Pillow','openpyxl'})
        self.assertFalse(item['task']['runtime_ready']);self.assertIsNone(item['task']['next_run_at'])

    def test_forged_summary_unresolved_and_syntax_do_not_create_task(self):
        before=database.fetch_one('SELECT COUNT(*) n FROM tasks')['n']
        for source,values in [('import unknown_biz',{'analysis':'{"ready":true}'}),('if True print(1)',{}),('pass',{'requirements_text':'--index-url x'})]:
            code,_=self.multipart('/api/apps',source,{'name':'bad-input',**values});self.assertEqual(code,400)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks')['n'],before)

    def test_explicit_exclusion_saved_and_details_admin_only(self):
        item=self.create('import helper',dependency_decisions=json.dumps([{'id':'unknown:helper','action':'exclude','reason':'业务目录已有模块，脚本自行加入路径'}]))
        path=f'/api/apps/{item["id"]}/dependency-details'
        self.assertEqual(self.request('GET',path)[0],401)
        code,value=self.request('GET',path,token=self.token);self.assertEqual(code,200,value)
        self.assertEqual(value['versions'][0]['analysis']['exclusions'][0]['id'],'unknown:helper')

    def test_initial_plan_has_no_occurrences_manual_execution_or_maintenance(self):
        item,app,version=self.preparing()
        task=database.fetch_one('SELECT * FROM tasks WHERE id=?',(item['task']['id'],))
        self.assertEqual(task['first_environment_ready'],0);self.assertIsNone(task['next_run_at'])
        with database.transaction() as conn,self.assertRaises(HTTPException):
            execution_queue._enqueue_task_in_transaction(task['id'],'manual',self.user['id'],conn)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM executions')['n'],0)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM maintenance_jobs')['n'],0)

    def test_ready_after_missed_point_releases_only_future_schedule(self):
        item,app,version=self.preparing();self.assertTrue(self.publish(item,app,version))
        task=database.fetch_one('SELECT * FROM tasks WHERE id=?',(item['task']['id'],))
        self.assertEqual(task['first_environment_ready'],1)
        self.assertEqual(task['next_run_at'],'2026-10-17T01:00:00+00:00')
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM executions')['n'],0)
        self.assertEqual(database.fetch_one('SELECT status FROM environment_records')['status'],'ready')
        self.assertFalse(self.publish(item,app,version))

    def test_disabled_task_ready_does_not_reenable_or_restore_plan(self):
        item,app,version=self.preparing(enabled='false');self.assertTrue(self.publish(item,app,version))
        task=database.fetch_one('SELECT * FROM tasks WHERE id=?',(item['task']['id'],))
        self.assertEqual(task['enabled'],0);self.assertIsNone(task['next_run_at'])

    def test_plan_edit_while_preparing_uses_latest_valid_plan(self):
        item,app,version=self.preparing()
        code,value=self.request('PATCH',f'/api/tasks/{item["task"]["id"]}',{'trigger_type':'weekly','trigger_config':{'time':'11:00','weekdays':[5]}},self.token)
        self.assertEqual(code,200,value);self.assertIsNone(value['next_run_at'])
        self.assertTrue(self.publish(item,app,version))
        task=database.fetch_one('SELECT next_run_at FROM tasks WHERE id=?',(item['task']['id'],))
        self.assertEqual(task['next_run_at'],'2026-10-16T03:00:00+00:00')

    def test_stale_revision_deleted_or_new_active_version_never_publishes(self):
        for scenario in ('revision','delete','version'):
            with self.subTest(scenario=scenario):
                item,app,version=self.preparing()
                if scenario=='revision':database.execute('UPDATE rpa_apps SET revision=revision+1 WHERE id=?',(app['id'],))
                if scenario=='delete':database.execute('UPDATE rpa_apps SET archived=1 WHERE id=?',(app['id'],))
                if scenario=='version':database.execute('UPDATE maintenance_policies SET active_version_id=NULL WHERE task_id=?',(item['task']['id'],))
                self.assertFalse(self.publish(item,app,version))
                self.assertEqual(database.fetch_one('SELECT first_environment_ready FROM tasks WHERE id=?',(item['task']['id'],))['first_environment_ready'],0)

    def test_cron_cannot_register_preparation_occurrence_even_with_stale_date(self):
        item=self.create('pass',trigger_type='cron',trigger_config='{"expression":"0 9 * * *"}')
        database.execute('UPDATE tasks SET next_run_at=? WHERE id=?',('2026-10-15T01:00:00+00:00',item['task']['id']))
        self.assertIsNone(cron_runs.register_due(item['task']['id'],'2026-10-16T02:00:00+00:00'))
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM cron_rounds')['n'],0)

    def test_retry_reuses_identity_and_gate_survives_restart(self):
        item=self.create('pass',trigger_type='daily',trigger_config='{"time":"09:00"}')
        database.execute("UPDATE rpa_apps SET environment_status='failed',environment_error='synthetic network failure' WHERE id=?",(item['id'],))
        environments.request_rebuild(item['id'],self.user['id'])
        database.init_db()
        from app.scheduling import reconcile_schedules
        reconcile_schedules()
        self.assertIsNone(database.fetch_one('SELECT next_run_at FROM tasks WHERE id=?',(item['task']['id'],))['next_run_at'])
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM tasks WHERE app_id=?',(item['id'],))['n'],1)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM task_code_versions WHERE task_id=?',(item['task']['id'],))['n'],1)

    def test_version_upload_is_analyzed_but_waits_for_confirmation(self):
        item=self.create('pass')
        # No business execution is needed to submit a candidate.
        current=database.fetch_one('SELECT active_version_id FROM maintenance_policies WHERE task_id=?',(item['task']['id'],))['active_version_id']
        code,value=self.multipart(f'/api/task-versions/tasks/{item["task"]["id"]}/upload','import pandas',{'base_version_id':str(current)})
        self.assertEqual(code,200,value);self.assertEqual(value['status'],'candidate')
        self.assertEqual(database.fetch_one('SELECT active_version_id FROM maintenance_policies WHERE task_id=?',(item['task']['id'],))['active_version_id'],current)
        candidate=database.fetch_one('SELECT v.requirements,d.analysis FROM task_code_versions v JOIN dependency_analyses d ON d.version_id=v.id WHERE v.task_id=? ORDER BY v.sequence DESC',(item['task']['id'],))
        self.assertEqual(candidate['requirements'],'pandas');self.assertTrue(json.loads(candidate['analysis'])['ready'])

    def test_incompatible_python_is_rejected_before_new_task(self):
        with patch.object(d,'target_context',side_effect=ValueError('自动依赖准备仅支持 64 位 Python 3.12')):
            code,value=self.multipart('/api/apps','pass',{'name':'unsupported'})
        self.assertEqual(code,400,value);self.assertIn('64 位',value['detail'])

    def test_target_change_discards_old_build_without_releasing_schedule(self):
        item,app,version=self.preparing()
        database.execute('UPDATE tasks SET target_host_id=99 WHERE id=?',(item['task']['id'],))
        self.assertFalse(self.publish(item,app,version))
        self.assertEqual(database.fetch_one('SELECT environment_status FROM rpa_apps WHERE id=?',(app['id'],))['environment_status'],'pending')
        self.assertNotIn(app['id'],environments.pending_environment_ids())
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM environment_records')['n'],0)

    def test_evidence_write_failure_rolls_back_ready_and_plan_release(self):
        item,app,version=self.preparing()
        with self.assertRaises(TypeError):
            environments._publish_environment(app,version,database.RPA_ENVS_DIR/'candidate',app['requirements_text'],{'unserializable':object()},[])
        self.assertEqual(database.fetch_one('SELECT environment_status FROM rpa_apps WHERE id=?',(app['id'],))['environment_status'],'building')
        self.assertIsNone(database.fetch_one('SELECT next_run_at FROM tasks WHERE id=?',(item['task']['id'],))['next_run_at'])

    def test_ready_task_name_and_plan_edit_keeps_version_and_environment(self):
        item,app,version=self.preparing();self.assertTrue(self.publish(item,app,version))
        before=database.fetch_one('SELECT env_path,revision FROM rpa_apps WHERE id=?',(app['id'],))
        count=database.fetch_one('SELECT COUNT(*) n FROM task_code_versions WHERE task_id=?',(item['task']['id'],))['n']
        code,value=self.request('PATCH',f'/api/tasks/{item["task"]["id"]}',{'name':'renamed','trigger_type':'weekly','trigger_config':{'time':'13:00','weekdays':[1]}},self.token)
        self.assertEqual(code,200,value)
        self.assertEqual(database.fetch_one('SELECT env_path,revision FROM rpa_apps WHERE id=?',(app['id'],)),before)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM task_code_versions WHERE task_id=?',(item['task']['id'],))['n'],count)

    def test_confirmed_dependency_change_builds_new_environment_and_preserves_old(self):
        item,app,version=self.preparing();self.assertTrue(self.publish(item,app,version))
        old=database.fetch_one('SELECT env_path FROM rpa_apps WHERE id=?',(app['id'],))['env_path']
        candidate=task_versions.submit(item['task']['id'],'import requests','',{},'',self.user['id'],base_id=version,origin='manual_candidate')
        code,value=self.request('POST',f'/api/task-versions/updates/{candidate["id"]}/activate',token=self.token)
        self.assertEqual(code,200,value)
        fresh=database.fetch_one('SELECT * FROM rpa_apps WHERE id=?',(app['id'],))
        self.assertEqual((fresh['environment_status'],fresh['requirements_text']),('pending','requests'))
        self.assertGreater(fresh['revision'],app['revision']);self.assertTrue(Path(old).is_dir())
        self.assertEqual(database.fetch_one('SELECT first_environment_ready FROM tasks WHERE id=?',(item['task']['id'],))['first_environment_ready'],0)

    def test_install_and_pip_check_failure_are_not_business_failures(self):
        for failed_phase in ('安装依赖','检查依赖','记录环境'):
            with self.subTest(failed_phase=failed_phase):
                item=self.create('import requests')
                async def command(*args,phase,**kw):
                    if phase=='创建虚拟环境':
                        python=environments._environment_python(Path(args[-1]));python.parent.mkdir(parents=True,exist_ok=True);python.write_bytes(b'synthetic')
                    return (1,'synthetic network/verification error') if phase==failed_phase else (0,'synthetic success')
                with patch.object(environments,'_run_command',side_effect=command):asyncio.run(environments.build_environment(item['id']))
                app=database.fetch_one('SELECT * FROM rpa_apps WHERE id=?',(item['id'],))
                self.assertEqual(app['environment_status'],'failed')
                self.assertIn('synthetic',app['install_log'])
                self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM executions')['n'],0)
                self.assertEqual(database.fetch_one('SELECT COUNT(*) n FROM maintenance_jobs')['n'],0)
                self.assertEqual(database.fetch_one('SELECT status FROM environment_records WHERE app_id=?',(item['id'],))['status'],'failed')
