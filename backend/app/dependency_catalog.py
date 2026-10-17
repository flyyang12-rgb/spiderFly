"""Reviewed import names, not a search of the controller's installed packages.

Sources checked 2026-10-16. Each project-owned page documents install/import;
aliases remain case sensitive. The stdlib snapshot is CPython 3.12.10's
sys.stdlib_module_names (all platforms), docs.python.org/3.12/library/sys.html.
"""
import json
from pathlib import Path

MAPPING_VERSION = '2026-10-16.1'
STDLIB_VERSION = 'CPython-3.12.10'
STDLIB = frozenset(json.loads(Path(__file__).with_name('stdlib_312.json').read_text('utf-8')))
PROJECTS = {
    'requests': (('requests',), 'https://requests.readthedocs.io/en/latest/user/quickstart/'),
    'httpx': (('httpx',), 'https://www.python-httpx.org/quickstart/'),
    'pandas': (('pandas',), 'https://pandas.pydata.org/docs/getting_started/install.html'),
    'numpy': (('numpy',), 'https://numpy.org/doc/stable/user/absolute_beginners.html'),
    'openpyxl': (('openpyxl',), 'https://openpyxl.readthedocs.io/en/stable/tutorial.html'),
    'Pillow': (('PIL',), 'https://pillow.readthedocs.io/en/stable/installation/basic-installation.html'),
    'beautifulsoup4': (('bs4',), 'https://www.crummy.com/software/BeautifulSoup/bs4/doc/'),
    'lxml': (('lxml',), 'https://lxml.de/tutorial.html'),
    'PyYAML': (('yaml',), 'https://pyyaml.org/wiki/PyYAMLDocumentation'),
    'python-dateutil': (('dateutil',), 'https://dateutil.readthedocs.io/en/stable/'),
    'pywin32': (('win32com', 'win32api', 'win32con', 'win32gui', 'win32event', 'win32process',
                 'win32file', 'win32security', 'win32clipboard', 'pywintypes', 'pythoncom'),
                'https://github.com/mhammond/pywin32'),
    'DrissionPage': (('DrissionPage',), 'https://www.drissionpage.cn/get_start/installation/'),
}
IMPORT_TO_PACKAGE = {module: package for package, (modules, _) in PROJECTS.items() for module in modules}
