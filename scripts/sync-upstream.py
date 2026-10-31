#!/usr/bin/env python3
"""Copy a stable-release-paired packaging snapshot, atomically after validation."""
import argparse
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import quote
import yaml
from common import GitHub, MANIFEST, digest, outputs, write_json
from validate import inspect, local_path, SHA


def stable_release(api):
    release = api.request('releases/latest')
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError(f'Not an official stable vX.Y.Z release: {tag}')
    obj = api.request('git/ref/tags/' + quote(tag, safe=''))['object']
    for _ in range(10):
        if obj['type'] != 'tag':
            break
        obj = api.request('git/tags/' + obj['sha'])['object']
    if obj['type'] != 'commit' or not SHA.fullmatch(obj['sha']):
        raise ValueError('Release tag does not resolve to a commit')
    return tag, obj['sha']


def collect(api, revision, root):
    """Download only the local resource closure, all at one immutable revision."""
    seen = set()

    def fetch(name, parse=False):
        name = local_path(name)
        if name in seen:
            return
        seen.add(name)
        data = api.raw(revision, name)
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if parse:
            walk(json.loads(data) if name.endswith('.json') else yaml.safe_load(data))

    def walk(item):
        if isinstance(item, dict):
            if 'path' in item:
                fetch(item['path'])
            for key, value in item.items():
                if key in ('sources', 'modules'):
                    for entry in value:
                        if isinstance(entry, str):
                            fetch(entry, True)
                        else:
                            walk(entry)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)
    fetch(MANIFEST, True)


def sync(root, api):
    root = Path(root)
    tag, commit = stable_release(api)
    metadata = api.request('commits/main')['sha']
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        # Check the pin before downloading the 1 MB dependency list.
        text = api.raw(metadata, MANIFEST)
        manifest = yaml.safe_load(text)
        pins = [s.get('commit') for m in manifest['modules'] if isinstance(m, dict)
                for s in m.get('sources', []) if isinstance(s, dict) and s.get('type') == 'git']
        if pins != [commit]:
            print(f'Waiting for upstream Flatpak metadata for {tag}; main pins {pins}')
            outputs(changed=False, pending=True)
            if not (root / 'upstream.lock.json').exists():
                raise ValueError('No synchronized release yet; retry after upstream metadata lands')
            return False
        collect(api, metadata, stage)
        _, files, pin = inspect(stage)
        if pin != commit:
            raise ValueError('Manifest source differs from stable release')
        lock = {'schema': 1, 'tag': tag, 'source_commit': commit,
                'metadata_commit': metadata,
                'release_lockfile_sha256': digest(api.raw(commit, 'pnpm-lock.yaml')),
                'files': {name: digest((stage / name).read_bytes()) for name in sorted(files)}}
        old_path = root / 'upstream.lock.json'
        old = json.loads(old_path.read_text()) if old_path.exists() else {}
        # An unrelated main commit must not cause a synchronization commit.
        equivalent = {k: v for k, v in lock.items() if k != 'metadata_commit'}
        old_equivalent = {k: v for k, v in old.items() if k != 'metadata_commit'}
        if equivalent == old_equivalent:
            from validate import validate
            validate(root)  # Refuse a locally corrupted pair instead of silently skipping it.
            outputs(changed=False, pending=False)
            return False
        write_json(stage / 'upstream.lock.json', lock)
        from validate import validate
        validate(stage)
        # No files are touched until all resources and their checksums validate.
        for name in files | {'upstream.lock.json'}:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((stage / name).read_bytes())
        for name in set(old.get('files', {})) - files:
            (root / local_path(name)).unlink(missing_ok=True)
        outputs(changed=True, pending=False)
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    sync(args.root, GitHub())
