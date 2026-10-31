#!/usr/bin/env python3
"""Durable snapshots in Releases; an atomic Git ref records ready/deployed state."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.parse import quote
from common import GitHub, digest, file_digest, outputs, write_json
from validate import validate

BRANCH = 'repository-state'
TAG = 'flatpak-repository'


def load(api):
    ref = api.request(f'git/ref/heads/{BRANCH}', missing_ok=True)
    if ref is None:
        # Losing durable state must never silently reset an existing repository.
        if api.request(f'releases/tags/{TAG}', missing_ok=True) is not None:
            raise ValueError('Snapshot release exists but state branch is missing; restore repository-state first')
        return None, None
    sha = ref['object']['sha']
    data = api.request(f'contents/state.json?ref={sha}')
    state = json.loads(base64.b64decode(data['content']))
    if state == {'schema': 1, 'initializing': True}:
        return sha, None
    if state['schema'] != 1 or not re.fullmatch(r'repository-\d+-\d+\.tar\.gz', state['asset']):
        raise ValueError('Invalid durable state')
    if not re.fullmatch(r'[0-9a-f]{64}', state['sha256']):
        raise ValueError('Invalid snapshot digest')
    return sha, state


def store(api, previous_sha, state):
    tree = api.request('git/trees', 'POST', {'tree': [{
        'path': 'state.json', 'mode': '100644', 'type': 'blob',
        'content': json.dumps(state, indent=2, sort_keys=True) + '\n'}]})
    commit = api.request('git/commits', 'POST', {
        'message': 'Record Flatpak repository publication state',
        'tree': tree['sha'], 'parents': [previous_sha] if previous_sha else []})
    if previous_sha:
        # No force push: a stale writer fails without overwriting another state.
        api.request(f'git/refs/heads/{BRANCH}', 'PATCH', {'sha': commit['sha'], 'force': False})
    else:
        api.request('git/refs', 'POST', {'ref': f'refs/heads/{BRANCH}', 'sha': commit['sha']})
    return commit['sha']


def input_id(root):
    root = Path(root)
    _, lock = validate(root)
    paths = set(lock['files']) | {'upstream.lock.json', 'requirements.txt', '.github/workflows/build.yml'}
    paths.update(str(p.relative_to(root)) for p in (root / 'scripts').glob('*.py'))
    paths.update(str(p.relative_to(root)) for p in (root / 'scripts').glob('*.sh'))
    return digest(''.join(f'{name}\0{digest((root / name).read_bytes())}\n' for name in sorted(paths)).encode())


def decide(wanted, state, force=False):
    build = force or state is None or state['input_id'] != wanted
    deploy = build or state.get('deployed_asset') != state.get('asset')
    return build, deploy


def restore(api, state, destination):
    if state is None:
        Path(destination).mkdir(parents=True, exist_ok=True)
        return
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['gh', 'release', 'download', TAG, '--repo', api.repo,
                        '--pattern', state['asset'], '--dir', directory], check=True)
        archive = Path(directory) / state['asset']
        if file_digest(archive) != state['sha256']:
            raise ValueError('Durable snapshot checksum mismatch; refusing a fresh repository')
        extract(archive, destination)


def extract(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as source:
        # Reject traversal, devices, and links before writing any member.
        members = source.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive entry: {member.name}')
        source.extractall(destination, members=members, filter='data')


def save(api, plan, site, fingerprint):
    sha, state = load(api)
    if sha != plan['state_sha']:
        raise ValueError('Durable state changed since planning; refusing to overwrite repository history')
    fingerprint = fingerprint.upper()
    if state and state['fingerprint'] != fingerprint:
        raise ValueError('Signing key changed; explicit key rotation is required')
    if sha is None:
        # Record bootstrap intent before creating the release. A failed first
        # upload can then retry, while a deleted state branch still fails closed.
        sha = store(api, None, {'schema': 1, 'initializing': True})
    if api.request(f'releases/tags/{TAG}', missing_ok=True) is None:
        api.request('releases', 'POST', {
            'tag_name': TAG, 'target_commitish': os.environ['SOURCE_REVISION'],
            'name': 'Flatpak repository snapshots (do not delete)',
            'body': 'Durable signed OSTree snapshots. Managed by CI; repository-state records the current snapshot.',
            'make_latest': 'false', 'prerelease': False})
    name = f'repository-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}.tar.gz'
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / name
        # Dereference links so both Pages and the stored snapshot contain plain files.
        subprocess.run(['tar', '--dereference', '--hard-dereference', '-czf', str(archive), '-C', site, '.'], check=True)
        checksum = file_digest(archive)
        subprocess.run(['gh', 'release', 'upload', TAG, str(archive), '--repo', api.repo], check=True)
    next_state = {'schema': 1, 'asset': name, 'sha256': checksum,
                  'input_id': plan['input_id'], 'fingerprint': fingerprint,
                  'source_revision': os.environ['SOURCE_REVISION'],
                  'deployed_asset': state.get('deployed_asset') if state else None}
    store(api, sha, next_state)
    return next_state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'restore', 'save', 'deployed'])
    parser.add_argument('--site', default='site')
    args = parser.parse_args()
    api = GitHub(os.environ['GITHUB_REPOSITORY'])
    if args.command == 'plan':
        sha, state = load(api)
        wanted = input_id('.')
        build, deploy = decide(wanted, state, os.environ.get('FORCE_REBUILD') == 'true')
        write_json('plan.json', {'input_id': wanted, 'state_sha': sha, 'build': build, 'deploy': deploy})
        outputs(build=build, deploy=deploy, input_id=wanted)
    elif args.command == 'restore':
        sha, state = load(api)
        if sha != json.loads(Path('plan.json').read_text())['state_sha']:
            raise ValueError('Durable state changed since planning')
        restore(api, state, args.site)
    elif args.command == 'save':
        save(api, json.loads(Path('plan.json').read_text()), args.site, os.environ['FLATPAK_GPG_FINGERPRINT'])
    else:
        sha, state = load(api)
        plan = json.loads(Path('plan.json').read_text())
        if state is None or state['input_id'] != plan['input_id']:
            raise ValueError('Publication state changed unexpectedly')
        state['deployed_asset'] = state['asset']
        store(api, sha, state)
