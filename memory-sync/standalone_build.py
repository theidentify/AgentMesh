"""Build a self-contained AgentMesh CLI from a trusted source checkout.

This produces a local binary, not a signed OTA release or an installer.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


HIDDEN_MODULES = (
    'brand', 'recall_memory', 'ingest_sessions', 'workflow', 'summarize_memory',
    'sqlite_memory', 'memory_sync', 'sync_worker', 'bounded_digest', 'install_inspect', 'install_setup', 'security_wizard',
    'install_adopt', 'worker_lifecycle', 'worker_diagnostics', 'worker_error_details', 'mac_replace', 'windows_install', 'windows_task',
    'omp_memory.parser', 'omp_memory.codex_parser', 'omp_memory.claude_parser',
    'cryptography.hazmat.primitives.asymmetric.ed25519',
)


def build_command(source, dist, work):
    source = Path(source).resolve(strict=True)
    if not (source / 'agentmesh.py').is_file() or not (source / 'schema.sql').is_file() or not (source / 'src' / 'omp_memory').is_dir():
        raise ValueError('source must contain the AgentMesh CLI, schema and parsers')
    dist, work = Path(dist).resolve(), Path(work).resolve()
    if any(path == source or path.is_relative_to(source) for path in (dist, work)):
        raise ValueError('build output must be outside source')
    return [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
            '--name', 'agentmesh', '--distpath', str(dist), '--workpath', str(work),
            '--specpath', str(work), '--additional-hooks-dir', str(source / 'packaging-hooks'),
            '--paths', str(source), '--paths', str(source / 'src'),
            '--add-data', str(source / 'schema.sql') + os.pathsep + '.',
            *[part for module in HIDDEN_MODULES for part in ('--hidden-import', module)],
            str(source / 'agentmesh.py')]


def operator_launcher(dist):
    """Adjacent relocatable operator entry, never a production-config default."""
    path = Path(dist) / 'Replace-AgentMesh.command'
    path.write_text('''#!/bin/zsh
set -eu
here="${0:A:h}"
print 'AgentMesh local development trial | not signed/notarized for distribution'
print 'A private replacement manifest is required. Planning precedes REPLACE.'
if (( $# )); then
  manifest="$1"
else
  read 'manifest?Absolute path to private replacement manifest: '
fi
"$here/agentmesh" mac-replace --manifest "$manifest" --binary "$here/agentmesh"
print 'Press Return to close.'
read ignored
''', encoding='utf-8')
    path.chmod(0o700)
    return path


def windows_launcher(dist):
    """Package the reviewed click helper with native CMD line endings."""
    source = Path(__file__).with_name('Install-AgentMesh.cmd')
    target = Path(dist) / source.name
    target.write_bytes(source.read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-8'))
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True, help='output directory outside the source tree')
    parser.add_argument('--work', type=Path, required=True, help='private build directory outside the source tree')
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent
    subprocess.run(build_command(source, args.dist, args.work), check=True)
    if sys.platform == 'darwin':
        operator_launcher(args.dist)
    elif os.name == 'nt':
        windows_launcher(args.dist)
    print(args.dist.resolve() / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
