"""Validate signed Agent facts against immutable runs, never mutable tasks."""
import hashlib
import json
import re
from datetime import datetime

from fastapi import HTTPException
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

MAX_REPORT_BYTES = 256 * 1024


def validate_report(report, run):
    if not isinstance(report, dict) or len(json.dumps(report, ensure_ascii=False).encode()) > MAX_REPORT_BYTES:
        raise HTTPException(413, '业务环境记录超过 256 KiB')
    expected = hashlib.sha256(run['requirements'].encode('utf-8')).hexdigest()
    if report['package_hash'] != run['package_hash'] or report['requirements_hash'] != expected:
        raise HTTPException(409, '环境记录与本次冻结任务包不一致')
    try:
        timestamp = datetime.fromisoformat(report['verified_at'])
        if timestamp.tzinfo is None:
            raise ValueError('缺少时区')
        installed = {}
        for package in report['packages']:
            key = canonicalize_name(package['name'])
            if key in installed:
                raise ValueError('重复依赖元数据')
            installed[key] = Version(package['version'])
        if report['status'] == 'ready':
            if (not re.fullmatch(r'3\.12\.\d+', report['python'] or '') or report['bits'] != 64
                    or not all(report[key] for key in ('pip_check', 'interpreter_verified', 'requirements_verified'))
                    or report['stage'] != 'ready' or report['error']):
                raise ValueError('环境未完成全部校验')
            for line in run['requirements'].splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                requirement = Requirement(line)
                actual = installed.get(canonicalize_name(requirement.name))
                if (requirement.url or requirement.marker or actual is None
                        or not requirement.specifier.contains(actual, prereleases=True)):
                    raise ValueError(f'实际依赖与冻结清单不一致：{requirement}')
        elif report['stage'] == 'ready' or not report['error']:
            raise ValueError('失败记录缺少阶段或错误')
    except (ValueError, TypeError) as error:
        raise HTTPException(422, f'业务环境记录无效：{error}') from error
    return report
