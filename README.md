# Backspace Flatpak (unofficial)

An independently maintained Flatpak distribution of [Backspace](https://github.com/TheZwiss/backspace), unaffiliated with its maintainers. The application ID remains `io.github.TheZwiss.backspace`. Stable releases for **x86_64 and aarch64** are built on native GitHub runners and delivered through a signed OSTree repository on GitHub Pages. Users install once and receive updates with Flatpak or KDE Discover.

## Install and update

Replace `OWNER` with the GitHub account hosting this repository. Install Flatpak and your desktop's Flatpak integration first (`plasma-discover-backend-flatpak` on Debian/Ubuntu KDE).

```sh
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --user --if-not-exists backspace \
  https://OWNER.github.io/backspace-flatpak/backspace.flatpakrepo
flatpak install --user backspace io.github.TheZwiss.backspace
flatpak run io.github.TheZwiss.backspace
```

```sh
flatpak update --user io.github.TheZwiss.backspace
```

The `.flatpakrepo` embeds the public signing key and points to `/backspace-flatpak/repo/`. Opening it in Discover also adds the remote on desktops with Flatpak integration. Discover consumes the signed AppStream catalog and offers normal updates. No application bundles need to be downloaded or reinstalled. Custom domains use the URL returned by GitHub's Pages configuration automatically.

For an installation already obtained from another remote, inspect `flatpak info --show-origin io.github.TheZwiss.backspace`; explicitly switch it using `flatpak install --user --reinstall backspace io.github.TheZwiss.backspace`. Back up its data first.

## Maintainer setup (once)

1. Create a **public** GitHub repository named `backspace-flatpak`, with `main` as its default branch, and push these files. Enable GitHub Actions. The first push may start a run before secrets/Pages are configured; it is safe to rerun after setup.
2. In **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**. No `gh-pages` branch is needed. Allow the `github-pages` environment to deploy from `main`. For unattended operation, do not require manual environment approvals.
3. Generate a dedicated persistent signing key using the commands below, and add the two repository secrets. No personal access token is required: jobs use the short-lived `GITHUB_TOKEN`.
4. Allow Actions to write repository contents. The sync job must be able to commit packaging updates to `main`, and the publication job must be able to create Releases and update `repository-state`. Repository/organization policies or branch protection must permit those specific operations. Do not enable PR approval permissions; the pipeline does not need them.
5. In **Actions → Sync and publish Backspace → Run workflow**, select `main` and run it with `force_rebuild` unchecked. Or use:

   ```sh
   gh workflow run sync.yml --repo OWNER/backspace-flatpak --ref main
   gh run list --repo OWNER/backspace-flatpak --workflow sync.yml
   ```

After the first successful deployment, the installation URL above is live. Both architectures must succeed before either is published. ARM64 hosted runners require a repository/account eligible for `ubuntu-24.04-arm`; this configuration targets public repositories. If ARM64 is unavailable, disable it deliberately in the matrix **and** update the architecture lists in `scripts/publish.sh` and `scripts/verify-remote.py` together.

### Persistent GPG key and secrets

Use a dedicated CI signing key, not your personal key. Run this locally with GnuPG and an authenticated GitHub CLI. This creates a non-expiring signing-only RSA key with no passphrase so unattended OSTree signing works. GitHub encrypts the private material as an Actions secret; only the trusted signing step receives it. Keep a separate encrypted backup of the key and its revocation certificate. Never commit them.

```sh
export GNUPGHOME
GNUPGHOME=$(mktemp -d)
chmod 700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-generate-key \
  'Backspace Flatpak (unofficial) <flatpak@example.invalid>' rsa4096 sign 0
fingerprint=$(gpg --batch --with-colons --list-secret-keys |
  awk -F: '$1 == "fpr" {print $10; exit}')

gpg --batch --export-secret-keys "$fingerprint" | base64 -w0 |
  gh secret set FLATPAK_GPG_PRIVATE_KEY --repo OWNER/backspace-flatpak
printf '%s' "$fingerprint" |
  gh secret set FLATPAK_GPG_FINGERPRINT --repo OWNER/backspace-flatpak

# Public material is safe to retain and share for independent verification.
gpg --batch --export "$fingerprint" > backspace-public.gpg
gpg --fingerprint "$fingerprint"
```

| Repository secret | Value |
| --- | --- |
| `FLATPAK_GPG_PRIVATE_KEY` | Single-line base64 of the binary secret-key export; no passphrase |
| `FLATPAK_GPG_FINGERPRINT` | Full uppercase 40-character fingerprint of that key |

Replace the example key identity if desired. Save the temporary GnuPG directory securely or export an encrypted offline backup before removing it. The workflow imports this key into a temporary directory, checks its fingerprint, signs the repository, and removes the directory on exit. It never generates a replacement key. A changed fingerprint is rejected once durable state exists; key rotation requires a deliberate migration, including importing the new public key on clients. Publish the fingerprint through a separate trusted channel so users can check the initial HTTPS trust bootstrap.

PR validation has read-only permissions and receives no signing secrets. Neither the source synchronization job nor either application build job receives the private key. Upstream scripts run only in the build jobs. Protect `main`, review changes to workflows/scripts, and do not add `pull_request_target` execution of PR code.

## How automation works

`sync.yml` runs at minute 17 every six hours (UTC), on relevant pushes to `main`, and on manual dispatch. GitHub may delay schedules and may disable schedules in inactive public repositories; check Actions if polling stops. Pushes containing only documentation do not build the application.

The synchronizer resolves GitHub's latest official stable `vX.Y.Z` release, rejects drafts/prereleases, and peels annotated tags to an exact source commit. It takes one immutable snapshot of upstream `main` and waits until that snapshot's Flatpak manifest pins the release commit. A new release published before upstream's metadata PR lands therefore waits until the next poll; it never substitutes an arbitrary `main` source commit or regenerates mismatched dependencies.

The manifest, `flatpak/node-sources.json`, build script, desktop file, and AppStream metadata are copied **byte for byte from the same metadata revision**. Referenced local includes are collected recursively; unused upstream files and CI scripts are not copied. `upstream.lock.json` records both revisions, the release lockfile checksum, and SHA256 checksums for the resource closure. Source URLs retain upstream SHA256/SHA512 verification. Validation runs before synchronization writes files. Unrelated upstream commits do not change the lock, and identical runs create no commits.

The reusable `build.yml` hashes all packaging/build inputs and compares them with durable publication state. An unchanged successful publication skips builds and deployment. A failed build is retried by a later poll; a successful build followed by a failed deployment reuses its stored signed snapshot without rebuilding. A manual forced rebuild is available for runtime/toolchain maintenance.

Builds reuse the upstream manifest and offline `flatpak/build.sh` through the pinned [Flatter](https://github.com/andyholmes/flatter) action and upstream's pinned multiarchitecture builder container. The only branch override is `stable`. Flatter's bundle output, Pages output, and repository cache are disabled. Disposable download/compiler caches accelerate builds; losing them affects speed only. Runtime/SDK refs follow upstream's versioned Flatpak branches, so their security updates can change build results. The application source and downloaded dependency content are pinned, but byte-for-byte reproducibility across evolving SDK revisions is not claimed.

## Repository persistence and failure handling

A GitHub Actions cache is **not** durable repository storage. Publication restores a complete, checksummed snapshot from the repository's `flatpak-repository` GitHub Release. The separate `repository-state` branch records its immutable asset name, SHA256, signing fingerprint, build-input digest, and last deployed asset. Do not delete this branch or that release.

After both architecture builds pass, the publication job:

1. Restores the complete prior repository and checks its archive digest. Missing/corrupt state fails closed rather than starting an empty replacement.
2. Imports each unsigned build using `flatpak build-commit-from --untrusted`, creating a signed child of the previous application commit. All parent history and objects are retained; no pruning runs.
3. Generates signed summary and AppStream metadata, the public key and `.flatpakrepo`, then checks OSTree integrity and the Pages directory layout.
4. Uses a fresh temporary Flatpak client against a local HTTP server to verify metadata, signatures and downloaded objects for both architectures.
5. Uploads a new immutable snapshot Release asset, then atomically advances the state branch. A small initialization record makes interrupted first uploads retryable.
6. Uploads `site/` with the official Pages artifact action and deploys it with the official Pages deployment action. Only a successful deployment is acknowledged in durable state.

The Pages artifact root contains `index.html`, `backspace.flatpakrepo`, `backspace.gpg`, and `repo/` with OSTree `config`, `summary`, `summary.sig`, `objects/`, refs and AppStream catalogs. Keys, build directories and diagnostics are outside this tree.

One workflow-wide concurrency group covers sync through deployment acknowledgement; it never cancels an active publication. A failed build cannot reach signing or deployment. Archive uploads/state updates happen before deployment; a failure leaves the existing Pages site active. An interrupted acknowledgement causes a harmless redeployment of the same complete snapshot. Git pushes/state updates do not use force. The sync commit uses `GITHUB_TOKEN`, so the caller explicitly invokes the reusable build instead of relying on another push event.

GitHub Pages has a [1 GB site limit and a soft 100 GB/month bandwidth limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits). Publication refuses sites over **900 MiB** before saving/deploying them. Retaining all historical versions will eventually require moving the same signed repository to larger static hosting, or an explicit retention-policy change. Until then, a size failure leaves the working version online. Snapshots are not automatically deleted. Static deltas are omitted to conserve space; standard OSTree updates work without them. No finite free Pages service can promise unlimited history or traffic.

## Desktop behavior and data

All upstream sandbox permissions are preserved, including native Wayland, fallback X11, audio, devices/webcam, notifications, tray integration and narrowly scoped UPower access. Screen capture and PipeWire use desktop portals. No additional host-filesystem or bus permissions are added. Install your desktop's XDG portal backend (for KDE, `xdg-desktop-portal-kde`) and PipeWire for screen sharing. Global shortcuts and autostart retain upstream's documented desktop-dependent limits.

Upstream's build script removes `resources/app-update.yml`, and upstream detects Flatpak to disable its internal updater. Flatpak owns updates. Persistent data is under `~/.var/app/io.github.TheZwiss.backspace/`; updates replace the deployment, not that directory. Never use `flatpak uninstall --delete-data` unless you intend to erase it. Application-level schema migrations may make older versions incompatible with newer data, so back up that directory before downgrading.

## Troubleshooting and rollback

- **New release not synced:** inspect the sync log for “Waiting for upstream Flatpak metadata.” Wait for upstream's release-paired metadata to land; do not change just the source commit.
- **Build failure:** inspect the architecture's full GitHub Actions log and `diagnostics-x86_64` / `diagnostics-aarch64` artifacts (14-day retention, subject to repository policy). Successful unsigned build archives last seven days. Existing publication is unchanged. Use Run workflow to retry; leave `force_rebuild` unchecked unless intentionally rebuilding a successful version. Clear only disposable caches if needed.
- **Deployment failure:** check that Pages uses Actions, `github-pages` permits `main`, and the workflow has Pages/OIDC permissions. Rerun normally; a saved candidate is restored without recompilation. Do not delete `repository-state` to recover.
- **Signing failure:** check both secrets, full fingerprint, and that the exported secret key has no passphrase and has not expired/revoked. Do not create a different key to fix a failed build.
- **Remote/catalog problem:** check `flatpak remotes --user --show-details`, `flatpak remote-ls --user backspace`, and `flatpak update --user --appstream`. Verify that `backspace.flatpakrepo` points at the site's `/repo/` directory, not the site root. Compare its key with `backspace.gpg` and the maintainer's independently published fingerprint.
- **Missing snapshot/state:** restore the state branch from its Git history/backups and retain its referenced Release asset. The state history records earlier archive names and checksums. Never treat missing durable storage as an empty cache. If the Release/branch were deleted, recover from an independently saved snapshot before enabling publication.
- **Source/API download failure:** retry the workflow. API authentication uses the repository token in CI. Locally set `GH_TOKEN` to an appropriate read token if anonymous GitHub requests are rate-limited.

Rollback a client to any retained signed application commit:

```sh
flatpak remote-info --user --log backspace io.github.TheZwiss.backspace
flatpak kill io.github.TheZwiss.backspace  # if running
cp -a ~/.var/app/io.github.TheZwiss.backspace ~/backspace-data-backup
flatpak update --user --commit=COMMIT_FROM_LOG io.github.TheZwiss.backspace
flatpak mask --user io.github.TheZwiss.backspace
```

The mask holds the rollback against automatic updates. Resume normal updates with:

```sh
flatpak mask --user --remove io.github.TheZwiss.backspace
flatpak update --user io.github.TheZwiss.backspace
```

For a repository-wide emergency, disable `Sync and publish Backspace` in Actions and redeploy a previous complete `github-pages` artifact from a successful run while it is retained, or restore its complete Release snapshot as a Pages artifact. Do not restore only refs or delete objects. Keep automatic publication disabled until the upstream problem is resolved. When resuming after an external redeployment, dispatch with `force_rebuild` checked to reconcile publication state. Client rollback is independent of the next upstream synchronization.

## Local validation and builds

On Debian/Ubuntu:

```sh
sudo apt-get install flatpak flatpak-builder ostree gnupg shellcheck python3-venv
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.txt
python3 scripts/validate.py
flatpak-builder --show-manifest io.github.TheZwiss.backspace.yml >/dev/null
python3 -m unittest discover -s tests -v
shellcheck scripts/*.sh
# With Go installed:
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
```

Tests cover stable/annotated release resolution, idempotency, waiting for metadata, failed downloads, checksum/path checks, archive safety, missing durable state, deployment retry decisions, and the descriptor/size guard. When Flatpak, OSTree and GPG are available, an isolated two-architecture fixture repository exercises real signing, HTTP pulls, installation, update, snapshot restore, parent history, rollback and data retention. The fixture does not run Electron or a desktop session.

Build the real upstream application locally with its unchanged offline inputs:

```sh
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --install-deps-from=flathub --default-branch=stable \
  --disable-updates --disable-rofiles-fuse --force-clean --repo=repo \
  _build io.github.TheZwiss.backspace.yml
```

The current initial snapshot is `v1.11.0`; exact source/provenance is in `upstream.lock.json`. See [VALIDATION.md](VALIDATION.md) for checks actually run during implementation and remaining environment-dependent checks. No production key, GitHub repository, Pages deployment, or user installation is created by the local tests.

## Files and upstream attribution

- `.github/workflows/sync.yml`: polling, trusted synchronization, publication serialization.
- `.github/workflows/build.yml`: input gating, native builds, isolated signing, snapshots and Pages deployment.
- `.github/workflows/check.yml`: read-only PR checks.
- `scripts/`: synchronization, validation, durable state, descriptor generation and client smoke checks.
- `tests/test_automation.py`: failure and lifecycle coverage.
- Root manifest, four `flatpak/` resources and `upstream.lock.json`: release-paired upstream packaging and provenance.
- `requirements.txt`: hash-locked Python YAML dependency.
- `README.md`, `VALIDATION.md`, `LICENSE`, `.gitignore`: setup, executed-check records, licensing and generated-file exclusions.

The copied application/packaging resources retain upstream's AGPL-3.0 licensing; `LICENSE` contains the upstream license text. Packaging automation in this repository is distributed under AGPL-3.0-or-later. Flatter is referenced as an action, not copied or forked. Source links and immutable revisions remain in the manifest/lock so the corresponding application sources can be obtained from upstream.
