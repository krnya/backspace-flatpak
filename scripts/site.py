#!/usr/bin/env python3
"""Create the remote descriptor and check Pages layout and size."""
import argparse
import base64
import configparser
import html
from pathlib import Path
import re
from urllib.parse import urlparse


def generate(site, base_url, public_key):
    site = Path(site)
    url = urlparse(base_url)
    if url.scheme != 'https' or not url.netloc or url.query or url.fragment or re.search(r'\s', base_url):
        raise ValueError('Pages URL must be an HTTPS base URL')
    base_url = base_url.rstrip('/')
    key = Path(public_key).read_bytes()
    if not key or key.startswith(b'-----BEGIN'):
        raise ValueError('Expected a binary OpenPGP public key')
    descriptor = ('[Flatpak Repo]\nTitle=Backspace (unofficial)\n'
                  'Comment=Independently maintained Backspace Flatpak\n'
                  'Description=Unofficial Backspace stable releases for x86_64 and aarch64\n'
                  f'Url={base_url}/repo/\nHomepage={base_url}/\nDefaultBranch=stable\n'
                  f'GPGKey={base64.b64encode(key).decode()}\n')
    (site / 'backspace.flatpakrepo').write_text(descriptor)
    (site / 'backspace.gpg').write_bytes(key)
    (site / '.nojekyll').touch()
    (site / 'index.html').write_text(f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Backspace Flatpak (unofficial)</title>
<h1>Backspace Flatpak (unofficial)</h1>
<p>An independent distribution, unaffiliated with the Backspace project.</p>
<p><a href="backspace.flatpakrepo">Add the signed Flatpak repository</a></p>
<pre>flatpak remote-add --user --if-not-exists backspace {html.escape(base_url)}/backspace.flatpakrepo
flatpak install --user backspace io.github.TheZwiss.backspace
flatpak update --user io.github.TheZwiss.backspace</pre>
<p><a href="backspace.gpg">Repository public key</a></p></html>\n''')


def check(site, max_bytes=900 * 1024 * 1024):
    site = Path(site)
    for name in ['repo/config', 'repo/summary', 'repo/summary.sig', 'backspace.gpg', 'backspace.flatpakrepo', 'index.html']:
        if not (site / name).is_file() or not (site / name).stat().st_size:
            raise ValueError(f'Missing/empty Pages resource: {name}')
    config = configparser.ConfigParser(interpolation=None)
    config.read(site / 'backspace.flatpakrepo')
    descriptor = config['Flatpak Repo']
    if base64.b64decode(descriptor['GPGKey'], validate=True) != (site / 'backspace.gpg').read_bytes():
        raise ValueError('Descriptor public key mismatch')
    if not descriptor['Url'].endswith('/repo/') or descriptor['DefaultBranch'] != 'stable':
        raise ValueError('Invalid remote descriptor')
    size = sum(p.stat().st_size for p in site.rglob('*') if p.is_file())
    if size > max_bytes:
        raise ValueError(f'Pages site is {size} bytes, above the {max_bytes} byte guard; retain old deployment and migrate hosting')
    print(f'Pages layout valid: {size} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', default='site')
    parser.add_argument('--url')
    parser.add_argument('--public-key')
    args = parser.parse_args()
    if args.url:
        generate(args.site, args.url, args.public_key)
    check(args.site)
