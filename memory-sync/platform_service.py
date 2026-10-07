"""Generate user-service files only; never register or start services."""
from pathlib import Path
import plistlib
import json
import math
import unicodedata


def _validate(python, app_dir, database, exchange, interval):
    for value in [python, app_dir, database, exchange]:
        value = str(value)
        if not Path(value).is_absolute() or any(unicodedata.category(c) == 'Cc' for c in value):
            raise ValueError('paths must be absolute and contain no control characters')
    if not math.isfinite(float(interval)) or float(interval) <= 0:
        raise ValueError('interval must be finite and positive')


def render_systemd(python, app_dir, database, exchange, interval=60):
    _validate(python, app_dir, database, exchange, interval)
    args = [str(python), str(Path(app_dir) / 'sync_worker.py'), str(database), str(exchange), '--interval', str(interval)]
    command = ' '.join(json.dumps(arg, ensure_ascii=False).replace('%', '%%') for arg in args)
    return ('[Unit]\nDescription=AgentMesh memory sync\n\n[Service]\n'
            'Type=simple\nExecStart=:' + command + '\nRestart=always\nRestartSec=10\n'
            'UMask=0077\n\n[Install]\nWantedBy=default.target\n')


def render_launchagent(python, app_dir, database, exchange, interval=60, postgres_dsn=None):
    _validate(python, app_dir, database, exchange, interval)
    logs = Path(database).parent
    config = {
        'Label': 'org.agentmesh.sync',
        'ProgramArguments': [str(python), str(Path(app_dir) / 'sync_worker.py'), str(database), str(exchange), '--interval', str(interval)],
        'RunAtLoad': True, 'KeepAlive': True, 'Umask': 0o077,
        'StandardOutPath': str(logs / 'agentmesh.stdout.log'),
        'StandardErrorPath': str(logs / 'agentmesh.stderr.log'),
    }
    if postgres_dsn:
        config['ProgramArguments'].append('--postgres')
        config['EnvironmentVariables'] = {'OMP_MEMORY_DSN': postgres_dsn}
    return plistlib.dumps(config)


def main(argv=None):
    import argparse
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['generate'])
    parser.add_argument('--platform', required=True, choices=['macos', 'linux'])
    for name in ['python', 'app-dir', 'database', 'exchange', 'output']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--postgres', action='store_true', help='macOS only: read OMP_MEMORY_DSN from generation environment')
    args = parser.parse_args(argv)
    dsn = os.environ.get('OMP_MEMORY_DSN') if args.postgres else None
    if args.postgres and (args.platform != 'macos' or not dsn):
        parser.error('--postgres requires macos and OMP_MEMORY_DSN')
    try:
        values = (args.python, args.app_dir, args.database, args.exchange, args.interval)
        data = render_launchagent(*values, postgres_dsn=dsn) if args.platform == 'macos' else render_systemd(*values).encode('utf-8')
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation avoids overwriting existing services or following symlinks.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    except (ValueError, OSError):
        parser.error('cannot generate service: invalid arguments or output already exists/unwritable')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
