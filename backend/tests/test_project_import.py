import io
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from zipfile import ZipFile, ZipInfo
from app import project_packages
from app.project_archive import extract_project
from app.dependency_analysis import analyze_project_sources

class LegacyName(ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode('gbk'), self.flag_bits & ~0x800

def archive(files, legacy=False):
    output = io.BytesIO()
    with ZipFile(output, 'w') as package:
        for name, content in files.items():
            package.writestr(LegacyName(name) if legacy else name, content)
    return output.getvalue()

class ProjectImportTests(unittest.TestCase):
    def test_gbk_names_are_readable_and_portable_to_old_agents(self):
        content = archive({'项目/启动.py': 'print("正常")', '项目/数据/报表.txt': 'data'}, True)
        self.assertEqual(project_packages.inspect(content, '')['entries'], ['项目/启动.py'])
        normalized = project_packages.normalize(content)
        self.assertEqual(project_packages.inspect(normalized, '项目/启动.py')['entry'], '项目/启动.py')
        with ZipFile(io.BytesIO(normalized)) as package:
            self.assertTrue(all(item.flag_bits & 0x800 for item in package.infolist()))
        with TemporaryDirectory() as temp:
            script, root = extract_project(io.BytesIO(content), '项目/启动.py', Path(temp)/'project')
            self.assertEqual(script.name, '启动.py')
            self.assertEqual((root/'数据/报表.txt').read_text(), 'data')

    def test_old_garbled_entry_remains_usable(self):
        content = archive({'项目/启动.py': 'pass'}, True)
        old_entry = '项目/启动.py'.encode('gbk').decode('cp437')
        self.assertEqual(project_packages.inspect(content, old_entry)['entry'], '项目/启动.py')

    def test_normal_utf8_archive_preserves_digest(self):
        content = archive({'项目/启动.py': 'pass'})
        self.assertEqual(content, project_packages.normalize(content))

    def test_project_imports_include_helpers_and_preserve_pins(self):
        result = analyze_project_sources({'main.py': 'import helper', 'helper.py': 'import requests\nimport cv2\nimport json'},
                                         'main.py', 'requests==2.32.3', [{'id':'unknown:cv2','action':'package','requirement':'opencv-python'}])
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['requirements'], 'opencv-python\nrequests==2.32.3')
        self.assertEqual(next(item for item in result['items'] if item['module']=='helper')['kind'], 'local')

    def test_missing_business_module_is_unresolved(self):
        result = analyze_project_sources({'main.py': 'from 公共库.报表 import run'}, 'main.py')
        self.assertFalse(result['ready'])
        self.assertIn('unknown:公共库', result['unresolved'])

    def test_unrelated_sibling_does_not_hide_missing_module(self):
        result = analyze_project_sources({'main.py': 'import helper', 'unused/helper.py': 'pass'}, 'main.py')
        self.assertFalse(result['ready'])

    def test_syntax_and_path_fail_before_running_anything(self):
        with self.assertRaises(ValueError):
            project_packages.inspect(archive({'../启动.py':'pass'}, True), '')
        with self.assertRaises(ValueError):
            analyze_project_sources({'main.py':'def:'}, 'main.py')

    def test_worker_reads_requirements_from_zip_and_all_sources(self):
        content = archive({'项目/main.py':'import helper', '项目/helper.py':'import requests',
                           '项目/requirements.txt':'requests>=2.31'}, True)
        result = project_packages.analyze_dependencies(content, '项目/main.py')
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['requirements'], 'requests>=2.31')


class ProjectImportApiTests(unittest.TestCase):
    def test_preview_and_save_recheck_missing_manifest(self):
        from fastapi.testclient import TestClient
        from app import security, database
        from app.main import app
        from tests.test_app_api import ManagedAppApiTests
        with ManagedAppApiTests().fixture() as fixture:
            with __import__('contextlib').closing(TestClient(app, cookies={security.SESSION_COOKIE_NAME: security.create_session(fixture['user']['id'])[0]})) as client:
                # Synthetic source only, no package install or business execution.
                content = archive({'项目/main.py':'import requests\nimport helper', '项目/helper.py':'import json'}, True)
                result = client.post('/api/apps/analyze-project-dependencies',
                    data={'project_entry':'项目/main.py'}, files={'project_archive':('project.zip',content)})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertTrue(result.json()['ready'])
                self.assertEqual(result.json()['requirements'], 'requests')
                saved = client.post('/api/apps',data={'name':'ZIP依赖测试','project_entry':'项目/main.py','enabled':'false',
                    'request_id':'project-import-check-20261127'}, files={'project_archive':('project.zip',content)})
                self.assertEqual(saved.status_code,201,saved.text)
                self.assertEqual(saved.json()['requirements_text'],'requests')
                missing = archive({'main.py':'from 公共库 import run'})
                result = client.post('/api/apps',data={'name':'不可保存缺项','project_entry':'main.py'},
                    files={'project_archive':('missing.zip',missing)})
                self.assertEqual(result.status_code,400,result.text)
                self.assertIn('公共库',result.json()['detail'])
