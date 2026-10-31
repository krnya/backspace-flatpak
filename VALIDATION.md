# Implementation validation

Checked on 2026-11-01. This records executed checks separately from behavior that still needs the first production run.

## Executed and passed

- **Workflow validation:** Actionlint 1.7.12 checked all three workflow files, including expressions, job dependencies, reusable-workflow inputs/secrets and shell snippets. ShellCheck 0.10.0 checked `scripts/publish.sh`. Python 3.13 compiled every script and test file.
- **Manifest parsing:** Flatpak Builder 1.4.4 successfully resolved the complete manifest with `--show-manifest`. The custom validator checked application/AppStream IDs, the immutable source pin, every local resource path, resource SHA256 provenance, HTTPS sources, and upstream SHA256/SHA512 download declarations. The manifest and all four required local resources match the immutable upstream metadata revision byte for byte.
- **Stable release identity:** inspected upstream's official latest release `v1.11.0`, its annotated tag and peeled source commit `cfa6ce43306f829f1c273f6402ef625362b793da`. The packaging snapshot is `2885a7d35c0e9188eb337ab238df992073e68df5`; all resources were fetched at that immutable revision. GitHub anonymous API rate limiting interrupted a subsequent live sync attempt, so initial population used the already inspected release identities and the downloaded immutable resources through the same synchronization code. Authenticated CI supplies `GH_TOKEN`.
- **Flatter compatibility:** inspected and compared `action.yml`, `src/main.js` and `src/flatter.js` at the exact action pin `ba9d3856bb0e59b43ba4684fe72128003f1b7706`. All match the inspected current implementation. Confirmed `files`, `arch`, empty `cache-key`, `flatpak-builder-args`, and disabled bundle/Pages outputs are supported. The action builds into `repo/`; cache disabling also skips Flatter's metadata update, so the separate publication step explicitly generates and signs metadata.
- **Builder image:** fetched its registry index, verified the SHA256 digest against the workflow pin, and confirmed Linux `amd64` and `arm64` variants are present.
- **Automated tests:** `python3 -m unittest discover -s tests -v` passed **16 tests** using Flatpak 1.16.6, OSTree 2025.2 and temporary GPG keys/installations. No existing application installations or data were changed.

The 16 tests cover:

1. Current manifest/resource validation, permissions, and upstream updater-file removal.
2. Identical synchronization and ignoring unrelated upstream commits.
3. Waiting for release metadata and refusing an unpaired initial release.
4. Prerelease rejection and annotated tag resolution.
5. Download failure before any local packaging change.
6. Corrupted offline-dependency checksum rejection.
7. Missing local build-resource rejection.
8. Skipping published inputs and retrying a pending deployment without rebuilding, including forced rebuilds with the same input digest.
9. Missing durable state failing closed when a snapshot release already exists.
10. Snapshot digest failure before extraction.
11. Recovery from an interrupted first-publication initialization record.
12. Failed snapshot upload preserving the previous durable pointer.
13. Accidental signing-key replacement rejection.
14. Archive traversal/symlink rejection.
15. Descriptor contents, embedded key agreement, required Pages files, and the size guard.
16. A real two-architecture signed OSTree lifecycle: creating two successive versions, restoring the complete repository snapshot between them, adding the generated `.flatpakrepo` through Flatpak's parser, HTTP signature/object verification for both app and AppStream refs, native catalog refresh, installation, update, rollback, preservation of parent history and a user-data marker, and rejection after summary tampering.

The lifecycle test builds a tiny synthetic application with the Backspace app ID and valid Flatpak metadata. ARM64 objects/catalogs are verified on x86_64; no ARM64 executable is run. This tests the distribution mechanism without claiming to test Electron or a graphical desktop.

## Actual Backspace build attempted

The downloaded Flatpak Builder was run against the real unchanged manifest in `.local-build/`, using a separate home, XDG directories, Flatpak installation and builder state. Build isolation was successfully probed. Dependency installation succeeded, including the Electron 25.08 BaseApp and Node 24 SDK extension. The pinned application Git source was cloned, and several offline source archives were downloaded with their declared checksums.

The build stopped during the Electron `v43.7.5` archive download:

```text
Failed to download sources: module backspace: Operation too slow.
Less than 10000 bytes/sec transferred the last 60 seconds
```

It did **not** reach application compilation/export. The local attempt log is retained at `.local-build/build.log` (ignored by Git). No unsigned or incomplete result was published. This is a network-limited build attempt, not evidence that the complete Backspace package builds successfully here.

## Not executed here

- Native x86_64 or aarch64 full Backspace builds to completion in GitHub Actions.
- The pinned builder container/Flatter action running on a hosted runner. Its inputs and repository output contract were inspected; CI still needs to exercise the complete integration.
- Actual GitHub Release asset writes, Git ref updates, OIDC Pages deployment or hosted rollback. No repository URL, authenticated GitHub CLI session or production signing secrets were supplied. The durable-state and failure decisions have local tests; GitHub's actual storage/deployment services have not been exercised.
- Launching the real Backspace Flatpak, KDE Discover UI, Wayland screen capture, PipeWire, notifications, microphone/camera, shortcuts or application-specific data migrations. Upstream code, updater handling, app ID and sandbox permissions are preserved. A portal-capable desktop is required for those acceptance checks.
- Bit-for-bit reproducibility across different runtime/SDK revisions, unlimited GitHub Pages history, or a changed upstream packaging layout. Versioned SDK branches can evolve; unsupported resource layouts or publication size limits fail visibly.

## First production acceptance check

After following the README setup, dispatch `sync.yml` on `main`. Confirm both native builds, repository preparation, Pages deployment and acknowledgement are green. Then add the generated `.flatpakrepo`, install on a desktop, check audio/notifications/screen sharing, write settings, update to the next published release and confirm the settings persist. Inspect `flatpak remote-info --log` for the previous signed commit and test rollback after backing up application data. A second unchanged dispatch should run synchronization/validation/planning and skip builds and deployment.
