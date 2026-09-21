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
from .identity import load_identity, public_key, save_json
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
    run = commands.add_parser("run", help="连接主控，默认非调度")
    run.add_argument("--dispatch", action="store_true", help="允许接收任务（主控也必须开启调度）")
    run.add_argument("--desktop", action="store_true", help="启用 Windows 托盘和运行状态条")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("正式 Agent 只支持已登录的 Windows 用户会话")
    directory = args.data_dir.resolve()
    with SingleInstance(directory):
        machine_id, key = load_identity(directory)
        config_path = directory / "config.json"
        if args.command == "setup":
            if config_path.exists():
                previous = json.loads(config_path.read_text(encoding="utf-8"))
                if previous["server"] != args.server.rstrip("/"):
                    parser.error("已注册身份不能直接切换主控；请使用新的 --data-dir 申请独立机器身份")
            api = Api(args.server, key)
            result = api.request("POST", "/api/agent/register", {
                "enrollment_code": args.code, "machine_id": machine_id,
                "name": args.name, "public_key": public_key(key), "agent_version": __version__,
                "protocol_version": PROTOCOL_VERSION,
            }, signed=False)
            if result.get("protocol_version") != PROTOCOL_VERSION:
                raise RuntimeError(
                    f"主控协议版本不兼容：Agent 使用 v{PROTOCOL_VERSION}，"
                    f"主控返回 v{result.get('protocol_version')}"
                )
            save_json(config_path, {"server": api.server, "host_id": result["host_id"], "name": args.name})
            print(f"注册成功，宿主机 #{result['host_id']}，状态 {result['approval_status']}。请在网页批准后运行 Agent。")
            return
        if not config_path.exists():
            parser.error("尚未注册，请先执行 setup")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        journal = Journal(directory / "journal.sqlite3")
        pid_path = directory / "agent.pid.json"
        shutdown_path = directory / "shutdown.request"
        shutdown_path.unlink(missing_ok=True)
        save_json(pid_path, {
            "pid": os.getpid(), "version": __version__, "protocol_version": PROTOCOL_VERSION,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        client = Client(Api(config["server"], key, config["host_id"]), journal, directory,
                        dispatch=args.dispatch, shutdown_file=shutdown_path)
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
