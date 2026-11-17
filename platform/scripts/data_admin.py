#!/usr/bin/env python3
"""Online backup and explicit migration; existing targets are never overwritten."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['backup', 'migrate', 'verify'])
parser.add_argument('destination', type=Path, help='备份文件；迁移时为目标数据目录；校验时为数据库文件')
args = parser.parse_args()
if args.action == 'verify':
    with sqlite3.connect(args.destination.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        result = conn.execute('PRAGMA integrity_check').fetchone()[0]
    print(result)
    sys.exit(0 if result == 'ok' else 1)
if not (store.DATA / 'platform.sqlite3').is_file():
    parser.error(f'源数据库不存在：{store.DATA / "platform.sqlite3"}')
destination = args.destination / 'platform.sqlite3' if args.action == 'migrate' else args.destination
store.backup(destination)
print(f'已完成并校验：{destination.resolve()}')
if args.action == 'migrate':
    import shlex
    print('原数据库保留。停止旧服务后，用以下配置启动所有规划/地面站进程：')
    print('export SENSE_DATA_DIR=' + shlex.quote(str(args.destination.resolve())))
    print('地图瓦片缓存无需迁移，访问时会重新生成。请勿在两个目录同时写入。')
