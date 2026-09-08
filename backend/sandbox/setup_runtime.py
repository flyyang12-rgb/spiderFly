"""Run explicitly inside WSL Ubuntu; install only in this user's SpiderFly directory."""
from pathlib import Path
import json
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

root = Path.home() / '.local/share/spiderfly-sandbox'
if (root / 'manifest.json').is_file():
    print('Existing readonly-v1 runtime preserved; upgrades require a new profile')
    sys.exit(0)
root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='packages-', dir=root) as temporary:
    subprocess.run(['apt-get', 'download', 'bubblewrap'], cwd=temporary, check=True)
    packages = list(Path(temporary).glob('bubblewrap_*.deb'))
    if len(packages) != 1:
        raise RuntimeError('Expected one Ubuntu bubblewrap package')
    subprocess.run(['dpkg-deb', '-x', str(packages[0]), str(root / 'root')], check=True)
metadata = json.load(urllib.request.urlopen('https://pypi.org/pypi/pip/json', timeout=30))
wheel = next(item for item in metadata['urls'] if item['filename'].endswith('py3-none-any.whl'))
payload = urllib.request.urlopen(wheel['url'], timeout=30).read()
if hashlib.sha256(payload).hexdigest() != wheel['digests']['sha256']:
    raise RuntimeError('pip wheel digest mismatch')
import io
pip_root = root / 'pip'
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    if any(not (pip_root / item.filename).resolve().is_relative_to(pip_root.resolve()) for item in archive.infolist()):
        raise RuntimeError('Unsafe wheel path')
    archive.extractall(pip_root)
environment = {**os.environ, 'PYTHONPATH': str(pip_root)}
subprocess.run(['python3', '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple',
                '--only-binary=:all:', '--upgrade', '--target', str(root / 'python'),
                'requests==2.32.5', 'openpyxl==3.1.5', 'beautifulsoup4==4.13.5'], check=True, env=environment)
subprocess.run([str(root / 'root/usr/bin/bwrap'), '--version'], check=True)
(root / 'manifest.json').write_text(json.dumps({'profile': 'readonly-v1', 'python': '3.12',
    'packages': {'requests': '2.32.5', 'openpyxl': '3.1.5', 'beautifulsoup4': '4.13.5'}}))
print('SpiderFly readonly-v1 runtime prepared')
