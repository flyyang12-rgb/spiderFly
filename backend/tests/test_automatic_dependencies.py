import unittest
from app import dependency_analysis as deps

class AutomaticDependencyTests(unittest.TestCase):
    def prepare(self, source, requirements='', decisions=None):
        return deps.automatic_preparation(deps.analyze_source(source,'main.py',requirements,decisions))

    def test_screenshot_packages_and_optional_imports_resolve_without_choices(self):
        result=self.prepare('import cv2,onnxruntime\ntry:\n import torch\n from paddleocr import PaddleOCR\nexcept ImportError:\n pass\n')
        self.assertTrue(result['ready'],result)
        self.assertEqual(set(result['requirements'].splitlines()),{'opencv-contrib-python','onnxruntime','torch','paddleocr','paddlepaddle'})
        self.assertEqual(result['unresolved'],[])
        self.assertTrue(result['automatic'])

    def test_existing_variants_and_version_constraints_are_preserved(self):
        result=self.prepare('import cv2,onnxruntime','opencv-python-headless==4.10.0.84\nonnxruntime-gpu>=1.20')
        self.assertEqual(set(result['requirements'].splitlines()),{'opencv-python-headless==4.10.0.84','onnxruntime-gpu>=1.20'})

    def test_generic_packages_are_attempted_by_package_name(self):
        result=self.prepare('import rich\ntry:\n import tenacity\nexcept ImportError: pass\n')
        self.assertTrue(result['ready'])
        self.assertEqual(set(result['requirements'].splitlines()),{'rich','tenacity'})

    def test_existing_explicit_exclusions_stay_excluded(self):
        result=self.prepare('if image:\n import requests',decisions=[{'id':'conditional:requests','action':'exclude','reason':'可选分支'}])
        self.assertEqual(result['requirements'],'')
        self.assertTrue(result['ready'])

    def test_dynamic_literals_resolve_but_expressions_do_not_become_package_names(self):
        result=self.prepare('import importlib\nimportlib.import_module("cv2")\nimportlib.import_module(name)\nexec(code)')
        self.assertTrue(result['ready'],result)
        self.assertEqual(result['requirements'],'opencv-python')
        self.assertEqual(len(result['deferred']),2)

    def test_type_only_imports_do_not_install_runtime_packages(self):
        result=self.prepare('from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n import torch')
        self.assertEqual(result['requirements'],'')
        self.assertTrue(result['ready'])

    def test_paddle_engine_is_added_even_when_ocr_is_explicitly_declared(self):
        result=self.prepare('import paddleocr','paddleocr==3.0.0')
        self.assertEqual(set(result['requirements'].splitlines()),{'paddleocr==3.0.0','paddlepaddle','opencv-contrib-python'})

    def test_paddle_gpu_is_not_replaced_by_cpu_engine(self):
        result=self.prepare('import paddleocr','paddleocr\npaddlepaddle-gpu==3.0.0')
        self.assertNotIn('paddlepaddle',result['requirements'].splitlines())

    def test_missing_files_are_not_treated_as_pypi_packages(self):
        result=self.prepare('from . import helper\nfrom 公共库 import run')
        self.assertFalse(result['ready'])
        self.assertEqual(result['requirements'],'')
        with self.assertRaisesRegex(ValueError,'项目缺少模块'):deps.require_resolved(result)

    def test_worker_integration_uses_automatic_resolution(self):
        result=deps.analyze('import cv2,onnxruntime','main.py')
        self.assertTrue(result['automatic'])
        self.assertEqual(set(result['requirements'].splitlines()),{'opencv-python','onnxruntime'})
