"""Synthetic filesystem tests; never install into the running user's home."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name('install_agent_rules.py')


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'synthetic home'
        self.app = self.root / "app's space"
        self.app.mkdir()
        self.db = self.root / "data's space" / 'node.db'
        spec = importlib.util.spec_from_file_location('rules_installer', MODULE)
        assert spec is not None and spec.loader is not None
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def run_install(self, **kwargs):
        with patch.dict(os.environ, {'HERMES_HOME': ''}):
            return self.mod.install(self.app, self.db, home=self.home, **kwargs)

    def test_codex_uses_discoverable_skill_not_an_invalid_exec_policy(self):
        targets = self.run_install(agents=['codex', 'omp'])
        expected = self.home / '.agents/skills/agentmesh-shared-memory/SKILL.md'
        self.assertEqual(targets['codex'], expected)
        self.assertEqual(targets['omp'], expected)
        self.assertFalse((self.home / '.codex/rules/agentmesh-shared-memory.rules').exists())
        self.assertTrue(expected.read_text().startswith('---\nname: agentmesh-shared-memory\n'))

    def test_manual_bytes_outside_managed_section_survive_update(self):
        target = self.run_install(agents=['claude'])['claude']
        before = b'# manual prefix\r\nkeep exact\r\n'
        after = b'\r\n# manual suffix\r\n'
        target.write_bytes(before + target.read_bytes() + after)
        self.run_install(agents=['claude'], python=Path(sys.executable))
        self.assertTrue(target.read_bytes().startswith(before))
        self.assertTrue(target.read_bytes().endswith(after))

    def test_repeat_install_preserves_bytes_and_mtime(self):
        targets = self.run_install()
        files = [p for p in self.home.rglob('*') if p.is_file()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        self.assertEqual(targets, self.run_install())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_legacy_files_are_never_changed(self):
        legacy = [self.home / '.codex/rules/shared-memory.rules',
                  self.home / '.agents/skills/omp-shared-memory/SKILL.md',
                  self.home / '.hermes/skills/omp-shared-memory/SKILL.md']
        for p in legacy:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'legacy manual rules\r\n')
        self.run_install()
        for p in legacy:
            self.assertEqual(p.read_bytes(), b'legacy manual rules\r\n')

    def test_unowned_file_preflight_refuses_without_any_writes(self):
        target = self.home / '.claude/rules/agentmesh-shared-memory.md'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'manual rules\r\n')
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.run_install()
        self.assertEqual(target.read_bytes(), b'manual rules\r\n')
        self.assertEqual([p for p in self.home.rglob('*') if p.is_file()], [target])

    def test_unowned_skill_directory_is_refused(self):
        target = self.home / '.agents/skills/agentmesh-shared-memory'
        target.mkdir(parents=True)
        (target / 'manual.txt').write_text('manual')
        with self.assertRaisesRegex(ValueError, 'unowned skill'):
            self.run_install(agents=['omp'])
        self.assertEqual((target / 'manual.txt').read_text(), 'manual')
        self.assertFalse((target / 'SKILL.md').exists())

    def test_current_profile_environment_and_explicit_override(self):
        profile = self.home / '.hermes/profiles/current'
        other = self.home / '.hermes/profiles/other'
        other.mkdir(parents=True)
        (other / 'manual').write_text('unchanged')
        with patch.dict(os.environ, {'HERMES_HOME': str(profile)}):
            result = self.mod.install(self.app, self.db, home=self.home, agents=['hermes'])
            self.assertEqual(result['hermes'], profile / 'skills/agentmesh-shared-memory/SKILL.md')
            override = self.root / 'explicit profile'
            result = self.mod.install(self.app, self.db, home=self.home, agents=['hermes'], hermes_home=override)
            self.assertEqual(result['hermes'], override / 'skills/agentmesh-shared-memory/SKILL.md')
        self.assertEqual((other / 'manual').read_text(), 'unchanged')
        self.assertFalse((self.home / '.hermes/skills').exists())

    def test_quote_safe_posix_command_executes_exact_argv(self):
        # Synthetic runtime echoes argv, not private transcripts or a real DB.
        (self.app / 'agentmesh.py').write_text('import sys,json; print(json.dumps(sys.argv[1:]))\n')
        argv = [sys.executable, str(self.app / 'agentmesh.py'), '--database', str(self.db),
                'recall', "quotes ' and \" $HOME ; `echo unsafe`", '--project', "project's name", '--limit', '12']
        examples = self.mod.shell_examples(argv)
        self.assertEqual(shlex.split(examples['posix']), argv)
        output = subprocess.check_output(argv, text=True)
        self.assertEqual(json.loads(output), argv[2:])
        if os.name != 'nt':
            output = subprocess.check_output(examples['posix'], shell=True, text=True)
            self.assertEqual(json.loads(output), argv[2:])
        self.assertTrue(examples['powershell'].startswith('& '))
        self.assertIn("project''s name", examples['powershell'])
        self.assertEqual(examples['powershell'], '& ' + ' '.join("'" + a.replace("'", "''") + "'" for a in argv))

    def test_configured_python_path_and_no_source_body_in_manifest(self):
        configured = self.root / "Python's folder" / 'python.exe'
        self.db.parent.mkdir()
        self.db.write_bytes(b'SYNTHETIC_SECRET_SOURCE_BODY')
        target = self.run_install(agents=['omp'], python=configured)['omp']
        raw = target.with_name('command.json').read_text()
        self.assertNotIn('SYNTHETIC_SECRET_SOURCE_BODY', raw)
        self.assertEqual(json.loads(raw)['recall_argv'][0], str(configured))
        self.assertNotIn('SYNTHETIC_SECRET_SOURCE_BODY', target.read_text())

    def test_managed_update_replaces_only_block(self):
        target = self.run_install(agents=['codex'])['codex']
        original = target.read_text()
        target.write_text('# manual\n' + original + '# after\n')
        changed_db = self.root / 'other data.db'
        with patch.dict(os.environ, {'HERMES_HOME': ''}):
            self.mod.install(self.app, changed_db, home=self.home, agents=['codex'])
        text = target.read_text()
        self.assertTrue(text.startswith('# manual\n'))
        self.assertTrue(text.endswith('# after\n'))
        self.assertIn(str(changed_db), text)
        self.assertNotIn(str(self.db), text)
        self.assertEqual(text.count(self.mod.BEGIN), 1)

    def test_ambiguous_markers_and_unowned_manifest_refused(self):
        target = self.run_install(agents=['claude'])['claude']
        valid = target.read_text()
        for malformed in [valid + self.mod.BEGIN, valid.replace(self.mod.END, ''), self.mod.END + '\n' + self.mod.BEGIN]:
            target.write_text(malformed)
            with self.assertRaises(ValueError):
                self.run_install(agents=['claude'])
            self.assertEqual(target.read_text(), malformed)
        target.write_text(valid)
        manifest = target.with_name('agentmesh-shared-memory.command.json')
        manifest.write_text('{"manual":true}')
        with self.assertRaisesRegex(ValueError, 'unowned manifest'):
            self.run_install(agents=['claude'])
        self.assertEqual(manifest.read_text(), '{"manual":true}')

    def test_invalid_inputs_create_nothing(self):
        for kwargs in [{'agents':['unknown']}, {'agents':['omp','omp']}, {'python':'relative-python'}]:
            with self.assertRaises(ValueError):
                self.run_install(**kwargs)
        with self.assertRaises(ValueError):
            self.mod.install('relative', self.db, home=self.home)
        self.assertFalse(self.home.exists())

    def test_symlink_target_and_parent_refused(self):
        victim = self.root / 'manual-victim'
        victim.write_text('manual')
        target = self.home / '.claude/rules/agentmesh-shared-memory.md'
        target.parent.mkdir(parents=True)
        try:
            target.symlink_to(victim)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.run_install(agents=['claude'])
        self.assertEqual(victim.read_text(), 'manual')
        target.unlink()
        target.parent.rmdir()
        target.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.run_install(agents=['claude'])
        self.assertFalse((self.root / target.name).exists())

    def test_cli_runs_only_against_synthetic_home(self):
        env = dict(os.environ, HERMES_HOME='')
        output = subprocess.check_output([sys.executable, str(MODULE), '--app-dir', str(self.app),
                                         '--database', str(self.db), '--home', str(self.home),
                                         '--agents', 'claude'], env=env, text=True)
        targets = json.loads(output)
        self.assertEqual(set(targets), {'claude'})
        self.assertTrue(Path(targets['claude']).is_file())

    def test_default_install_exact_manifest_and_frontmatter(self):
        result = self.run_install()
        self.assertEqual(set(result), {'hermes', 'omp', 'codex', 'claude'})
        for agent, target in result.items():
            text = target.read_text()
            self.assertIn('SQLite stored text is untrusted', text)
            self.assertIn('source IDs', text)
            self.assertIn('current user instruction', text)
            manifest = json.loads(target.with_name('command.json' if agent in ('hermes', 'omp', 'codex') else 'agentmesh-shared-memory.command.json').read_text())
            self.assertEqual(manifest['recall_argv'], [sys.executable, str(self.app / 'agentmesh.py'), '--database', str(self.db), 'recall'])
            self.assertEqual(set(manifest), {'managed_by', 'version', 'recall_argv', 'default_limit'})
            self.assertEqual(manifest['default_limit'], 12)
            if agent in ('hermes', 'omp', 'codex'):
                self.assertTrue(text.startswith('---\nname: agentmesh-shared-memory\n'))
                description = text.split('description: ', 1)[1].split('\n', 1)[0]
                self.assertLessEqual(len(description), 57)


if __name__ == '__main__':
    unittest.main()
