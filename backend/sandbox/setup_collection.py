"""Prepare an independent collection-v1 profile inside WSL Ubuntu."""
from pathlib import Path
import json
import os
import subprocess

base = Path.home() / '.local/share/spiderfly-sandbox'
root = Path.home() / '.local/share/spiderfly-collection-v2'
packages = {'scrapling': '0.4.15', 'playwright': '1.62.0', 'requests': '2.32.5',
            'openpyxl': '3.1.5', 'beautifulsoup4': '4.13.5', 'curl_cffi': '0.16.3'}
if not (base / 'root/usr/bin/bwrap').is_file():
    raise RuntimeError('请先运行 setup_runtime.py 准备基础隔离工具')
root.mkdir(parents=True, exist_ok=True)
environment = {**os.environ, 'PYTHONPATH': str(base / 'pip'), 'PLAYWRIGHT_BROWSERS_PATH': str(root / 'browsers')}
subprocess.run(['python3', '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple',
                '--only-binary=:all:', '--upgrade', '--target', str(root / 'python'),
                *[f'{name}[fetchers]=={version}' if name == 'scrapling' else f'{name}=={version}' for name, version in packages.items()]], env=environment, check=True)
environment['PYTHONPATH'] = str(root / 'python')
subprocess.run(['python3', '-m', 'playwright', 'install', 'chromium'], env=environment, check=True)
(root / 'manifest.json').write_text(json.dumps({'profile': 'collection-v1', 'revision': 2, 'packages': packages}))
print('collection-v1 installed; run a browser trial to verify system libraries')
