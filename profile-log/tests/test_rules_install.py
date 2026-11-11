"""`agentmesh-log rules install`: the broker-mode successor of memory-sync's install_agent_rules.py."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
import pytest
from agentmesh_log import sharedrules as rules


def setup(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    py = Path(sys.executable).resolve()
    key = tmp_path / 'keys' / 'recall.key'
    return home, py, key


def run(home, py, key, **kw):
    return rules.install(agent_key=key, profiles=kw.pop('profiles', ['work', 'personal']), python=py, home=home,
                         hermes_home=kw.pop('hermes_home', home / '.hermes'), **kw)


def test_targets_manifest_and_frontmatter(tmp_path):
    home, py, key = setup(tmp_path)
    written = run(home, py, key)
    assert written['claude'] == home / '.claude' / 'rules' / 'agentmesh-shared-memory.md'
    assert written['codex'] == written['omp'] == home / '.agents' / 'skills' / 'agentmesh-shared-memory' / 'SKILL.md'
    assert written['hermes'] == home / '.hermes' / 'skills' / 'agentmesh-shared-memory' / 'SKILL.md'
    manifest = json.loads((home / '.claude' / 'rules' / 'agentmesh-shared-memory.command.json').read_text())
    assert manifest['managed_by'] == rules.OWNER and manifest['default_limit'] == 12
    assert manifest['recall_argv'] == [str(py), '-m', 'agentmesh_log.cli', 'recall', '--compat', '--per-profile',
                                       '--profile', 'work', '--profile', 'personal', '--agent-key', str(key)]
    assert manifest['memory_argv']['grep'] == [str(py), '-m', 'agentmesh_log.cli', 'memory', 'grep', '--agent-key', str(key)]
    skill = written['codex'].read_text()
    assert skill.startswith('---\nname: agentmesh-shared-memory\n') and rules.BEGIN in skill
    claude = written['claude'].read_text()
    assert claude.startswith(rules.BEGIN) and 'Browse the memory tree' in claude and 'do not ingest, summarize, sync, or write memory' in claude


def test_repeat_keeps_bytes_and_mtime(tmp_path):
    home, py, key = setup(tmp_path)
    path = run(home, py, key, agents=['claude'])['claude']
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    run(home, py, key, agents=['claude'])
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_text_outside_block_survives(tmp_path):
    home, py, key = setup(tmp_path)
    path = run(home, py, key, agents=['claude'])['claude']
    path.write_text('mine before\n' + path.read_text() + 'mine after\n')
    run(home, py, key, agents=['claude'], profiles=['work'])
    text = path.read_text()
    assert text.startswith('mine before\n') and text.endswith('mine after\n') and "'--profile' 'personal'" not in text


def test_unowned_refused_before_writes(tmp_path):
    home, py, key = setup(tmp_path)
    claude = home / '.claude' / 'rules' / 'agentmesh-shared-memory.md'
    claude.parent.mkdir(parents=True); claude.write_text('hand-written\n')
    with pytest.raises(ValueError, match='unowned'): run(home, py, key)
    assert claude.read_text() == 'hand-written\n' and not (home / '.agents').exists() and not (home / '.hermes').exists()
    claude.unlink()
    skills = home / '.agents' / 'skills' / 'agentmesh-shared-memory'; skills.mkdir(parents=True)
    with pytest.raises(ValueError, match='skill directory'): run(home, py, key, agents=['codex'])
    run(home, py, key, agents=['claude'])
    manifest = home / '.claude' / 'rules' / 'agentmesh-shared-memory.command.json'
    manifest.write_text('{"managed_by": "someone else"}')
    with pytest.raises(ValueError, match='manifest'): run(home, py, key, agents=['claude'])
    path = home / '.claude' / 'rules' / 'agentmesh-shared-memory.md'
    manifest.unlink(); path.write_text(rules.BEGIN + rules.BEGIN + rules.END)
    with pytest.raises(ValueError, match='ambiguous'): run(home, py, key, agents=['claude'])


@pytest.mark.skipif(os.name == 'nt', reason='symlinks need privileges on Windows')
def test_symlinks_refused(tmp_path):
    home, py, key = setup(tmp_path)
    elsewhere = tmp_path / 'elsewhere'; elsewhere.mkdir()
    (home / '.claude').symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'): run(home, py, key, agents=['claude'])
    assert not any(elsewhere.iterdir())


def test_invalid_inputs(tmp_path):
    home, py, key = setup(tmp_path)
    for kw in (dict(profiles=[]), dict(profiles=['work', 'work']), dict(profiles=['a b']), dict(agents=['vim']),
               dict(agents=['claude', 'claude'])):
        with pytest.raises(ValueError): run(home, py, key, **kw)
    with pytest.raises(ValueError): rules.install(agent_key='relative.key', profiles=['work'], python=py, home=home)
    assert not any(home.iterdir())


def test_dry_run_writes_nothing(tmp_path):
    home, py, key = setup(tmp_path)
    assert run(home, py, key, dry_run=True)['claude'].name == 'agentmesh-shared-memory.md'
    assert not any(home.iterdir())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell example')
def test_posix_example_is_exact_argv(tmp_path):
    home, py, key = setup(tmp_path)
    text = run(home, py, key, agents=['claude'])['claude'].read_text()
    line = text.split('```sh\n', 1)[1].split('\n', 1)[0]
    assert shlex.split(line) == [str(py), '-m', 'agentmesh_log.cli', 'recall', '--compat', '--per-profile', '--profile', 'work',
                                 '--profile', 'personal', '--agent-key', str(key), 'QUERY', '--limit', '12']


def test_cli(tmp_path):
    home, py, key = setup(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    def cli(*extra):
        result = subprocess.run([sys.executable, '-m', 'agentmesh_log.cli', 'rules', 'install', '--agent-key', str(key),
                                 '--profile', 'work', '--python', str(py), '--home', str(home), '--hermes-home', str(home / 'h'), *extra],
                                capture_output=True, text=True, env=env)
        return json.loads(result.stdout)
    assert cli('--agents', 'claude')['rules'] == {'claude': str(home / '.claude' / 'rules' / 'agentmesh-shared-memory.md')}
    (home / '.claude' / 'rules' / 'agentmesh-shared-memory.md').write_text('not ours\n')
    refused = cli('--agents', 'claude')
    assert refused['error'] == 'refused' and 'unowned' in refused['reason']
