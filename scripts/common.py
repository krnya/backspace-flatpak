"""Small shared helpers; network access is restricted to GitHub HTTPS APIs."""
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

APP_ID = 'io.github.TheZwiss.backspace'
MANIFEST = APP_ID + '.yml'
UPSTREAM = 'TheZwiss/backspace'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def outputs(**values):
    for key, value in values.items():
        line = f'{key}={str(value).lower() if isinstance(value, bool) else value}\n'
        if '\n' in str(value):
            raise ValueError('Multiline workflow output rejected')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write(line)
        print(line, end='')


class GitHub:
    def __init__(self, repo=UPSTREAM):
        self.repo = repo
        self.base = f'https://api.github.com/repos/{repo}'

    def request(self, path, method='GET', data=None, missing_ok=False):
        headers = {'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'backspace-flatpak'}
        if os.environ.get('GH_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + '/' + path, data=body,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and missing_ok:
                return None
            raise RuntimeError(f'GitHub {method} {path}: HTTP {error.code}') from error

    def raw(self, revision, path):
        # This also avoids forwarding the GitHub token to download hosts.
        url = f'https://raw.githubusercontent.com/{self.repo}/{revision}/{path}'
        with urllib.request.urlopen(url, timeout=90) as response:
            return response.read()
