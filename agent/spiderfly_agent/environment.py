"""Inspect a task venv without importing its business script or packages."""
import hashlib
import json
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPORT_VERSION = 1
MAX_REPORT_BYTES = 256 * 1024
MAX_PACKAGES = 1000
PROBE = r'''
import importlib.metadata as metadata,json,struct,sys
from pathlib import Path
packages = sorted([{'name': d.metadata['Name'], 'version': d.version}
                   for d in metadata.distributions()], key=lambda d: (d['name'] or '').lower())
facts = {'python': '.'.join(map(str,sys.version_info[:3])), 'bits': struct.calcsize('P')*8,
         'executable': sys.executable, 'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
         'packages': packages}
Path(sys.argv[1]).write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
'''


def requirements_hash(requirements):
    return hashlib.sha256(requirements.encode('utf-8')).hexdigest()


def bounded_report(report):
    if len(json.dumps(report, ensure_ascii=False).encode('utf-8')) > MAX_REPORT_BYTES:
        raise ValueError('环境记录超过 256 KiB，未回传包明细')
    return report


def verify_packages(packages, requirements):
    if not isinstance(packages, list) or len(packages) > MAX_PACKAGES:
        raise ValueError('实际依赖记录数量无效（最多 1000 项）')
    installed = {}
    for package in packages:
        name, version = package.get('name'), package.get('version')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name)
                or not isinstance(version, str) or len(version) > 128):
            raise ValueError('实际依赖名称或版本无效')
        key = canonicalize_name(name)
        if key in installed:
            raise ValueError(f'环境存在重复依赖元数据：{name}')
        installed[key] = Version(version)
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        requirement = Requirement(line)
        if requirement.url or requirement.marker:
            raise ValueError('业务依赖不支持 URL 或环境标记')
        version = installed.get(canonicalize_name(requirement.name))
        if version is None or not requirement.specifier.contains(version, prereleases=True):
            raise ValueError(f'实际依赖与冻结清单不一致：{requirement}')


def read_probe(path: Path, python: Path, cache: Path):
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError('环境记录超过 256 KiB')
    facts = json.loads(path.read_text('utf-8'))
    # Resolve the venv prefix as well as the binary: POSIX venv binaries may be
    # symlinks, whereas a copied system interpreter must never masquerade as a venv.
    if (Path(facts['executable']).absolute() != python.absolute()
            or Path(facts['prefix']).resolve() != cache.resolve()
            or Path(facts['base_prefix']).resolve() == cache.resolve()
            or not re.fullmatch(r'3\.12\.\d+', facts['python']) or facts['bits'] != 64):
        raise ValueError('业务环境必须是本次独立环境中的 64 位 Python 3.12')
    verify_packages(facts['packages'], '')
    return {key: facts[key] for key in ('python', 'bits', 'packages')}
