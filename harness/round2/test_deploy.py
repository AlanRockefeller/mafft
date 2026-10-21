"""Deployment failure/rollback tests use disposable fake installations only."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mafft-deploy-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.build = self.root / 'build'
        self.install = self.build / 'install'
        (self.install / 'bin').mkdir(parents=True)
        (self.install / 'libexec/mafft').mkdir(parents=True)
        self.launcher = self.install / 'bin/mafft'
        self.launcher.write_text('#!/bin/sh\nif [ "$1" = --version ]; then\n'
                                 ' printf "%s\\n" ' + deploy.shlex.quote(deploy.VERSION) + '\n'
                                 'elif [ "$1" = --show-helper ]; then\n'
                                 ' printf "%s\\n" "$MAFFT_BINARIES"\n'
                                 'elif [ "$1" = --show-name ]; then\n'
                                 ' printf "%s\\n" "${0##*/}"\n'
                                 'else\n for arg do last="$arg"; done\n cat "$last"\nfi\n')
        self.launcher.chmod(0o755)
        (self.install / 'bin/linsi').symlink_to('mafft')
        helper = self.install / 'libexec/mafft/mafft-profile'
        helper.write_text('#!/bin/sh\nprintf "profile\\n"\n')
        helper.chmod(0o755)
        (self.install / 'bin/mafft-profile').symlink_to(helper)
        for name, body in {
            'mafft-homologs.rb': 'printf "%s\\n" "$MAFFT_HOMOLOGS_MAFFT"\n',
            'mafft-sparsecore.rb': 'printf "%s\\n" "$@"\n',
        }.items():
            driver = self.install / 'bin' / name
            driver.write_text('#!/bin/sh\n' + body)
            driver.chmod(0o755)
        native = self.build / 'source/core/native.c'
        native.parent.mkdir(parents=True)
        native.write_text('/* disposable test source */\n')
        metadata = {'prefix': str(self.install), 'source_sha256': {'core/native.c': deploy.sha(native)}}
        (self.build / 'build.json').write_text(json.dumps(metadata))
        manifest = {p.relative_to(self.install).as_posix(): deploy.sha(p)
                    for p in self.install.rglob('*') if p.is_file()}
        (self.build / 'binary-sha256.json').write_text(json.dumps(manifest))
        self.data, self.golden = self.root / 'data', self.root / 'golden'
        self.data.mkdir(); self.golden.mkdir()
        for p in (self.data, self.golden):
            (p / 'tiny.fasta').write_text('>a\nACGT\n>b\nACGA\n')
        self.corpus = self.root / 'corpus.json'
        self.corpus.write_text(json.dumps({'files': [{'file': 'tiny.fasta',
            'sha256': deploy.sha(self.data / 'tiny.fasta'), 'production_options': ['--auto']}]}))
        self.gold_manifest = self.root / 'golden.sha256'
        self.gold_manifest.write_text(deploy.sha(self.golden / 'tiny.fasta') + '  old/location/tiny.fasta\n')
        self.entrypoint = self.root / 'commands/mafft'
        self.entrypoint.parent.mkdir()
        self.entrypoint.write_text('#!/bin/sh\nprintf "old-version\\n"\n')
        self.entrypoint.chmod(0o751)
        self.releases = self.root / 'releases'
        constants = {'BUILD_SHA': deploy.sha(self.build / 'build.json'),
            'INSTALL_SHA': deploy.sha(self.build / 'binary-sha256.json'),
            'CORPUS_SHA': deploy.sha(self.corpus), 'GOLDEN_SHA': deploy.sha(self.gold_manifest),
            'RELEASE_NAME': 'test-release', 'SMOKE_INPUTS': ('tiny.fasta',)}
        self.patches = mock.patch.multiple(deploy, **constants)
        self.patches.start(); self.addCleanup(self.patches.stop)
        self.command = ['deploy', '--entrypoint', str(self.entrypoint), '--build', str(self.build),
            '--releases-dir', str(self.releases), '--corpus', str(self.corpus),
            '--data', str(self.data), '--golden', str(self.golden),
            '--golden-manifest', str(self.gold_manifest), '--timeout', '10']

    def call(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = deploy.main(arguments)
        self.last_error = stderr.getvalue()
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None

    def apply(self):
        code, result = self.call(self.command + ['--apply'])
        self.assertEqual(code, 0, self.last_error)
        return result

    def rollback(self, result):
        return self.call(['rollback', '--deployment', result['deployment'], '--apply'])

    def test_dry_run_performs_no_writes_or_process_launches(self):
        before = deploy.snapshot(self.entrypoint)
        with mock.patch.object(deploy.subprocess, 'Popen', side_effect=AssertionError('process launched')):
            code, result = self.call(self.command)
        self.assertEqual(code, 0, self.last_error)
        self.assertFalse(result['apply'])
        self.assertFalse(self.releases.exists())
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        self.assertEqual(list(self.entrypoint.parent.iterdir()), [self.entrypoint])

    def test_regular_file_activation_and_exact_rollback(self):
        before = deploy.snapshot(self.entrypoint)
        original = self.entrypoint.read_bytes()
        result = self.apply()
        self.assertTrue(self.entrypoint.is_symlink())
        self.assertEqual(subprocess.check_output([str(self.entrypoint), '--version'], text=True).strip(), deploy.VERSION)
        code, _ = self.rollback(result)
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        self.assertEqual(self.entrypoint.read_bytes(), original)
        self.assertEqual(subprocess.check_output([str(self.entrypoint)], text=True), 'old-version\n')
        self.assertEqual(self.rollback(result)[0], 0)  # Idempotent rollback.

    def test_relative_symlink_is_restored_without_modifying_its_target(self):
        old = self.entrypoint.parent / 'old'
        self.entrypoint.rename(old)
        self.entrypoint.symlink_to('old')
        before, original = deploy.snapshot(self.entrypoint), old.read_bytes()
        result = self.apply()
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(self.rollback(result)[0], 0)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        self.assertEqual(os.readlink(self.entrypoint), 'old')

    def test_absent_entrypoint_rollback_removes_only_the_link(self):
        self.entrypoint.unlink()
        result = self.apply()
        self.assertEqual(self.rollback(result)[0], 0)
        self.assertFalse(self.entrypoint.exists() or self.entrypoint.is_symlink())
        self.assertTrue((self.releases / deploy.RELEASE_NAME).is_dir())

    def test_strategy_alias_and_inherited_helpers_use_the_new_release(self):
        self.apply()
        final = self.releases / deploy.RELEASE_NAME
        env = dict(os.environ, MAFFT_BINARIES='/deliberately/wrong')
        for name in ['mafft', 'linsi']:
            output = subprocess.check_output([str(final / 'bin' / name), '--show-helper'], env=env, text=True)
            self.assertEqual(output.strip(), str(final / 'payload/libexec/mafft'))
        self.assertEqual(subprocess.check_output([str(final / 'bin/mafft-profile')], text=True), 'profile\n')
        # Copied aliases are independent of the research installation.
        self.assertFalse((final / 'payload/bin/linsi').is_symlink())
        self.assertFalse((final / 'payload/bin/mafft-profile').is_symlink())

    def test_existing_public_strategy_alias_retains_its_defaults(self):
        alias = self.entrypoint.parent / 'linsi'
        alias.symlink_to('mafft')
        self.apply()
        self.assertEqual(subprocess.check_output([str(alias), '--show-name'], text=True), 'linsi\n')
        self.assertEqual(subprocess.check_output([str(self.entrypoint), '--show-name'], text=True), 'mafft\n')

    def test_ruby_driver_wrappers_pass_the_quoted_release_command(self):
        self.releases = self.root / "releases with space; and 'quote'"
        self.command[self.command.index('--releases-dir') + 1] = str(self.releases)
        self.apply()
        final = self.releases / deploy.RELEASE_NAME
        expected = deploy.shlex.quote(str(final / 'bin/mafft'))
        env = dict(os.environ, MAFFT_HOMOLOGS_MAFFT='/deliberately/wrong')
        homologs = subprocess.check_output([str(final / 'bin/mafft-homologs.rb')], text=True, env=env)
        self.assertEqual(homologs, expected + '\n')
        sparse = subprocess.check_output([str(final / 'bin/mafft-sparsecore.rb'), '-i', 'input.fa'], text=True)
        self.assertEqual(sparse.splitlines(), ['-m', expected, '-i', 'input.fa'])
        # Ruby interpolates this value into a shell command: validate that layer too.
        version = subprocess.check_output(['/bin/sh', '-c', homologs.strip() + ' --version'], text=True)
        self.assertEqual(version.strip(), deploy.VERSION)

    def test_relocated_bundle_absolute_alias_is_resolved_inside_bundle(self):
        moved = self.root / 'moved-build'
        self.build.rename(moved)
        metadata, manifest, sources = deploy.read_package(moved)
        self.assertEqual(sources['bin/mafft-profile'], moved / 'install/libexec/mafft/mafft-profile')

    def test_paths_with_spaces_and_shell_metacharacters_are_quoted(self):
        changed = self.root / 'releases with spaces; no shell expansion'
        self.command[self.command.index('--releases-dir') + 1] = str(changed)
        self.apply()
        self.assertEqual(subprocess.check_output([str(self.entrypoint), '--version'], text=True).strip(), deploy.VERSION)

    def test_changed_package_or_source_is_refused_before_writes(self):
        for p in [self.launcher, self.build / 'source/core/native.c']:
            saved = p.read_bytes()
            p.write_bytes(saved + b'changed')
            code, _ = self.call(self.command + ['--apply'])
            self.assertEqual(code, 1)
            self.assertFalse(self.releases.exists())
            p.write_bytes(saved)

    def test_external_alias_and_extra_files_are_rejected(self):
        link = self.install / 'bin/linsi'
        link.unlink(); link.symlink_to('/bin/sh')
        self.assertEqual(self.call(self.command)[0], 1)
        link.unlink(); link.symlink_to('mafft')
        (self.install / 'unexpected').write_text('extra')
        self.assertEqual(self.call(self.command)[0], 1)

    def test_redeploy_is_a_verified_noop(self):
        self.apply()
        transactions = list((self.releases / 'deployments').iterdir())
        result = self.apply()
        self.assertIn('already active', result['result'])
        self.assertEqual(transactions, list((self.releases / 'deployments').iterdir()))
        helper = self.releases / deploy.RELEASE_NAME / 'payload/libexec/mafft/mafft-profile'
        helper.chmod(0o444)
        self.assertEqual(self.call(self.command + ['--apply'])[0], 1)

    def test_failed_golden_check_never_activates(self):
        before = deploy.snapshot(self.entrypoint)
        with mock.patch.object(deploy, 'check_alignment', side_effect=ValueError('injected golden mismatch')):
            code, _ = self.call(self.command + ['--apply'])
        self.assertEqual(code, 1)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        result = json.loads(next((self.releases / 'deployments').glob('*/result.json')).read_text())
        self.assertEqual(result['rollback'], 'not-activated')

    def test_unrestorable_ownership_is_refused_before_validation_or_activation(self):
        before = deploy.snapshot(self.entrypoint)
        with mock.patch.object(deploy.os, 'chown', side_effect=PermissionError('cannot restore owner')), \
             mock.patch.object(deploy.subprocess, 'Popen', side_effect=AssertionError('process launched')):
            code, _ = self.call(self.command + ['--apply'])
        self.assertEqual(code, 1)
        self.assertIn('cannot restore owner', self.last_error)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        self.assertFalse(list(self.entrypoint.parent.glob('.mafft-restore-check-*')))
        result = json.loads(next((self.releases / 'deployments').glob('*/result.json')).read_text())
        self.assertEqual(result['rollback'], 'not-activated')

    def test_failed_postcheck_automatically_restores_original(self):
        before = deploy.snapshot(self.entrypoint)
        real_check = deploy.check_version
        def failure(binary, directory, timeout):
            if binary == self.entrypoint:
                raise ValueError('injected post-activation version mismatch')
            return real_check(binary, directory, timeout)
        with mock.patch.object(deploy, 'check_version', side_effect=failure):
            code, _ = self.call(self.command + ['--apply'])
        self.assertEqual(code, 1)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        result = json.loads(next((self.releases / 'deployments').glob('*/result.json')).read_text())
        self.assertFalse(result['ok'])
        self.assertEqual(result['rollback'], 'restored')

    def test_external_changes_during_precheck_are_not_overwritten(self):
        def change(*args):
            self.entrypoint.write_text('manual edit')
            raise RuntimeError('injected validation failure')
        with mock.patch.object(deploy, 'check_alignment', side_effect=change):
            self.assertEqual(self.call(self.command + ['--apply'])[0], 1)
        self.assertEqual(self.entrypoint.read_text(), 'manual edit')
        result = json.loads(next((self.releases / 'deployments').glob('*/result.json')).read_text())
        self.assertEqual(result['rollback'], 'not-activated')

    def test_rollback_refuses_a_newer_or_manual_activation(self):
        result = self.apply()
        self.entrypoint.unlink(); self.entrypoint.symlink_to('/a/different/release')
        self.assertEqual(self.rollback(result)[0], 1)
        self.assertEqual(os.readlink(self.entrypoint), '/a/different/release')

    def test_old_rollback_cannot_overwrite_a_new_transaction_of_the_same_release(self):
        first = self.apply()
        self.assertEqual(self.rollback(first)[0], 0)
        self.entrypoint.write_text('#!/bin/sh\nprintf "changed original\\n"\n')
        changed = deploy.snapshot(self.entrypoint)
        second = self.apply()
        active = deploy.snapshot(self.entrypoint)
        self.assertEqual(self.rollback(first)[0], 1)
        self.assertEqual(deploy.snapshot(self.entrypoint), active)
        self.assertEqual(self.rollback(second)[0], 0)
        self.assertEqual(deploy.snapshot(self.entrypoint), changed)

    def test_modified_activation_anchor_prevents_rollback(self):
        result = self.apply()
        record = json.loads(Path(result['deployment']).read_text())
        anchor = Path(record['target'])
        anchor.unlink(); anchor.symlink_to('/a/different/release')
        self.assertEqual(self.rollback(result)[0], 1)
        self.assertEqual(os.readlink(anchor), '/a/different/release')

    def test_corrupt_backup_prevents_rollback(self):
        result = self.apply()
        transaction = Path(result['deployment']).parent
        (transaction / 'previous-file').write_text('corrupt')
        self.assertEqual(self.rollback(result)[0], 1)
        self.assertTrue(self.entrypoint.is_symlink())

    def test_entry_lock_prevents_competing_activation(self):
        before = deploy.snapshot(self.entrypoint)
        with deploy.locked(deploy.entry_lock(self.entrypoint)):
            self.assertEqual(self.call(self.command + ['--apply'])[0], 1)
        self.assertEqual(deploy.snapshot(self.entrypoint), before)
        self.assertFalse((self.releases / deploy.RELEASE_NAME).exists())

    def test_destination_cannot_overlap_the_preserved_build(self):
        self.command[self.command.index('--releases-dir') + 1] = str(self.build / 'releases')
        self.assertEqual(self.call(self.command + ['--apply'])[0], 1)
        self.assertFalse((self.build / 'releases').exists())

    def test_entrypoint_parent_resolves_but_final_symlink_does_not(self):
        old = self.entrypoint.parent / 'old'
        self.entrypoint.rename(old); self.entrypoint.symlink_to('old')
        alias = self.root / 'alias'; alias.symlink_to(self.entrypoint.parent)
        self.assertEqual(deploy.entry_path(alias / 'mafft'), self.entrypoint)

    def test_timeout_kills_validation_descendants_and_retains_command(self):
        directory = self.root / 'timeout'
        program = ('import subprocess,sys,time; '
                   'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                   'print(p.pid,flush=True); time.sleep(30)')
        with self.assertRaises(subprocess.TimeoutExpired):
            deploy.run([sys.executable, '-c', program], directory, .25)
        pid = int((directory / 'stdout').read_text())
        p = Path('/proc') / str(pid) / 'stat'
        if p.exists():
            self.assertIn(p.read_text().split(') ', 1)[1].split()[0], ('Z', 'X'))
        self.assertTrue(json.loads((directory / 'result.json').read_text())['aborted'])
        self.assertTrue((directory / 'command.json').exists())

    def test_reaped_process_group_is_never_killed_after_wait_raises(self):
        child = mock.Mock(returncode=None, pid=12345)
        def reaped_interrupt(**kwargs):
            child.returncode = 0
            raise KeyboardInterrupt()
        child.wait.side_effect = reaped_interrupt
        with mock.patch.object(deploy.subprocess, 'Popen', return_value=child), \
             mock.patch.object(deploy.os, 'killpg') as killpg:
            with self.assertRaises(KeyboardInterrupt):
                deploy.run(['/not/run'], self.root / 'reaped', 1)
        killpg.assert_not_called()
        self.assertTrue(json.loads((self.root / 'reaped/result.json').read_text())['aborted'])

    def test_interrupt_is_delivered_only_after_reap_state_is_recorded(self):
        child = mock.Mock(returncode=None, pid=12345)
        def interrupted_reap(**kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            self.assertIn(signal.SIGTERM, signal.sigpending())
            child.returncode = 0
            return 0
        child.wait.side_effect = interrupted_reap
        with deploy.interruptions(), \
             mock.patch.object(deploy.subprocess, 'Popen', return_value=child), \
             mock.patch.object(deploy.os, 'killpg') as killpg:
            with self.assertRaises(InterruptedError):
                deploy.run(['/not/run'], self.root / 'interrupted-reap', 1)
        killpg.assert_not_called()
        self.assertEqual(json.loads((self.root / 'interrupted-reap/result.json').read_text())['returncode'], 0)

    def test_unsafe_manifest_paths_are_rejected(self):
        for name in ['../x', '/etc/passwd', '', 'a/../b', 'a/./b', 'a//b', None]:
            with self.assertRaises(ValueError):
                deploy.relative(name)


if __name__ == '__main__':
    unittest.main()
