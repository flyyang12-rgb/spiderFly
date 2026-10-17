"""Task checks extend the existing worker, journal and process ownership."""
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

from spiderfly_agent.environment import MAX_REPORT_BYTES, bounded_report, read_probe, verify_packages
from spiderfly_agent.worker import Interrupted, Worker
import test_agent


class EnvironmentTests(unittest.TestCase):
    setUp = test_agent.TemporaryJournal.setUp
    tearDown = test_agent.TemporaryJournal.tearDown
    make_worker = test_agent.WorkerTests.make_worker
    outcome = test_agent.WorkerTests.outcome

    def prepare_worker(self, requirements=''):
        worker = self.make_worker("print('must wait for checks')",requirements=requirements)
        worker.work.mkdir(parents=True)
        worker.deadline = time.monotonic()+30
        worker.command = Mock(return_value=0)
        return worker

    def prepare(self, worker):
        return Worker.prepare_environment(worker,worker.api.request.return_value['requirements'],worker.run_info['package_hash'])

    def facts(self, worker):
        return next(e['environment'] for e in self.journal.pending(worker.run_id) if e['kind']=='environment')

    def test_new_environment_checks_metadata_and_pip_before_ready(self):
        worker = self.prepare_worker('requests==2.32.5')
        facts={'python':'3.12.10','bits':64,'packages':[{'name':'requests','version':'2.32.5'}]}
        with patch('spiderfly_agent.worker.read_probe',return_value=facts):
            python=self.prepare(worker)
        calls=[c.args[0] for c in worker.command.call_args_list]
        self.assertIn('--clear',calls[0])
        self.assertIn('install',calls[1])
        self.assertEqual(calls[-1][-3:],['-m','pip','check'])
        self.assertEqual((python.parent.parent/'.ready').read_text(),worker.run_info['package_hash'])
        self.assertTrue(self.facts(worker)['requirements_verified'])

    def test_pip_conflict_removes_ready_marker_and_records_original_stage(self):
        worker=self.prepare_worker()
        cache=self.root/'envs'/worker.run_info['package_hash'][:32]
        python=cache/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
        python.parent.mkdir(parents=True);python.write_bytes(b'fake')
        (cache/'.ready').write_text(worker.run_info['package_hash'])
        worker.command=Mock(side_effect=[0,1])
        with patch('spiderfly_agent.worker.read_probe',return_value={'python':'3.12.10','bits':64,'packages':[]}):
            with self.assertRaisesRegex(RuntimeError,'一致性检查失败'):self.prepare(worker)
        self.assertFalse((cache/'.ready').exists())
        self.assertEqual(self.facts(worker)['stage'],'pip_check')
        self.assertTrue(self.facts(worker)['cache_reused'])
        self.assertFalse(any('install' in c.args[0] for c in worker.command.call_args_list))

    def test_wrong_direct_version_fails_even_when_pip_check_passes(self):
        worker=self.prepare_worker('requests==2.32.5')
        with patch('spiderfly_agent.worker.read_probe',return_value={'python':'3.12.10','bits':64,'packages':[{'name':'requests','version':'2.32.4'}]}):
            with self.assertRaisesRegex(ValueError,'冻结清单不一致'):self.prepare(worker)
        self.assertEqual(self.facts(worker)['stage'],'requirements')
        self.assertFalse(self.facts(worker)['requirements_verified'])

    def test_bad_interpreter_never_runs_pip_or_business(self):
        worker=self.prepare_worker()
        with patch('spiderfly_agent.worker.read_probe',side_effect=ValueError('wrong interpreter')):
            with self.assertRaisesRegex(ValueError,'wrong interpreter'):self.prepare(worker)
        self.assertEqual(self.facts(worker)['stage'],'interpreter')
        self.assertEqual(worker.command.call_count,2)

    def test_cancellation_during_check_records_failure_and_cannot_mark_ready(self):
        worker=self.prepare_worker()
        worker.command=Mock(side_effect=[0,Interrupted('cancelled')])
        with self.assertRaises(Interrupted):self.prepare(worker)
        self.assertEqual(self.facts(worker)['status'],'failed')
        self.assertFalse(any(e['kind']=='started' for e in self.journal.pending(1)))

    def test_report_persisted_in_journal_before_business_started(self):
        worker=self.make_worker("print('ready')")
        worker.prepare_environment=lambda req,digest:Worker.prepare_environment(worker,req,digest)
        worker.command=Mock(return_value=0)
        with patch('spiderfly_agent.worker.read_probe',return_value={'python':'3.12.10','bits':64,'packages':[]}):worker.run()
        kinds=[e['kind'] for e in self.journal.pending(1)]
        self.assertLess(kinds.index('environment'),kinds.index('started'))
        self.assertEqual(self.outcome()['status'],'succeeded')

    def test_old_controller_cannot_silently_run_without_report_support(self):
        worker=self.make_worker("print('never')")
        worker.run_info.pop('environment_report_version')
        worker.run()
        worker.prepare_environment.assert_not_called()
        self.assertEqual(self.outcome()['status'],'failed')
        self.assertIn('升级主控',self.outcome()['text'])

    def test_probe_reads_real_json_but_never_imports_declared_package(self):
        cache=self.root/'venv';python=cache/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
        path=self.root/'probe.json'
        facts={'python':'3.12.10','bits':64,'executable':str(python),'prefix':str(cache),'base_prefix':str(self.root/'base'),
               'packages':[{'name':'never_import_this_package','version':'1.2.3'}]}
        path.write_text(json.dumps(facts))
        self.assertEqual(read_probe(path,python,cache)['packages'],facts['packages'])
        for changes in ({'python':'3.13.0'}, {'bits':32}, {'prefix':str(self.root)},
                        {'base_prefix':str(cache)}, {'executable':str(Path(sys.executable))}):
            path.write_text(json.dumps({**facts,**changes}))
            with self.assertRaises(ValueError):read_probe(path,python,cache)

    def test_package_constraints_extras_aliases_and_additional_packages(self):
        packages=[{'name':'Pillow','version':'12.0.0'},{'name':'requests','version':'2.32.5'}]
        verify_packages(packages,'Pillow>=11,<13\nrequests[socks]==2.32.5\n# preserved note')
        for requirements in ('requests==1','openpyxl','requests;python_version>"3"','requests @ https://example.com/x.whl'):
            with self.assertRaises(ValueError):verify_packages(packages,requirements)

    def test_duplicate_and_oversized_package_inventory_is_rejected(self):
        for packages in ([{'name':'p','version':'1'}]*1001,
                         [{'name':'My_pkg','version':'1'},{'name':'my-pkg','version':'1'}],
                         [{'name':'bad/name','version':'1'}]):
            with self.assertRaises(ValueError):verify_packages(packages,'')

    def test_entire_wire_report_is_bounded_before_business_not_only_probe_file(self):
        with self.assertRaises(ValueError):bounded_report({'text':'x'*(MAX_REPORT_BYTES+1)})
        worker=self.prepare_worker()
        facts={'python':'3.12.10','bits':64,'packages':[
            {'name':'p'+str(i)+'x'*120,'version':'1.'+'1'*120} for i in range(1000)]}
        # Exercise the final envelope guard independently of the probe limit;
        # a failed inventory must still fit the existing transport.
        with patch('spiderfly_agent.worker.read_probe',return_value=facts), patch('spiderfly_agent.worker.verify_packages'):
            with self.assertRaises(ValueError):self.prepare(worker)
        self.assertEqual(self.facts(worker)['status'],'failed')
        self.assertEqual(self.facts(worker)['packages'],[])
        bounded_report(self.facts(worker))
