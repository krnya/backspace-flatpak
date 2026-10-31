#!/usr/bin/env bash
# Only import built objects here; never execute upstream application/build code.
set -euo pipefail
: "${GNUPGHOME:?}" "${FLATPAK_GPG_FINGERPRINT:?}" "${PAGES_URL:?}"
key=${FLATPAK_GPG_FINGERPRINT^^}
[[ $key =~ ^[A-F0-9]{40}$ ]] || { echo 'Expected full signing-key fingerprint' >&2; exit 1; }
mkdir -p site/repo
if [[ ! -f site/repo/config ]]; then
  ostree --repo=site/repo init --mode=archive-z2
fi
for arch in x86_64 aarch64; do
  ref="app/io.github.TheZwiss.backspace/$arch/stable"
  source="artifacts/$arch/repo"
  ostree --repo="$source" fsck
  # Create a signed child of the previous published commit, preserving history.
  flatpak build-commit-from --src-repo="$source" --src-ref="$ref" \
    --untrusted --force --timestamp=NOW --no-update-summary --gpg-sign="$key" site/repo "$ref"
done
gpg --batch --export "$key" > "$RUNNER_TEMP/backspace-public.gpg"
flatpak build-update-repo --gpg-sign="$key" \
  --gpg-import="$RUNNER_TEMP/backspace-public.gpg" \
  --default-branch=stable --title='Backspace (unofficial)' site/repo
# Never prune: retain all ancestors and objects across deployments.
ostree --repo=site/repo fsck
python3 scripts/site.py --url "$PAGES_URL" --public-key "$RUNNER_TEMP/backspace-public.gpg"
# A fresh, isolated client must verify both the summary and app commit signatures.
python3 scripts/verify-remote.py site
