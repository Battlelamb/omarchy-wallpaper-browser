# Wallpaper Browser backend

## Operations and providers

Reads still images directly from `$HOME/Pictures/Wallpapers`, `$HOME/.config/omarchy/backgrounds`, and one collection-directory level under either; each safe stock/user theme's `backgrounds/`; and `$HOME/.local/state/omarchy/current/theme/backgrounds`. This includes Omarchy's per-theme user background overlays. No whole Pictures/home scan, arbitrary configured roots, recursive crawl, downloader, new wallpaper renderer, shuffler or timer. PNG/JPEG/WebP/BMP only; SVG, GIF/video, control-character names, symlinks, special/oversized files are excluded. Existing rotation is not disabled, paused or modified; it can replace this manual choice later.

Primary `apply` validates fresh full membership, reads bounded stable bytes, creates a content-addressed private snapshot, and checks that the selected source identity did not race. It passes only the frozen absolute snapshot path to existing `omarchy-theme-bg-set` (the underlying `omarchy theme bg set` provider). This intentionally differs from passing a mutable library path to a process that reopens it later. A shell ping must first return exact `ok` with no warnings. The persistent background link must match the snapshot.

The stock setter's `omarchy-shell -q` suppresses failure. Therefore a separate **non-quiet** `omarchy-shell background set <snapshot>` must return the inspected void-method's empty success response with normal exit and no stderr. Missing targets/functions, "Not ready" or other nonempty replies, silent no-op setters, nonzero and missing shell all fail closed. Preflight 3s, setter 5s, non-quiet ACK 3s; no background process is added.

**ACK limitation:** this confirms that the running shell dispatched its background setter, not that a renderer presented the desired pixels. Installed Background.qml exposes no path/ready/readback IPC. The result message states that limit. We do not fabricate renderer/frame confirmation from a link, a quiet exit, a log or fixture response. Actual renderer presentation remains unverified and requires a separately authorized nested native trial.

## Restore and snapshot storage

One-level Restore returns the previous safely recognized wallpaper **bytes**, not necessarily its original symlink spelling. Before applying, an old background is frozen only if it is fresh library membership or a verified plugin-owned snapshot. The receipt records selected ID, old frozen path and expected new path. Restore accepts the original selected ID or `restore:last`, refuses an external current-background path change, and validates **both the current and previous snapshots** with `remembered_image` (safe private path, content-addressed hash, recognized image). A matching current pathname alone does not authorize Restore: an in-place edit to its inode fails closed before shell preflight or either setter. On refusal the current link, edited bytes and ownership receipt remain unchanged. A valid Restore uses the same setter/ACK path. It removes only the receipt, never user library images. With no safely recognized previous background, Restore is not advertised.

Snapshots live directly under `$HOME/.local/state/omarchy-wallpaper-browser/image-<sha256>.<ext>` and remain durable while the shell uses them. Existing matching snapshots are reused; modified snapshots are rejected. New snapshots are bounded to 16 files / 256 MiB. At the limit actions fail with instructions for manual pruning; there is deliberately no automatic deletion of a possibly current/restore image. Keep the current link target and both paths named in restore.json when pruning. A failed preflight may leave a harmless retained snapshot. This is not atomic rollback of a renderer, an external rotator or unrelated theme effects.

## Verified result

Restore ownership fix R2: 32 backend cases pass using the current masked `scripts/run-tests.py --backend-only` runner. The named regression `test_restore_refuses_in_place_current_snapshot_edit` was first observed failing on the unfixed backend, then passing after the minimal fix. Immutable `release-output/restore-r2-{red,green}.{json,txt}` preserve that cycle; `release-output/restore-ownership-fix-result.json` records final source hashes, counts and limits. Earlier stock probes below are historical, not rerun or live integration evidence for this fix.

31 unittest cases passed in the final masked offline sandbox. Real installed stock data produced 98 local wallpaper rows in a read-only fake-HOME probe. Tests cover one-level collections, literal metacharacter filenames, full membership beyond display cap, stable/stale IDs, frozen source/read races, missing/unready/non-quiet shell acknowledgement, persistent no-op detection, quota, external-change/corrupted-snapshot restore refusal and real CLI round-trip via fixture shims. No real shell call or background setter was executed.

## Contract and safety

System Python 3.11+ / standard library only; tested with installed Python 3.14.8. No cross-plugin imports, new packages, framework, daemon, network, installer, or autostart. Runtime paths do not depend on cwd. The UI calls:

```
/usr/bin/python3 runtime/0.1.0/backend.py catalog
/usr/bin/python3 runtime/0.1.0/backend.py action --id ID --verb VERB --apply
```

Catalog returns one JSON object with bounded unique rows, actions and actionLabel. Explicit actions return `{ok,message}` and optional `path`; CLI exits nonzero for any unconfirmed/partial result. No action is performed without `--apply`. Unknown/stale IDs are rejected against fresh bounded membership. `restore:last` is also accepted only when a matching durable receipt exists; the generic UI should restore with the original selected ID. A different selected row is not authorization to restore the last operation.

All selected source paths come from fixed local roots, not QML-supplied paths. Directory traversal uses directory FDs and O_NOFOLLOW for every component; symlinked roots, files and ancestors are rejected. The sole deliberate symlink reader is Omarchy's known current-background link; its target is separately allowlisted and opened safely before use. No arbitrary root/config/template input or remote thumbnails. Local previews are original installed/user files, not bundled art. Filenames are bounded/plain text; control characters are excluded. Image admission checks file type, byte limit and signatures; PNG dimensions are additionally capped. Catalog image previews are not a general-purpose hostile-media sanitizer; Qt/codec rendering is outside the backend suite.

Limits: 1024 entries per scanned directory (an overfull directory is skipped, not partially trusted), 512 wallpaper roots/files, 32 MiB per image, 64 KiB metadata reads, 8192-byte restore receipt, 256 displayed rows, <=1 MiB CLI output. Wallpaper identity is a hash of literal absolute path plus device/inode/size/mtime/ctime, not display text. Replacement invalidates the ID. No arbitrary recursive disk crawl. Content changes during bounded reads are rejected. Process argv is literal, shell=False; stdout and stderr each capped at 64 KiB, hard deadline and process-group SIGKILL on interruption/overflow/timeout, including pipe-holding descendants after their parent exits. Provider stderr is summarized rather than dumping paths/private hook output to the UI.

Plugin-owned state is under `$HOME/.local/state/omarchy-<slug>/`, private modes with an exclusive nonblocking lock and atomic/fsynced receipts. Existing state symlinks and special-file locks are refused. This is not an isolation boundary against malicious programs running as the same Unix user, nor protection from a provider that intentionally daemonizes into another session. Existing Omarchy setters can have their own broad side effects.

## Repeatable offline verification

From this project's root, with TMPDIR pointing at the approved scratch directory:

```
TMPDIR=/home/leviathan/.hermes/cache/scratch env -u PYTHONPATH /usr/bin/python3 scripts/run-tests.py --backend-only
```

The runner uses bwrap --unshare-all --clearenv, read-only source/usr/system ImageMagick policy, empty stock fixture, fake HOME, private /run and /tmp, and no host sockets, display or network. It also masks actual theme/background/shell executables with refusal stubs. Every successful setter in tests is a fixture shim inside the box. Inside the cleared box, tests execute `/usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v`; the outer invocation explicitly clears PYTHONPATH too. The current runner streams results without creating receipts; this fix captured stdout/stderr and source hashes into new exclusive-create receipt files. Old receipts were not overwritten. Do not run unittest on the live session.

Feature RED/GREEN receipts and extra boundary-control receipts are retained under ignored release-output. An initial missing-method discovery error is retained alongside its corrected assertion-based RED. Runtime feature slices were tested before implementation; additional passing characterization controls were added for existing guards. The first final portable-runner attempt failed before tests because a destination was a symlink; canonical target deduplication fixed it without changing any test threshold. That failed receipt is preserved.

No GUI, compositor setters, native renderer pixels, live theme/background changes, installation, enablement, package installation, publication or push was exercised. Headless fixture shims establish adapter logic, not successful live desktop integration. A separate read-only stock catalog probe uses real installed theme/image data in an offline fake-HOME box with setters masked. See release-output/backend-result.json for exact counts, source hashes and receipt names. Source provenance is in data/source-ledger.json and NOTICE.backend.md.
