#!/usr/bin/env python3
"""Validate the source closure without running upstream code."""
import json
from pathlib import Path, PurePosixPath
import re
import sys
import xml.etree.ElementTree as ET
import yaml
from common import APP_ID, MANIFEST, UPSTREAM, digest

SHA = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')


def local_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(f'Unsafe local resource path: {value}')
    if str(path) != MANIFEST and path.parts[0] != 'flatpak':
        raise ValueError(f'Unexpected local resource outside flatpak/: {value}')
    return str(path)


def inspect(root):
    """Return parsed manifest and every recursively referenced local file."""
    root = Path(root)
    found = set()
    manifests = set()

    def load(name):
        name = local_path(name)
        if name in found:
            return None
        found.add(name)
        text = (root / name).read_text()
        data = json.loads(text) if name.endswith('.json') else yaml.safe_load(text)
        manifests.add(name)
        walk(data)
        return data

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            kind = value.get('type')
            if kind in ('archive', 'file') and 'url' in value:
                checksum = (SHA256.fullmatch(value.get('sha256', '')) or
                            re.fullmatch(r'[0-9a-f]{128}', value.get('sha512', '')))
                if not value['url'].startswith('https://') or not checksum:
                    raise ValueError(f'Download must use HTTPS and SHA256/SHA512: {value.get("url")}')
            if kind == 'git':
                if value.get('url') != f'https://github.com/{UPSTREAM}.git' or not SHA.fullmatch(value.get('commit', '')):
                    raise ValueError('Git source must be the immutable upstream release commit')
                if value.get('branch') or value.get('tag'):
                    raise ValueError('Mutable git source ref rejected')
            if 'path' in value:
                name = local_path(value['path'])
                if not (root / name).is_file():
                    raise ValueError(f'Missing resource: {name}')
                found.add(name)
            for key, item in value.items():
                if key in ('sources', 'modules'):
                    for entry in item:
                        if isinstance(entry, str):
                            load(entry)
                        else:
                            walk(entry)
                elif isinstance(item, (list, dict)):
                    walk(item)

    manifest = load(MANIFEST)
    if manifest.get('app-id') != APP_ID:
        raise ValueError('Application ID changed')
    pins = []

    def pins_in(value):
        if isinstance(value, dict):
            if value.get('type') == 'git':
                pins.append(value['commit'])
            for item in value.values():
                pins_in(item)
        elif isinstance(value, list):
            for item in value:
                pins_in(item)
    pins_in(manifest)
    if len(pins) != 1:
        raise ValueError('Expected one application source pin')
    if 'flatpak/node-sources.json' not in found:
        raise ValueError('Offline dependencies missing')
    # Flatpak automatically provides ~/.var/app/APP_ID; no host-home permission.
    meta = ET.parse(root / f'flatpak/{APP_ID}.metainfo.xml').getroot()
    if meta.findtext('id') != APP_ID:
        raise ValueError('AppStream ID mismatch')
    return manifest, found, pins[0]


def validate(root):
    root = Path(root)
    manifest, files, pin = inspect(root)
    lock = json.loads((root / 'upstream.lock.json').read_text())
    if lock['source_commit'] != pin or not SHA.fullmatch(lock['metadata_commit']):
        raise ValueError('Source/provenance mismatch')
    if set(lock['files']) != files:
        raise ValueError('Resource inventory mismatch')
    for name, checksum in lock['files'].items():
        if digest((root / local_path(name)).read_bytes()) != checksum:
            raise ValueError(f'Provenance checksum mismatch: {name}')
    print(f'Validated {lock["tag"]}, source {pin}, {len(files)} pinned resources')
    return manifest, lock


if __name__ == '__main__':
    validate(sys.argv[1] if len(sys.argv) > 1 else '.')
