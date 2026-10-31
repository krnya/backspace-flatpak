import base64
import copy
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from common import APP_ID, MANIFEST
from validate import inspect, validate


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sync = module('sync', 'sync-upstream.py')
state = module('state', 'repository-state.py')
site_tools = module('site_tools', 'site.py')
check_site, generate = site_tools.check, site_tools.generate
LOCK = json.loads((ROOT / 'upstream.lock.json').read_text())


class API:
    def __init__(self):
        self.sha = LOCK['metadata_commit']
        self.source = LOCK['source_commit']
        self.prerelease = False
        self.fail = None

    def request(self, path):
        if path == 'releases/latest':
            return {'tag_name': LOCK['tag'], 'draft': False, 'prerelease': self.prerelease}
        if path.startswith('git/ref/tags/'):
            return {'object': {'type': 'tag', 'sha': 'a' * 40}}
        if path.startswith('git/tags/'):
            return {'object': {'type': 'commit', 'sha': self.source}}
        if path == 'commits/main':
            return {'sha': self.sha}
        raise AssertionError(path)

    def raw(self, revision, name):
        if name == self.fail:
            raise RuntimeError('simulated download failure')
        if name == 'pnpm-lock.yaml':
            return b'fixture lockfile'
        return (ROOT / name).read_bytes()


class AutomationTests(unittest.TestCase):
    def test_current_manifest_and_resource_closure(self):
        manifest, lock = validate(ROOT)
        self.assertEqual(manifest['app-id'], APP_ID)
        self.assertIn('--socket=wayland', manifest['finish-args'])
        self.assertIn('--socket=pulseaudio', manifest['finish-args'])
        self.assertIn('rm -f "$FLATPAK_DEST/backspace/resources/app-update.yml"',
                      (ROOT / 'flatpak/build.sh').read_text())

    def test_sync_idempotent_and_unrelated_main_commit_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            api = API()
            self.assertTrue(sync.sync(directory, api))
            first = (Path(directory) / 'upstream.lock.json').read_bytes()
            api.sha = 'b' * 40
            self.assertFalse(sync.sync(directory, api))
            self.assertEqual(first, (Path(directory) / 'upstream.lock.json').read_bytes())

    def test_waits_for_new_stable_release_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            api = API()
            sync.sync(directory, api)
            before = (Path(directory) / MANIFEST).read_bytes()
            api.source = 'd' * 40
            self.assertFalse(sync.sync(directory, api))
            self.assertEqual(before, (Path(directory) / MANIFEST).read_bytes())
            with tempfile.TemporaryDirectory() as empty:
                with self.assertRaisesRegex(ValueError, 'No synchronized release'):
                    sync.sync(empty, api)

    def test_prereleases_rejected(self):
        api = API()
        api.prerelease = True
        with self.assertRaisesRegex(ValueError, 'Not an official stable'):
            sync.stable_release(api)

    def test_incomplete_download_never_changes_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            api = API()
            sync.sync(directory, api)
            before = (Path(directory) / MANIFEST).read_bytes()
            api.fail = 'flatpak/node-sources.json'
            with self.assertRaisesRegex(RuntimeError, 'download failure'):
                sync.sync(directory, api)
            self.assertEqual(before, (Path(directory) / MANIFEST).read_bytes())
            validate(directory)

    def test_detects_corrupted_offline_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            api = API()
            sync.sync(directory, api)
            file = Path(directory) / 'flatpak/node-sources.json'
            file.write_text(file.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                sync.sync(directory, api)

    def test_missing_local_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            api = API()
            sync.sync(directory, api)
            (Path(directory) / 'flatpak/build.sh').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing resource'):
                inspect(directory)

    def test_plan_skips_success_and_retries_deployment_without_rebuild(self):
        self.assertEqual(state.decide('a', None), (True, True))
        published = {'input_id': 'a', 'asset': 'snapshot-a', 'deployed_asset': 'snapshot-a'}
        self.assertEqual(state.decide('a', published), (False, False))
        pending = dict(published, asset='snapshot-b')
        self.assertEqual(state.decide('a', pending), (False, True))
        self.assertEqual(state.decide('b', published), (True, True))
        self.assertEqual(state.decide('a', published, True), (True, True))

    def test_missing_durable_state_is_not_a_cache_miss(self):
        class Missing:
            def request(self, path, **kwargs):
                return None if path.startswith('git/ref/') else {'id': 1}
        with self.assertRaisesRegex(ValueError, 'state branch is missing'):
            state.load(Missing())

    def test_snapshot_checksum_failure_never_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'site'
            class Download:
                repo = 'example/backspace-flatpak'
            def download(args, **kwargs):
                target = Path(args[-1]) / 'repository-1-1.tar.gz'
                target.write_bytes(b'corrupted archive')
            with patch.object(state.subprocess, 'run', side_effect=download):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    state.restore(Download(), {'asset': 'repository-1-1.tar.gz', 'sha256': '0' * 64}, destination)
            self.assertFalse(destination.exists())

    def test_interrupted_bootstrap_record_is_retryable(self):
        class Initializing:
            def request(self, path, **kwargs):
                if path.startswith('git/ref/'):
                    return {'object': {'sha': 'a' * 40}}
                return {'content': base64.b64encode(b'{"schema":1,"initializing":true}').decode()}
        sha, current = state.load(Initializing())
        self.assertEqual(sha, 'a' * 40)
        self.assertIsNone(current)
        self.assertEqual(state.decide('next', current), (True, True))

    def test_failed_snapshot_upload_preserves_previous_pointer(self):
        previous = {'input_id': 'old', 'fingerprint': 'A' * 40, 'asset': 'repository-1-1.tar.gz'}
        class ExistingRelease:
            repo = 'example/backspace-flatpak'
            def request(self, *args, **kwargs):
                return {'id': 1}
        real_run = subprocess.run
        def fail_upload(args, **kwargs):
            if args[0] == 'gh':
                raise subprocess.CalledProcessError(1, args)
            return real_run(args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'contents').write_text('fixture snapshot')
            with patch.object(state, 'load', return_value=('b' * 40, previous)), \
                 patch.object(state, 'store') as store, \
                 patch.object(state.subprocess, 'run', side_effect=fail_upload), \
                 patch.dict(os.environ, GITHUB_RUN_ID='2', GITHUB_RUN_ATTEMPT='1', SOURCE_REVISION='c' * 40):
                with self.assertRaises(subprocess.CalledProcessError):
                    state.save(ExistingRelease(), {'input_id': 'new', 'state_sha': 'b' * 40}, directory, 'A' * 40)
                store.assert_not_called()
        self.assertEqual(previous['input_id'], 'old')

    def test_accidental_signing_key_replacement_rejected(self):
        with patch.object(state, 'load', return_value=('b' * 40, {'fingerprint': 'A' * 40})):
            with self.assertRaisesRegex(ValueError, 'Signing key changed'):
                state.save(None, {'state_sha': 'b' * 40}, '.', 'B' * 40)

    def test_archive_traversal_and_symlinks_rejected(self):
        for path, kind in [('../escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
            with tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as target:
                    info = tarfile.TarInfo(path)
                    info.type = kind
                    info.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                    target.addfile(info, io.BytesIO())
                with self.assertRaisesRegex(ValueError, 'Unsafe archive entry'):
                    state.extract(archive, Path(directory) / 'output')

    def test_descriptor_and_pages_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / 'repo').mkdir()
            for name in ['config', 'summary', 'summary.sig']:
                (site / 'repo' / name).write_bytes(b'fixture')
            key = site / 'key'
            key.write_bytes(b'public key fixture')
            generate(site, 'https://example.github.io/backspace-flatpak', key)
            check_site(site)
            self.assertIn('Url=https://example.github.io/backspace-flatpak/repo/',
                          (site / 'backspace.flatpakrepo').read_text())
            with self.assertRaisesRegex(ValueError, 'above'):
                check_site(site, max_bytes=1)
            (site / 'repo/summary.sig').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing/empty'):
                check_site(site)


@unittest.skipUnless(all(shutil.which(x) for x in ['ostree', 'flatpak', 'gpg']), 'requires ostree, flatpak and gpg')
class SignedRepositoryTests(unittest.TestCase):
    def test_signed_http_install_update_history_and_data(self):
        # Tiny app fixture: exercise real OSTree/Flatpak, never build Electron in unit tests.
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copytree(ROOT / 'scripts', work / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            env = dict(os.environ, GNUPGHOME=str(work / 'gpg'), RUNNER_TEMP=str(work),
                       PAGES_URL='https://example.github.io/backspace-flatpak')
            (work / 'gpg').mkdir(mode=0o700)
            def run(*args, **kwargs):
                try:
                    return subprocess.run(args, cwd=work, env=kwargs.pop('env', env), check=True,
                                          text=True, capture_output=True, **kwargs).stdout
                except subprocess.CalledProcessError as error:
                    error.add_note(error.stdout + error.stderr)
                    raise
            run('gpg', '--batch', '--passphrase', '', '--quick-generate-key',
                'Test Flatpak <test@example.invalid>', 'rsa2048', 'sign', '1d')
            key = next(line.split(':')[9] for line in run('gpg', '--with-colons', '--list-secret-keys').splitlines()
                       if line.startswith('fpr:'))
            env['FLATPAK_GPG_FINGERPRINT'] = key
            commits = []
            for version in ['1', '2']:
                for arch in ['x86_64', 'aarch64']:
                    app = work / 'app'
                    if app.exists():
                        shutil.rmtree(app)
                    (app / 'files/bin').mkdir(parents=True)
                    (app / 'files/bin/backspace').write_text('#!/bin/sh\necho ' + version + '\n')
                    (app / 'files/bin/backspace').chmod(0o755)
                    (app / 'export').mkdir()
                    (app / 'metadata').write_text(f'[Application]\nname={APP_ID}\nruntime=org.freedesktop.Platform/{arch}/25.08\nsdk=org.freedesktop.Sdk/{arch}/25.08\ncommand=backspace\n')
                    catalog = app / f'files/share/app-info/xmls/{APP_ID}.xml.gz'
                    catalog.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(catalog, 'wt') as target:
                        target.write(f'<components><component type="desktop-application">'
                                     f'<id>{APP_ID}</id><name>Backspace fixture</name>'
                                     '<summary>Repository integration test</summary>'
                                     f'<bundle type="flatpak">app/{APP_ID}/{arch}/stable</bundle>'
                                     f'<releases><release version="{version}" date="2026-10-31"/></releases>'
                                     '</component></components>')
                    repo = work / f'artifacts/{arch}/repo'
                    repo.parent.mkdir(parents=True, exist_ok=True)
                    run('flatpak', 'build-export', '--disable-sandbox', '--arch=' + arch, str(repo), str(app), 'stable')
                run('bash', 'scripts/publish.sh')
                commit = run('ostree', '--repo=site/repo', 'rev-parse', f'app/{APP_ID}/x86_64/stable').strip()
                commits.append(commit)
                if version == '1':
                    # Retain a durable snapshot, then delete/recover the entire site.
                    archive = work / 'snapshot.tar.gz'
                    run('tar', '-czf', str(archive), '-C', 'site', '.')
                    shutil.rmtree(work / 'site')
                    state.extract(archive, work / 'site')
                    client = dict(env, HOME=str(work / 'client'), FLATPAK_USER_DIR=str(work / 'client/flatpak'),
                                  XDG_DATA_HOME=str(work / 'client/data'), XDG_CACHE_HOME=str(work / 'client/cache'),
                                  XDG_CONFIG_HOME=str(work / 'client/config'))
                    run('flatpak', 'remote-add', '--user', '--gpg-import=' + str(work / 'site/backspace.gpg'),
                        'test', 'file://' + str(work / 'site/repo'), env=client)
                    run('flatpak', 'install', '--user', '-y', '--no-deps', 'test', APP_ID, env=client)
                    data = work / 'client/.var/app' / APP_ID / 'config/sentinel'
                    data.parent.mkdir(parents=True, exist_ok=True)
                    data.write_text('keep me')
                else:
                    run('flatpak', 'update', '--user', '-y', '--no-deps', APP_ID, env=client)
                    self.assertEqual(data.read_text(), 'keep me')
                    installed = run('flatpak', 'info', '--user', '--show-commit', APP_ID, env=client).strip()
                    self.assertEqual(installed, commit)
            self.assertNotEqual(*commits)
            history = run('ostree', '--repo=site/repo', 'log', f'app/{APP_ID}/x86_64/stable')
            self.assertIn(commits[0], history)
            run('flatpak', 'update', '--user', '-y', '--no-deps', '--commit=' + commits[0], APP_ID, env=client)
            self.assertEqual(data.read_text(), 'keep me')
            # Tampering with repository summary must fail a fresh HTTP client.
            summary = work / 'site/repo/summary'
            payload = bytearray(summary.read_bytes())
            payload[-1] ^= 1
            summary.write_bytes(payload)
            with self.assertRaises(subprocess.CalledProcessError):
                run('python3', 'scripts/verify-remote.py', 'site')


if __name__ == '__main__':
    unittest.main()
