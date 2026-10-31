#!/usr/bin/env python3
"""Verify the HTTP repository exactly as a new client would, without host changes."""
import functools
import configparser
import http.server
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from common import APP_ID


def verify(site):
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ)
        env.update(HOME=directory, XDG_DATA_HOME=directory + '/data',
                   XDG_CONFIG_HOME=directory + '/config', XDG_CACHE_HOME=directory + '/cache',
                   FLATPAK_USER_DIR=directory + '/flatpak', OSTREE_SYSROOT=directory + '/sysroot')
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path(site).resolve()))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            def run(*args):
                try:
                    return subprocess.run(args, env=env, check=True, text=True, capture_output=True).stdout
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f'{args[0]} failed: {error.stderr}') from error
            # Consume the generated descriptor, replacing only its deployment URL
            # with this test server. Flatpak imports the embedded GPG key itself.
            descriptor = configparser.ConfigParser(interpolation=None)
            descriptor.optionxform = str
            descriptor.read(Path(site, 'backspace.flatpakrepo'))
            descriptor['Flatpak Repo']['Url'] = base + '/repo/'
            local = Path(directory, 'test.flatpakrepo')
            with local.open('w') as target:
                descriptor.write(target)
            run('flatpak', 'remote-add', '--user', '--from', 'test', str(local))
            for arch in ['x86_64', 'aarch64']:
                ref = f'app/{APP_ID}/{arch}/stable'
                info = run('flatpak', 'remote-info', '--user', '--arch=' + arch, 'test', ref)
                print(info)
                # Pulling verifies the commit signature and every content checksum.
                run('ostree', '--repo=' + directory + '/flatpak/repo', 'pull', '--depth=0', 'test', ref)
                catalog = 'appstream2/' + arch
                run('ostree', '--repo=' + directory + '/flatpak/repo', 'pull', '--depth=0', 'test', catalog)
                xml = run('ostree', '--repo=' + directory + '/flatpak/repo', 'cat', 'test:' + catalog, '/appstream.xml')
                if APP_ID not in xml:
                    raise ValueError(f'Application missing from signed {arch} AppStream catalog')
            # Flatpak's catalog refresh uses the host architecture's summary.
            run('flatpak', 'update', '--user', '--appstream', 'test')
            print('HTTP summary, both app signatures, and object checksums verified')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    verify(sys.argv[1])
