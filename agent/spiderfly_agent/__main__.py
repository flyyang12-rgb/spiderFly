import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
from datetime import datetime, timezone

from . import PROTOCOL_VERSION, __version__
from .api import Api
from .client import Client
from .identity import load_identity, save_json
from .journal import Journal
from .windows import SingleInstance


def main():
    parser = argparse.ArgumentParser(description="SpiderFly Windows 宿主机 Agent")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ".")) / "SpiderFlyAgent")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="使用主控接入码申请注册")
    setup.add_argument("--server", required=True)
    setup.add_argument("--code", required=True)
    setup.add_argument("--name", default=socket.gethostname())
    commands.add_parser('registration-status', help='只读核对主控上的当前登记')
    run = commands.add_parser("run", help="连接主控，默认非调度")
    run.add_argument("--dispatch", action="store_true", help="允许接收任务（主控也必须开启调度）")
    run.add_argument("--desktop", action="store_true", help="启用 Windows 托盘和运行状态条")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("正式 Agent 只支持已登录的 Windows 用户会话")
    directory = args.data_dir.resolve()
    if args.command == 'registration-status':
        from .enrollment import registration_status
        # Machine-readable JSON crosses Windows PowerShell 5.1 code pages.
        # ASCII escapes keep names intact regardless of Python console encoding.
        print(json.dumps(registration_status(directory), ensure_ascii=True))
        return
    with SingleInstance(directory):
        config_path = directory / "config.json"
        if args.command == "setup":
            from .enrollment import apply
            result = apply(directory, args.server, args.code, args.name)
            print(f"注册成功，宿主机 #{result['host_id']}，状态 {result['approval_status']}。请在网页批准后运行 Agent。")
            return
        machine_id, key = load_identity(directory)
        if not config_path.exists():
            parser.error("尚未注册，请先执行 setup")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        from .updater import RemoteUpdater
        api = Api(config['server'], key, config['host_id'])
        updater = RemoteUpdater(api, directory, machine_id)
        journal = Journal(directory / "journal.sqlite3")
        pid_path = directory / "agent.pid.json"
        shutdown_path = directory / "shutdown.request"
        shutdown_path.unlink(missing_ok=True)
        save_json(pid_path, {
            "pid": os.getpid(), "version": __version__, "protocol_version": PROTOCOL_VERSION,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        client = Client(api, journal, directory, dispatch=args.dispatch,
                        shutdown_file=shutdown_path, updater=updater)
        signal.signal(signal.SIGINT, lambda *_: client.shutdown.set())
        signal.signal(signal.SIGTERM, lambda *_: client.shutdown.set())
        try:
            print("调度已开启，请保持当前 Windows 会话登录且未锁屏。" if args.dispatch else "非调度模式：仅连接和回传状态，不领取任务。")
            if args.desktop:
                from .desktop import DesktopApp
                DesktopApp(client).run()
            else:
                client.run()
        finally:
            journal.close()
            pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Agent 启动或运行失败: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
