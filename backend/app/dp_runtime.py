"""Native Windows DP profile. Process separation is NOT a security sandbox."""
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .config import DATA_DIR, MANAGED_BROWSER_PORT
from . import maintenance_runtime as runtime

PROFILE = 'drissionpage-v1'
MARKER = '# spiderfly-runtime: ' + PROFILE
PACKAGES = {'drissionpage': '4.1.1.4'}
DRIVER = Path(__file__).resolve().parents[1] / 'native/dp_driver.py'


def uses_dp(source):
    return MARKER in source.splitlines()[:10]


def validate_source(source):
    """Catch accidental port/ownership mistakes; not a Python security filter."""
    import ast
    tree = ast.parse(source)
    attrs = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    if attrs & {'auto_port', 'set_local_port', 'set_user_data_path', 'quit', 'new_tab', 'set_browser_path'}:
        raise ValueError('DP 浏览器由平台管理；不要分配端口、指定资料目录或 quit')
    if not {'set_address', 'existing_only', 'headless'}.issubset(attrs) or 'SPIDERFLY_BROWSER_ADDRESS' not in source:
        raise ValueError('DP 使用 ChromiumOptions(read_file=False).set_address(os.environ["SPIDERFLY_BROWSER_ADDRESS"]).existing_only().headless() 接入本次浏览器')
    expected = ast.dump(ast.parse('os.environ["SPIDERFLY_BROWSER_ADDRESS"]', mode='eval').body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'Chromium', 'ChromiumPage'}:
            if node.func.id != 'Chromium' or len(node.args) != 1 or not isinstance(node.args[0], ast.Name) or node.keywords:
                raise ValueError('DP 通过 Chromium(co).latest_tab 连接，不直接传端口或使用默认配置')
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == 'set_address' and (len(node.args) != 1 or ast.dump(node.args[0]) != expected or node.keywords):
            raise ValueError('DP 地址必须直接读取 os.environ["SPIDERFLY_BROWSER_ADDRESS"]，不能写死端口')
        if node.func.attr in {'headless', 'existing_only'} and (node.args or node.keywords):
            raise ValueError('DP 使用无参数 existing_only().headless()，避免连接时重启浏览器')


def ready():
    if os.name != 'nt':
        raise ValueError('drissionpage-v1 需要 Windows 宿主机')
    try:
        version = importlib.metadata.version('DrissionPage')
    except importlib.metadata.PackageNotFoundError:
        version = ''
    if version != PACKAGES['drissionpage']:
        raise ValueError('DP 环境未准备：请在平台 Python 环境安装 backend/requirements-dp.txt')


async def execute(source, *, hosts, timeout=120, stop=None):
    from .host_runtime import check_host_busy
    from .runner import _runtime_environment, _terminate_process
    ready()
    busy = await asyncio.to_thread(check_host_busy, managed_browser_port=MANAGED_BROWSER_PORT)
    if busy.busy:
        raise ValueError(f'等待宿主机空闲：{busy.message}；没有接管浏览器')
    if stop and stop.is_set():
        raise InterruptedError('DP 运行已停止')
    root = Path(DATA_DIR) / 'dp_runs'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='run-', dir=root, ignore_cleanup_errors=True) as directory:
        work = Path(directory)
        (work / 'main.py').write_text(source, encoding='utf-8')
        env = _runtime_environment(work_dir=work)
        for key in list(env):
            if key.upper() in {'PYTHONPATH', 'PYTHONHOME'}:
                del env[key]
        env['SPIDERFLY_ARTIFACT_DIR'] = str(work / 'artifacts')
        env['SPIDERFLY_RESULT_FILE'] = str(work / 'result.json')
        env['SPIDERFLY_BROWSER_ADDRESS'] = f'127.0.0.1:{MANAGED_BROWSER_PORT}'
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-X', 'utf8', str(DRIVER), str(work), json.dumps(sorted(hosts)),
            env=env, cwd=work, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        async def drain(stream):
            data = bytearray()
            while chunk := await stream.read(65536):
                data.extend(chunk)
                if len(data) > runtime.MAX_OUTPUT:
                    raise ValueError('DP 输出超过限额')
            return bytes(data)
        async def collect():
            stdout, stderr = await asyncio.gather(drain(process.stdout), drain(process.stderr))
            await process.wait()
            if process.returncode:
                raise ValueError('DP 执行器失败：' + stderr.decode('utf-8', errors='replace')[-3000:])
            return runtime.decode_result(json.loads(stdout))
        running = asyncio.create_task(collect())
        halted = asyncio.create_task(stop.wait()) if stop else None
        try:
            done, _ = await asyncio.wait([running] + ([halted] if halted else []),
                                         timeout=max(1, min(120, timeout)), return_when=asyncio.FIRST_COMPLETED)
            if halted and halted in done:
                raise InterruptedError('DP 运行已停止')
            if running not in done:
                raise TimeoutError('DP 运行超过时间上限')
            return await running
        finally:
            await _terminate_process(process)
            if halted: halted.cancel()
            running.cancel()
            await asyncio.gather(running, *([halted] if halted else []), return_exceptions=True)
