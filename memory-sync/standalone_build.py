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
    'sqlite_memory', 'memory_sync', 'sync_worker', 'bounded_digest',
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True, help='output directory outside the source tree')
    parser.add_argument('--work', type=Path, required=True, help='private build directory outside the source tree')
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent
    subprocess.run(build_command(source, args.dist, args.work), check=True)
    print(args.dist.resolve() / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
