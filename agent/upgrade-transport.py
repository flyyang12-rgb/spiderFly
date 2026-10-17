"""Short-lived signed upgrade requests; never keeps DLLs loaded during pip."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('inspect', 'pause', 'resume'))
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--token')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not (root / 'config.json').is_file() or not (root / 'identity.json').is_file():
        raise ValueError('请选择已接入的 Agent 数据目录')
    if args.action == 'inspect':
        count = 0
        journal = root / 'journal.sqlite3'
        if journal.exists():
            with sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True, timeout=3) as conn:
                count = conn.execute("SELECT COUNT(*) FROM runs WHERE state!='acked'").fetchone()[0]
        print(json.dumps({'unacknowledged': count}))
        return
    from spiderfly_agent.api import Api, ApiError
    from spiderfly_agent.identity import load_identity
    config = json.loads((root / 'config.json').read_text('utf-8'))
    _, key = load_identity(root)
    api = Api(config['server'], key, config['host_id'])
    try:
        try:
            value = api.request('POST', '/api/agent/upgrade-' + args.action, {'token': args.token})
        except ApiError as error:
            if error.status == 404:
                raise ValueError('请先更新主控，再使用一键升级') from None
            raise
        print(json.dumps(value))
    finally:
        api.session.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
