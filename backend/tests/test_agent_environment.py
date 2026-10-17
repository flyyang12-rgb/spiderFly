"""Real signed HTTP / SQLite checks for task-environment evidence."""
import hashlib
import json
import unittest

from app import database
from app.services import host_dispatch
from tests import test_hosts


class AgentEnvironmentTests(unittest.TestCase):
    # Reuse the existing synthetic identity/API fixture, without rerunning its tests.
    for _name in ('setUp', 'make_task', 'request', 'success', 'new_code', 'registration',
                  'register', 'signed_headers', 'agent', 'heartbeat', 'queue', 'events', 'detail', 'host'):
        locals()[_name] = getattr(test_hosts.HostApiTests, _name)

    def leased(self, requirements=''):
        host = self.register()
        self.heartbeat(host, agent_version='0.4.0')
        task = self.make_task(requirements=requirements)[0] if requirements else self.task_id
        run = self.queue(host, task_id=task)
        self.heartbeat(host, agent_version='0.4.0')
        claim = self.success(self.agent(host, 'POST', f'/api/agent/runs/{run["id"]}/claim'))
        self.assertEqual(claim['environment_report_version'], 1)
        return host, run

    def report(self, run, **changes):
        frozen = database.fetch_one('SELECT * FROM remote_runs WHERE id=?', (run['id'],))
        return {'report_version':1,'package_hash':frozen['package_hash'],
                'requirements_hash':hashlib.sha256(frozen['requirements'].encode()).hexdigest(),
                'cache_reused':False,'status':'ready','stage':'ready','python':'3.12.10','bits':64,
                'packages':[{'name':'pip','version':'25.0.1'}], 'pip_check':True,
                'interpreter_verified':True,'requirements_verified':True,
                'verified_at':'2026-10-16T00:00:00+00:00','error':'',**changes}

    def environment_event(self, run, seq=1, **changes):
        return {'seq':seq,'kind':'environment','environment':self.report(run, **changes)}

    def test_ready_facts_start_and_outcome_are_atomic_and_bound_to_frozen_run(self):
        host, run = self.leased('requests==2.32.5')
        event = self.environment_event(run, packages=[{'name':'requests','version':'2.32.5'}])
        self.success(self.events(host, run['id'], event, {'seq':2,'kind':'started'},
                                 {'seq':3,'kind':'finished','status':'succeeded','exit_code':0}))
        detail = self.detail(run['id'])
        self.assertEqual(detail['environment_status'], 'ready')
        self.assertEqual(detail['environment']['packages'][0]['version'], '2.32.5')
        self.assertEqual(detail['status'], 'succeeded')
        self.assertIsNone(self.host(host)['active_run_id'])

    def test_new_agent_cannot_start_or_succeed_without_ready_facts(self):
        host, run = self.leased()
        for event in ({'seq':1,'kind':'started'}, {'seq':1,'kind':'finished','status':'succeeded','exit_code':0}):
            self.assertEqual(self.events(host, run['id'], event).status_code, 409)
        self.assertEqual(self.detail(run['id'])['ack_seq'], 0)

    def test_failed_facts_preserve_error_and_prevent_business_start(self):
        host, run = self.leased()
        self.success(self.events(host, run['id'], self.environment_event(run, status='failed',stage='pip_check',pip_check=False,error='synthetic conflict')))
        self.assertEqual(self.events(host, run['id'], {'seq':2,'kind':'started'}).status_code, 409)
        self.success(self.events(host, run['id'], {'seq':2,'kind':'stderr','text':'original pip error'},
                                 {'seq':3,'kind':'finished','status':'failed','exit_code':None,'text':'dependency failed'}))
        self.assertEqual(self.detail(run['id'])['environment']['error'], 'synthetic conflict')

    def test_wrong_hash_or_target_cannot_attach_facts(self):
        host, run = self.leased()
        other = self.register()
        self.assertEqual(self.events(other, run['id'], self.environment_event(run)).status_code, 404)
        for changes in ({'package_hash':'f'*64}, {'requirements_hash':'f'*64}):
            self.assertEqual(self.events(host, run['id'], self.environment_event(run, **changes)).status_code, 409)
        self.assertIsNone(self.detail(run['id'])['environment'])

    def test_actual_versions_must_match_constraints_not_only_pip_check(self):
        host, run = self.leased('requests==2.32.5')
        for packages in ([], [{'name':'requests','version':'2.32.4'}]):
            self.assertEqual(self.events(host, run['id'], self.environment_event(run,packages=packages)).status_code, 422)

    def test_python_bitness_checks_and_metadata_are_validated(self):
        host, run = self.leased()
        for changes in ({'python':'3.13.1'}, {'bits':32}, {'pip_check':False}, {'interpreter_verified':False},
                        {'requirements_verified':False}, {'verified_at':'2026-10-16T00:00:00'},
                        {'packages':[{'name':'Pip','version':'25'},{'name':'pip','version':'25'}]}):
            with self.subTest(changes=changes):
                self.assertEqual(self.events(host, run['id'], self.environment_event(run,**changes)).status_code, 422)

    def test_report_shapes_and_bounds_are_enforced(self):
        host, run = self.leased()
        for changes in ({'packages':[{'name':'x','version':'1'}]*1001}, {'report_version':2}, {'error':'x'*1001},
                        {'executable':'private local path'}, {'packages':[{'name':'bad/name','version':'1'}]}):
            self.assertEqual(self.events(host, run['id'], self.environment_event(run,**changes)).status_code, 422)
        packages=[{'name':'p'+str(i)+'x'*120,'version':'1.'+'1'*120} for i in range(1000)]
        self.assertEqual(self.events(host, run['id'], self.environment_event(run,packages=packages)).status_code, 413)

    def test_replay_is_idempotent_and_conflicting_facts_cannot_overwrite(self):
        host, run = self.leased()
        event = self.environment_event(run)
        self.success(self.events(host, run['id'], event))
        self.success(self.events(host, run['id'], event))
        self.assertEqual(self.events(host, run['id'], self.environment_event(run,cache_reused=True)).status_code, 409)
        self.assertEqual(self.events(host, run['id'], self.environment_event(run,seq=2)).status_code, 409)
        self.assertEqual(database.fetch_one('SELECT COUNT(*) AS n FROM remote_environment_records')['n'], 1)

    def test_batch_gap_rolls_back_environment_and_ack(self):
        host, run = self.leased()
        response = self.events(host, run['id'], self.environment_event(run), {'seq':3,'kind':'started'})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.detail(run['id'])['environment'])
        self.assertEqual(self.detail(run['id'])['ack_seq'], 0)

    def test_old_agent_and_pre_upgrade_events_remain_replayable_without_claiming_checks(self):
        host = self.register()
        run = self.queue(host)
        self.heartbeat(host)
        self.success(self.agent(host,'POST',f'/api/agent/runs/{run["id"]}/claim'))
        event = {'seq':1,'kind':'started'}
        self.success(self.events(host, run['id'], event))
        self.success(self.events(host, run['id'], event))
        self.assertFalse(self.host(host)['environment_reporting_supported'])
        self.assertEqual(self.detail(run['id'])['environment_status'], 'unreported')
        host_dispatch.init_tables()
        self.assertEqual(self.detail(run['id'])['ack_seq'], 1)

    def test_reports_use_frozen_requirements_despite_later_live_edit(self):
        host, run = self.leased('requests==2.32.5')
        database.execute("UPDATE rpa_apps SET requirements_text='requests==1' WHERE id=(SELECT app_id FROM tasks WHERE id=?)", (run['task_id'],))
        self.success(self.events(host, run['id'], self.environment_event(run,packages=[{'name':'requests','version':'2.32.5'}])))
        self.assertEqual(self.detail(run['id'])['environment_status'], 'ready')

    def test_admin_dependency_details_show_target_facts_member_is_denied(self):
        host, run = self.leased()
        self.success(self.events(host,run['id'], self.environment_event(run)))
        path=f'/api/apps/{self.app_id}/dependency-details'
        value=self.success(self.request('GET',path))
        self.assertEqual(value['remote_environments'][0]['run_id'],run['id'])
        self.assertEqual(value['environments'],[])
        self.assertEqual(self.request('GET',path,user='member').status_code,403)
        self.assertEqual(self.request('GET',f'/api/remote-runs/{run["id"]}',user='member').status_code,403)

    def test_environment_cannot_be_injected_into_another_event(self):
        host, run = self.leased()
        self.assertEqual(self.events(host,run['id'],{'seq':1,'kind':'stdout','environment':self.report(run)}).status_code,422)
        self.assertEqual(self.events(host,run['id'],{'seq':1,'kind':'environment'}).status_code,422)
