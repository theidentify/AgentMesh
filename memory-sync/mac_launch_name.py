"""Name venv LaunchAgents in macOS Login Items."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from xml.parsers.expat import ExpatError

NAME = 'AgentMesh'


def plan(agents_dir, revert=False):
    items = []
    for plist in sorted(Path(agents_dir).glob('org.agentmesh.*.plist')):
        try:
            with plist.open('rb') as stream: data = plistlib.load(stream)
        except (OSError, ValueError, TypeError, OverflowError, ExpatError):
            continue
        if not isinstance(data, dict) or 'Program' in data: continue
        argv = data.get('ProgramArguments')
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) for a in argv): continue
        old = Path(argv[0])
        if not old.is_absolute(): continue
        if revert:
            if old.name != NAME or not (old.parent/'python').exists(): continue
            new, link = old.parent/'python', None
        else:
            if not re.fullmatch(r'python(3(\.\d+)*)?', old.name): continue
            if not (old.parent.parent/'pyvenv.cfg').is_file(): continue
            new = link = old.parent/NAME
        items.append(dict(plist=plist, label=data.get('Label', plist.stem), old=argv[0], new=str(new), link=link))
    return items


def ensure_link(link, python):
    if not os.path.lexists(link):
        os.symlink(Path(python).name, link)
    elif not Path(link).is_symlink() or os.path.realpath(link) != os.path.realpath(python):
        raise ValueError(f'{link} exists and is not a link to {python}')


def rewrite(plist, new):
    with open(plist, 'rb') as stream: data = plistlib.load(stream)
    data['ProgramArguments'][0] = new
    mode = os.stat(plist).st_mode & 0o777
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=Path(plist).parent, delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), mode)
            plistlib.dump(data, stream)
        os.replace(temporary, plist)
    finally:
        if temporary is not None and os.path.lexists(temporary): os.unlink(temporary)


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def loaded(label, runner):
    return runner(['launchctl', 'print', f'gui/{os.getuid()}/{label}']).returncode == 0


def unloaded(label, runner, sleep=time.sleep, tries=50):
    """bootout returns before a KeepAlive service has exited; bootstrap fails until launchd lets go of it."""
    for _ in range(tries):
        if not loaded(label, runner): return True
        sleep(0.2)
    return False


def apply(items, runner=_run, reload=True, sleep=time.sleep):
    for item in items:
        if item['link'] is not None: ensure_link(item['link'], item['old'])
    active = {item['label'] for item in items if reload and loaded(item['label'], runner)}
    for item in items: rewrite(item['plist'], item['new'])
    domain = f'gui/{os.getuid()}'
    for item in sorted(items, key=lambda item: item['label'] == 'org.agentmesh.control'):
        label = item['label']
        if label not in active: continue
        runner(['launchctl', 'bootout', f'{domain}/{label}'])
        if not unloaded(label, runner, sleep): raise RuntimeError(f'{label} is still loaded after bootout')
        if runner(['launchctl', 'bootstrap', domain, str(item['plist'])]).returncode != 0:
            # The unit is booted out by now; say how to load it again.
            raise RuntimeError(f"bootstrap failed for {label}; retry: launchctl bootstrap {domain} {item['plist']}")
    return active


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', default=Path.home()/'Library'/'LaunchAgents')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--revert', action='store_true')
    parser.add_argument('--no-reload', action='store_true')
    args = parser.parse_args(argv)
    try:
        items = plan(args.agents, revert=args.revert)
        active = apply(items, reload=not args.no_reload) if args.apply else set()
        for item in items:
            marker = (' (reload)' if item['label'] in active else ' (file only)') if args.apply else ''
            print(f"{item['label']}: {item['old']} -> {item['new']}{marker}")
        if not items: print('nothing to change')
    except (ValueError, RuntimeError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
