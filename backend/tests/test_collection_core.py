import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from app import collection, config
from tests.test_collection import CONTRACT, SOURCE

class CheckpointTests(unittest.TestCase):
    def test_checkpoint_scope_and_original_contract_control_cleanup(self):
        async def run():
            paths = []
            async def execute(*args, **kwargs):
                path = kwargs['checkpoint']
                paths.append(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                return {'exit_code': 0, 'log': '', 'files': {'books.csv': b'title\na\n'},
                        'network': {'resumed': True, 'requests': 0}}
            with tempfile.TemporaryDirectory() as root, patch.object(config, 'DATA_DIR', Path(root)), patch.object(collection.runtime, 'execute_source', side_effect=execute):
                await collection.execute(SOURCE, '', CONTRACT, state_scope='task:1')
                self.assertTrue(paths[-1].exists())
                await collection.execute(SOURCE, '', CONTRACT, state_scope='task:2')
                self.assertNotEqual(paths[0], paths[1])
                await collection.execute(SOURCE+'\n# v2', '', CONTRACT, state_scope='task:1')
                self.assertNotEqual(paths[0], paths[2])
                self.assertFalse(paths[0].exists())
        asyncio.run(run())

    def test_successful_resume_clears_checkpoint_after_validation(self):
        async def run():
            paths=[]
            async def execute(*args, **kwargs):
                path=kwargs['checkpoint']; paths.append(path)
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
                return {'exit_code':0,'log':'','files':{'books.csv':b'title\na\nb\n'},
                        'network':{'resumed':True,'requests':0}}
            with tempfile.TemporaryDirectory() as root, patch.object(config,'DATA_DIR',Path(root)), patch.object(collection.runtime,'execute_source',side_effect=execute):
                result=await collection.execute(SOURCE,'',CONTRACT,state_scope='task:1')
                self.assertEqual(result['exit_code'],0)
                self.assertFalse(paths[0].exists())
        asyncio.run(run())

@unittest.skipUnless(os.environ.get('SPIDERFLY_TEST_COLLECTION')=='1','requires WSL collection')
class NativeTests(unittest.TestCase):
    def test_real_crawl_resume_then_fresh_scheduled_collection(self):
        source = collection.MARKER + '\nSPIDERFLY_ACCEPTANCE=' + repr(CONTRACT) + """
from spiderfly_collection import crawl
import csv, os
from pathlib import Path

def parse(page):
    return ([{'title': x} for x in page.css('h3 a::attr(title)').getall()[:2]], [])
rows=crawl(['https://books.toscrape.com/'],parse)
with (Path(os.environ['SPIDERFLY_ARTIFACT_DIR'])/'books.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['title']);writer.writeheader();writer.writerows(rows)
"""
        async def run():
            with tempfile.TemporaryDirectory() as root, patch.object(config,'DATA_DIR',Path(root)):
                # Simulate a failed output-validation stage after a successful page commit.
                original=collection.runtime.validate_result
                with patch.object(collection.runtime,'validate_result',side_effect=ValueError('output failed')):
                    first=await collection.execute(source,'',CONTRACT,state_scope='task:101')
                self.assertEqual(first['exit_code'],0,first['log'])
                second=await collection.execute(source,'',CONTRACT,state_scope='task:101')
                self.assertEqual(second['exit_code'],0,second['log'])
                self.assertTrue(second['network']['resumed'])
                self.assertEqual(second['network']['requests'],0)
                self.assertEqual(first['files'],second['files'])
                self.assertEqual(original(second,collection.runtime.validate_contract(CONTRACT))['rows'],2)
                third=await collection.execute(source,'',CONTRACT,state_scope='task:101')
                self.assertGreater(third['network']['requests'],0)
                self.assertFalse(third['network']['resumed'])
        asyncio.run(run())

    def test_native_dynamic_session_and_action_failure(self):
        source=collection.MARKER+'\nSPIDERFLY_ACCEPTANCE='+repr(CONTRACT)+"""
from spiderfly_collection import dynamic_session
with dynamic_session() as session:
    page=session.fetch('https://books.toscrape.com/',wait_for='h3')
    assert len(page.css('h3')) == 20
    print('native dynamic passed')
"""
        result=asyncio.run(collection.execute(source,'',CONTRACT,timeout=45))
        self.assertEqual(result['exit_code'],0,result['log'])
        self.assertIn('native dynamic passed',result['log'])
        source=source.replace("page=session.fetch('https://books.toscrape.com/',wait_for='h3')",
            "def broken(page): raise ValueError('action failed')\n    page=session.fetch('https://books.toscrape.com/',wait_for='h3',page_action=broken)")
        failed=asyncio.run(collection.execute(source,'',CONTRACT,timeout=45))
        self.assertEqual(failed['exit_code'],1)
        self.assertIn('action failed',failed['log'])

    def test_native_transport_fixture(self):
        import subprocess
        probe=(Path(__file__).with_name('collection_native_probe.py')).resolve().as_posix()
        probe='/mnt/'+probe[0].lower()+probe[2:]
        result=subprocess.run(['wsl','-d','Ubuntu','--','env',
            'python3', '-c',
            "import sys,runpy;from pathlib import Path;sys.path.insert(0,str(Path.home()/'.local/share/spiderfly-collection-v2/python'));runpy.run_path(sys.argv[1],run_name='__main__')",probe],
            capture_output=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
