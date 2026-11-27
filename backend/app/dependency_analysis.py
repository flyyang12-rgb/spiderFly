"""Bounded, static analysis. This module never imports or runs uploaded code."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from functools import lru_cache
from datetime import datetime, timezone

if __package__:
    from .dependency_catalog import IMPORT_TO_PACKAGE, MAPPING_VERSION, STDLIB, STDLIB_VERSION
else:  # A platform-owned worker launched with -I, never the uploaded script.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dependency_catalog import IMPORT_TO_PACKAGE, MAPPING_VERSION, STDLIB, STDLIB_VERSION

PARSER_VERSION = 'ast-3.12-v1'
MAX_SOURCE = 2 * 1024 * 1024
MAX_NODES = 100_000
MAX_ITEMS = 300
ANALYSIS_TIMEOUT = 5
_NAME = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?'
_REQUIREMENT = re.compile(rf'({_NAME}(?:\[{_NAME}(?:\s*,\s*{_NAME})*\])?)\s*((?:(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9*+!._-]+\s*(?:,\s*(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9*+!._-]+\s*)*)?)')

def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def canonical(value: str) -> str:
    return re.sub(r'[-_.]+', '-', value).lower()

def parse_requirements(value: str) -> dict[str, str]:
    if not isinstance(value, str) or len(value) > 20_000:
        raise ValueError('依赖清单不能超过 20,000 个字符')
    result = {}
    for number, raw in enumerate(value.lstrip('\ufeff').replace('\r\n', '\n').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = _REQUIREMENT.fullmatch(line)
        if not match:
            raise ValueError(f'依赖第 {number} 行：只支持 PyPI 包名和版本约束，不支持 URL、路径、参数或环境标记')
        name, spec = match.groups()
        key = canonical(name.split('[',1)[0])
        if key in {'spiderfly-instructions', 'spiderfly-runtime'}:
            raise ValueError('指令包已移除，请使用普通 Python 依赖')
        # packaging validates PEP 440 versions, including contradictory same-line pins.
        from packaging.specifiers import SpecifierSet, InvalidSpecifier
        try:
            specs = SpecifierSet(spec)
        except InvalidSpecifier:
            raise ValueError(f'依赖第 {number} 行：版本约束无效') from None
        pins = {s.version for s in specs if s.operator in {'==', '==='} and '*' not in s.version}
        if any(s.operator == '===' and '*' in s.version for s in specs):
            raise ValueError(f'依赖第 {number} 行：版本约束无效')
        if len(pins) > 1 or any(not specs.contains(pin, prereleases=True) for pin in pins):
            raise ValueError(f'依赖第 {number} 行：{name} 的版本约束冲突')
        from packaging.version import Version, InvalidVersion
        try:
            lowers = [(Version(s.version), s.operator == '>=') for s in specs if s.operator in {'>','>='}]
            uppers = [(Version(s.version), s.operator == '<=') for s in specs if s.operator in {'<','<='}]
            for low, low_inclusive in lowers:
                for high, high_inclusive in uppers:
                    if low > high or low == high and not (low_inclusive and high_inclusive):
                        raise ValueError(f'依赖第 {number} 行：{name} 的版本范围冲突')
        except InvalidVersion:
            raise ValueError(f'依赖第 {number} 行：版本约束无效') from None
        normalized = name + str(specs)
        if key in result and canonical(result[key]) != canonical(normalized):
            raise ValueError(f'依赖第 {number} 行：{name} 重复且约束冲突，请合并为一行')
        result.setdefault(key, normalized)
    return result

def _label(node):
    return ast.unparse(node)[:160]

def local_import(module, filename, modules):
    package = list(Path(filename).parts[:-1])
    if module.startswith('.'):
        level = len(module) - len(module.lstrip('.'))
        if level > len(package):
            return False
        prefix = package[:len(package) - level + 1]
        candidate = '.'.join(prefix + [module.lstrip('.')]).strip('.')
        return candidate in modules
    return module in modules or '.'.join(package + [module]) in modules


def analyze_source(source: str, filename: str, requirements: str = '', decisions=None, target=None, local_modules=None) -> dict:
    started = time.monotonic()
    if not isinstance(source, str) or not source or len(source.encode('utf-8')) > MAX_SOURCE:
        raise ValueError('Python 文件不能为空且不能超过 2MB')
    if '\x00' in source:
        raise ValueError('Python 文件含有空字符')
    try:
        tree = ast.parse(source, filename='<uploaded.py>', feature_version=(3, 12))
        # Compile an AST only to catch return/await outside functions; NEVER eval/exec.
        compile(tree, '<uploaded.py>', 'exec', dont_inherit=True)
    except SyntaxError as exc:
        raise ValueError(f'Python 语法错误：第 {exc.lineno or 1} 行，{exc.msg}') from None
    except (RecursionError, MemoryError, ValueError):
        raise ValueError('Python 语法结构过于复杂或无效，请拆简后上传') from None
    nodes = 0
    for _ in ast.walk(tree):
        nodes += 1
        if nodes > MAX_NODES or time.monotonic() - started > ANALYSIS_TIMEOUT - 1:
            raise ValueError('Python 分析超出资源限制，请拆简后上传')
    user = parse_requirements(requirements)
    final = dict(user)
    items = {}
    typing_names = set()
    typing_modules = set()
    dynamic_functions = {'__import__'}
    dynamic_modules = {'importlib'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == 'typing': typing_modules.add(name.asname or name.name)
                if name.name == 'importlib': dynamic_modules.add(name.asname or name.name)
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if node.module == 'typing' and name.name == 'TYPE_CHECKING': typing_names.add(name.asname or name.name)
                if node.module == 'importlib' and name.name == 'import_module': dynamic_functions.add(name.asname or name.name)
    shadowed = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    shadowed.update(n.arg for n in ast.walk(tree) if isinstance(n, ast.arg))
    typing_names -= shadowed
    typing_modules -= shadowed

    def is_type(node):
        return (isinstance(node, ast.Name) and node.id in typing_names or
                isinstance(node, ast.Attribute) and node.attr == 'TYPE_CHECKING' and
                isinstance(node.value, ast.Name) and node.value.id in typing_modules)

    def add(module, line, context, reason=''):
        root = module.split('.')[0] if not module.startswith('.') else module
        kind = 'type_only' if context == 'type_only' else 'stdlib' if root in STDLIB else context or 'import'
        package = IMPORT_TO_PACKAGE.get(root, '')
        if local_modules is not None and local_import(module, filename, local_modules):
            kind, reason = 'local', '包内模块'
        elif root == Path(filename).stem and kind not in {'type_only'}:
            kind, reason = 'self', '与脚本同名；单文件上传不包含其他业务模块'
        elif root.startswith('.') and kind != 'type_only':
            kind, reason = 'relative', '相对导入；单文件上传不包含其他业务模块'
        elif kind == 'import' and not package:
            kind, reason = 'unknown', '无法确认模块对应的安装包'
        key = f'{kind}:{root}'
        item = items.setdefault(key, dict(id=key, module=root, kind=kind, package=package, lines=[], reason=reason))
        if line not in item['lines']: item['lines'].append(line)
        if len(items) > MAX_ITEMS: raise ValueError('导入项超过 300 项，请拆简后上传')

    def walk(node, context=''):
        if isinstance(node, ast.If):
            # Main guards do not make imports optional; business entry is still runtime.
            main_guard = (isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
                          and node.test.left.id == '__name__')
            inner = 'type_only' if is_type(node.test) else context or ('' if main_guard else 'conditional')
            for child in node.body: walk(child, inner)
            for child in node.orelse: walk(child, context or 'conditional')
            return
        if isinstance(node, (ast.Try, ast.TryStar)):
            optional = any(any(isinstance(n, ast.Name) and n.id in {'ImportError','ModuleNotFoundError'}
                               for n in ast.walk(h.type)) for h in node.handlers if h.type)
            inner = context or ('conditional' if optional else '')
            for child in ast.iter_child_nodes(node): walk(child, inner)
            return
        if isinstance(node, ast.Import):
            for name in node.names: add(name.name, node.lineno, context)
        elif isinstance(node, ast.ImportFrom):
            add('.' * node.level + (node.module or ''), node.lineno, context)
        elif isinstance(node, ast.Call):
            dynamic = (isinstance(node.func, ast.Name) and node.func.id in dynamic_functions or
                       isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module' and
                       isinstance(node.func.value, ast.Name) and node.func.value.id in dynamic_modules)
            if dynamic:
                name = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg in {'name'}), None)
                module = name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else f'<动态导入@{node.lineno}>'
                add(module, node.lineno, 'type_only' if context == 'type_only' else 'dynamic', '动态导入，需核对实际用途')
            elif isinstance(node.func, ast.Name) and node.func.id in {'exec', 'eval'}:
                add(f'<动态代码@{node.lineno}>', node.lineno, 'type_only' if context == 'type_only' else 'dynamic', '动态代码无法静态确认依赖')
        for child in ast.iter_child_nodes(node): walk(child, context)

    try: walk(tree)
    except RecursionError: raise ValueError('Python 语法结构过于复杂，请拆简后上传') from None
    final, exclusions, unresolved, decisions = resolve_items(items, user, decisions)
    manifest = '\n'.join(final[key] for key in sorted(final))
    if len(manifest) > 20_000: raise ValueError('合并后的依赖清单超过 20,000 个字符，请精简后保存')
    return dict(source_hash=digest(source), user_requirements_hash=digest(requirements),
                filename=filename, target=target or {}, parser_version=PARSER_VERSION,
                mapping_version=MAPPING_VERSION, stdlib_version=STDLIB_VERSION,
                items=list(items.values()), unresolved=unresolved, exclusions=exclusions,
                decisions=decisions, user_requirements=requirements, requirements=manifest,
                requirements_hash=digest(manifest), ready=not unresolved)

def resolve_items(items, user, decisions):
    final = dict(user)
    decisions = decisions or []
    if not isinstance(decisions, list) or len(decisions) > MAX_ITEMS or len(json.dumps(decisions)) > 60_000:
        raise ValueError('依赖处理记录无效或过长')
    choices = {}
    for d in decisions:
        if not isinstance(d, dict) or d.get('id') in choices or d.get('id') not in items:
            raise ValueError('依赖处理项已过期，请重新分析')
        choices[d['id']] = d
    unresolved, exclusions = [], []
    for item in items.values():
        kind, package = item['kind'], item['package']
        item['lines'].sort()
        if kind in {'stdlib', 'type_only', 'local'}:
            item['status'] = 'excluded'
            if item['id'] in choices: raise ValueError('标准库和纯类型导入无需处理')
        elif kind == 'import':
            final.setdefault(canonical(package), package)
            item['status'] = 'automatic'
            if item['id'] in choices: raise ValueError('已验证依赖无需逐项处理，请通过补充清单指定版本')
        else:
            choice = choices.get(item['id'])
            if not choice and canonical(item['module']) in user:
                choice = {'action': 'package', 'requirement': user[canonical(item['module'])]}
            if choice and choice.get('action') == 'exclude':
                reason = str(choice.get('reason', '')).strip()
                if not reason or len(reason) > 500: raise ValueError('排除依赖需填写理由，最多 500 字')
                exclusions.append({'id':item['id'], 'reason':reason})
                item.update(status='excluded', exclusion_reason=reason)
            elif choice and choice.get('action') == 'package':
                declared = parse_requirements(str(choice.get('requirement', '')))
                if len(declared) != 1: raise ValueError('每个待补充项请填写一个正确包名或约束')
                key, value = next(iter(declared.items()))
                if key in user and canonical(value) != key and canonical(user[key]) != canonical(value):
                    raise ValueError(f'{value} 与用户清单约束冲突')
                # Explicit user constraints win over an unpinned resolution.
                if key in final and key not in user and canonical(final[key]) != key and canonical(final[key]) != canonical(value):
                    raise ValueError(f'{value} 与另一处理项约束冲突')
                final[key] = user.get(key, value)
                item.update(status='resolved', requirement=final[key])
            elif choice: raise ValueError('依赖处理动作无效')
            else:
                item['status'] = 'unresolved'
                unresolved.append(item['id'])
    return final, exclusions, unresolved, decisions

def analyze_project_sources(sources, filename, requirements='', decisions=None, target=None):
    if not isinstance(sources, dict) or len(sources) > 2000 or sum(len(s.encode('utf8')) for s in sources.values()) > 4 * MAX_SOURCE:
        raise ValueError('项目 Python 源码过多')
    # Package files are inspected statically. No uploaded code is imported.
    modules = set()
    for path in sources:
        parts = Path(path).with_suffix('').parts
        modules.add('.'.join(parts))
        for count in range(1, len(parts)):
            modules.add('.'.join(parts[:count]))
    items = {}
    for path, source in sources.items():
        result = analyze_source(source, path, requirements, local_modules=modules)
        for item in result['items']:
            previous = items.setdefault(item['id'], {**item, 'files': [], 'lines': []})
            previous['files'].append(path)
            previous['lines'] = sorted(set(previous['lines'] + item['lines']))
    final, exclusions, unresolved, decisions = resolve_items(items, parse_requirements(requirements), decisions)
    manifest = '\n'.join(final[key] for key in sorted(final))
    if len(manifest) > 20_000:
        raise ValueError('合并后的依赖清单超过 20,000 个字符')
    return dict(filename=filename, source_hash=digest(json.dumps(sources, sort_keys=True)),
                user_requirements=requirements, user_requirements_hash=digest(requirements),
                requirements=manifest, requirements_hash=digest(manifest), target=target or {},
                parser_version=PARSER_VERSION, mapping_version=MAPPING_VERSION, stdlib_version=STDLIB_VERSION,
                items=list(items.values()), unresolved=unresolved, exclusions=exclusions,
                decisions=decisions, ready=not unresolved)


def analyze(source, filename, requirements='', decisions=None, target=None, sources=None):
    """Hard wall-time bound includes parsing; uploaded source is stdin JSON only."""
    if len(source.encode('utf-8')) > MAX_SOURCE: raise ValueError('Python 文件不能超过 2MB')
    env = {key:value for key,value in os.environ.items() if not key.upper().startswith(
        ('SPIDERFLY_', 'DEEPSEEK_', 'OPENAI_', 'FEISHU_', 'LARK_')) and key.upper() not in {'PYTHONPATH','PYTHONHOME'}}
    try:
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve()), '--worker'],
            input=json.dumps(dict(source=source, filename=filename, requirements=requirements, decisions=decisions, target=target, **({'sources': sources} if sources is not None else {}))),
            capture_output=True, encoding='utf-8', timeout=ANALYSIS_TIMEOUT, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired:
        raise ValueError('依赖分析超时，请拆简脚本后重试') from None
    try: payload = json.loads(result.stdout)
    except (ValueError, TypeError): raise ValueError('依赖分析未完成，请重试或检查脚本结构') from None
    if 'error' in payload: raise ValueError(payload['error'])
    return automatic_preparation(payload['result'])

def automatic_preparation(analysis):
    """Resolve installer choices without executing code or retrying business runs."""
    if __package__:
        from .dependency_catalog import AUTOMATIC_IMPORT_TO_PACKAGE, AUTOMATIC_MAPPING_VERSION, PACKAGE_FAMILIES
    else:
        from dependency_catalog import AUTOMATIC_IMPORT_TO_PACKAGE, AUTOMATIC_MAPPING_VERSION, PACKAGE_FAMILIES
    final = parse_requirements(analysis['requirements'])
    pending = set(analysis['unresolved'])
    blocked, deferred = [], []
    for item in analysis['items']:
        if item['id'] not in pending:
            continue
        module = item['module']
        if item['kind'] == 'self':
            item.update(status='excluded', exclusion_reason='入口文件自身模块')
            continue
        if item['kind'] == 'dynamic' and module.startswith('<'):
            item.update(status='deferred', reason='动态模块由程序运行时确定')
            deferred.append(item['id'])
            continue
        if item['kind'] == 'relative' or not module.isascii():
            item.update(status='missing', reason='未找到随程序提供的业务模块')
            blocked.append(item['id'])
            continue
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', module):
            # Variable import names and exec are runtime behavior, not package names.
            # Keep this fact in the analysis; never run the uploaded script to guess.
            item.update(status='deferred', reason='动态模块由程序运行时确定')
            deferred.append(item['id'])
            continue
        family = PACKAGE_FAMILIES.get(module, ())
        selected = next((final[canonical(package)] for package in family if canonical(package) in final), None)
        package = AUTOMATIC_IMPORT_TO_PACKAGE.get(module, module)
        requirement = selected or final.get(canonical(package), package)
        final.update(parse_requirements(requirement))
        item.update(status='automatic', package=package, requirement=requirement,
                    resolution='catalog' if module in AUTOMATIC_IMPORT_TO_PACKAGE else 'package-name')
    # PaddleOCR needs a separate inference engine, as documented by the project.
    if any(item['module'] == 'paddleocr' and item['status'] not in ('excluded', 'missing') for item in analysis['items']):
        if not any(canonical(name) in final for name in ('paddlepaddle', 'paddlepaddle-gpu')):
            final.setdefault('paddlepaddle', 'paddlepaddle')
        # PaddleX OCR installs the contrib flavor; avoid two distributions owning cv2.
        declared = parse_requirements(analysis.get('user_requirements', ''))
        if not any(canonical(name) in declared for name in PACKAGE_FAMILIES['cv2']):
            final.pop('opencv-python', None)
            final.setdefault('opencv-contrib-python', 'opencv-contrib-python')
            for item in analysis['items']:
                if item['module'] == 'cv2' and item['status'] == 'automatic':
                    item.update(package='opencv-contrib-python', requirement='opencv-contrib-python')
    manifest = '\n'.join(final[key] for key in sorted(final))
    if len(manifest) > 20_000:
        raise ValueError('自动生成的依赖清单超过 20,000 个字符')
    analysis.update(requirements=manifest, requirements_hash=digest(manifest),
                    unresolved=blocked, deferred=deferred, ready=not blocked,
                    automatic=True, automatic_mapping_version=AUTOMATIC_MAPPING_VERSION)
    return analysis


@lru_cache(maxsize=4)
def local_python_info(python):
    try:
        r = subprocess.run([str(python), '-I', '-c', 'import sys,struct,json;print(json.dumps({"version":sys.version.split()[0],"bits":struct.calcsize("P")*8}))'],
                           capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        info = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError('无法检查主控 Python，请检查基础解释器配置') from None
    if not info['version'].startswith('3.12.') or info['bits'] != 64:
        raise ValueError('自动依赖准备仅支持 64 位 Python 3.12')
    return info

def target_context(target_host_id=None):
    if target_host_id is not None:
        return {'kind':'agent', 'host_id':int(target_host_id), 'supported_python':'3.12', 'python_verified':False}
    from .config import BASE_PYTHON
    return {'kind':'controller', 'host_id':None, 'python_verified':True, **local_python_info(str(BASE_PYTHON))}

def require_resolved(analysis):
    if not analysis['ready']:
        names = ', '.join(item['module'] for item in analysis['items'] if item['id'] in analysis['unresolved'])
        raise ValueError(('项目缺少模块：' if analysis.get('automatic') else '依赖待补充：') + names[:500] + ('。请随完整项目提供相关文件。' if analysis.get('automatic') else '。请填写包名或排除理由'))

def freeze(conn, version_id, analysis):
    conn.execute('INSERT OR IGNORE INTO dependency_analyses(version_id,analysis,created_at) VALUES(?,?,?)',
                 (version_id,json.dumps(analysis,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')))

if __name__ == '__main__':
    try:
        payload = json.loads(sys.stdin.read(12 * MAX_SOURCE))
        if 'sources' in payload:
            payload.pop('source', None)
            answer = {'result': analyze_project_sources(**payload)}
        else:
            answer = {'result': analyze_source(**payload)}
    except (ValueError, TypeError, RecursionError, MemoryError) as exc:
        answer = {'error':str(exc)[:1000]}
    print(json.dumps(answer,ensure_ascii=False))
