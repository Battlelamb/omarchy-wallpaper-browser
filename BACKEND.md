# Wallpaper Browser backend — v0.4.1

## Exact CLI

From the plugin checkout (runtime paths are otherwise independent of cwd):

```sh
/usr/bin/python3 runtime/0.4.1/backend.py catalog
/usr/bin/python3 runtime/0.4.1/backend.py preferences
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"options":{"folders":["/absolute/user-owned/image-folder"],"fileType":"jpeg","aspect":"landscape","favoritesOnly":false,"collection":""}}'
/usr/bin/python3 runtime/0.4.1/backend.py action --id WALL-ID-FROM-CATALOG --verb favorite --apply
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"collections":{"Work":["WALL-ID-FROM-CATALOG"]},"options":{"collection":"Work"}}'
/usr/bin/python3 runtime/0.4.1/backend.py action --id WALL-ID-FROM-CATALOG --verb shuffle --apply
/usr/bin/python3 runtime/0.4.1/backend.py action --id WALL-ID-FROM-CATALOG --verb apply --apply
/usr/bin/python3 runtime/0.4.1/backend.py action --id restore:last --verb restore --apply
/usr/bin/python3 runtime/0.4.1/backend.py preferences --apply --payload '{"reset":true}'
```

Paths and IDs above are placeholders; use existing folders and exact current catalog IDs. **No Wallpaper Browser action accepts a payload.** `shuffle` still requires explicit `--apply` as command authorization, but it never invokes a desktop setter. It returns `selectedId`; applying that ID is a separate explicit action.

Commands are exactly `catalog`, `preferences`, `action`; unknown/abbreviated options fail. Catalog has no options. Preference reads have no options; writes require `--payload` and `--apply`, without `--id`/`--verb`. Actions need `--id`, `--verb`, `--apply`; no missing-apply action runs. JSON payloads are objects, ≤16384 UTF-8 bytes, rejecting duplicate keys, nonfinite constants, unknown fields/options, invalid types and excessive nesting. Responses are single JSON objects, ≤1 MiB. Usable catalog/read or confirmed writes exit0; failures exit nonzero, without private tracebacks.

## Schema and storage

| Option | Type | Default | Validation |
| --- | --- | --- | --- |
| `density` | enum | `comfortable` | `comfortable`, `compact` (native UI only) |
| `layout` | enum | `grid` | `grid`, `list` (native UI only) |
| `sortOrder` | enum | `default` | `default`, `name`, `favorites-first` (stable ordering before backend display cap) |
| `fileType` | enum | `all` | `all`, `png`, `jpeg`, `webp`, `bmp` |
| `aspect` | enum | `all` | `all`, `landscape`, `portrait`, `square`, `ultrawide`, `unknown` |
| `favoritesOnly` | boolean | false | JSON boolean only |
| `folders` | string-list | `[]` | ≤32 absolute existing user-owned directories, ≤4096 characters/path; no traversal, control characters or symlink components |
| `collection` | string | `""` | ≤256 characters; exact collection name; empty means all |

Preferences responses contain `{ok,preferences,settingsSchema,message}`. Document shape: `{version:1,favorites:[],recent:[],collections:{},options:{...}}`. Patches accept only `options`, `favorites`, `collections`; recent/version are not writable. The sole-key `{"reset":true}` resets only this preference document. Collections are named lists of inert wallpaper IDs: stale/unknown entries never authorize an action. A collections patch replaces the map; an options patch merges keys. Up to512 favorites,32 recents,64 named collections and512 IDs per collection; IDs are bounded/control-free ≤256 characters.

The independent sibling `preferences.py` validates bounded regular-file reads and saves through a private locked atomic store. Storage suffix is `omarchy/lego/wallpaper-browser/preferences.json` below absolute `XDG_CONFIG_HOME` or `$HOME/.config`. Corrupted/unsafe existing files are not silently overwritten. Reset does not touch original images, active wallpaper, snapshots or Restore journals. Confirmed Apply updates recent after helper acknowledgement; a later preference-storage error produces a warning, not a false desktop failure.

New preference writes are preflighted under the store lock against an aggregate 128 KiB compact UTF-8 document budget before replacement. Individual list/text maxima do not imply their Cartesian maximum is supported. Legacy valid stores remain bounded to 16 MiB for reads and explicit Reset/recovery; a rejected write leaves the existing file unchanged. Replies use the common UTF-8 encoder below 1 MiB (including the newline, at most 1 MiB); the UI action parser uses the same strict byte ceiling. Oversized optional preference snapshots on successful commands are omitted with a warning, preserving `ok:true`; actual catalogue overflow still fails closed, without truncating saved data. A Recent-save failure likewise warns without reversing confirmed helper/export acceptance.

## Catalogue and explicit selection

Default roots remain `$HOME/Pictures/Wallpapers`, `$HOME/.config/omarchy/backgrounds`, their immediate collection directories, installed stock/user theme backgrounds, and staged current-theme backgrounds. Explicitly saved user-owned folders add the same direct-plus-one-level scan, revalidated at each use. Unsafe/missing saved folders are skipped; no arbitrary recursive crawl, URL, network reader or executable path is introduced.

Rows retain `id`, `label`, `detail`, `preview`; add `favorite`, `category` and `collection`. `category` is an encoded-dimension aspect class. Up to65536 header bytes per image are read through a stable safe FD for PNG/JPEG/WebP/BMP dimensions. Square is exact width=height; ultrawide is width/height≥2; other width>height is landscape, otherwise portrait. Invalid/oversized/unlocated dimensions are unknown. Metadata orientation is not applied. Details include encoded width/height and a numeric ratio. This is bounded header inspection, not full media validation.

`fileType` is the lower-case validated local image-header kind (`png`, `jpeg`, `webp`, `bmp`), also exposed on each row. Existing suffix admission stays case-insensitive and accepts `.jpg`/`.jpeg`; both map to JPEG when their header is JPEG. Renamed admitted suffixes do not override the detected header. The stored enum deliberately accepts `jpeg`, not `jpg` or uppercase spellings. Type is read through a bounded no-follow stable FD (64 bytes); failures skip the row. This is existing header-level admission, not a new full decoder or web source.

Backend file-type/favorites/aspect/named-collection filters run **before** the256-row display cap. Named collection membership is checked independently of a row's first display collection label. Source IDs remain `wall:<first32-SHA256(literal-path + device/inode/size/mtime/ctime)>`; replacement or metadata changes invalidate an old selection/favorite. Fresh action membership uses all bounded512 files, not just displayed tiles.

Primary verb is `apply`. Secondary fixed verbs: `favorite`, `shuffle`, conditional `restore`. Favorite toggles only a fresh member. Shuffle validates the supplied current selection, chooses from the filtered displayed catalogue, avoids that ID if another result exists, and returns `{ok:true,selectedId,message}`. With one eligible row it selects that row; with none it fails. No provider process, desktop state, preference recent entry or Restore receipt is created by shuffle. The UI uses `selectedId` to reveal the chosen tile; transient search is not an input to backend shuffle.

## Apply and Restore invariants

Apply rereads selected/current admitted sources, freezes bounded content-addressed snapshots, rechecks source identity and uses snapshot paths, never mutable library paths, for the external setter. An unrelated racing source is skipped; a changed/unsafe selected or current admitted source refuses the action. The known Omarchy current-background link is the only deliberate symlink reader: its target is separately admitted; arbitrary symlink roots/files/ancestors are forbidden.

Success requires shell ping, persistent `omarchy-theme-bg-set` state readback and an acknowledged non-quiet `omarchy-shell background set`. Quiet best-effort setter success alone is not success. Helper timeouts/output overflow/nonzero/stderr/readback mismatches are failures with possible partial state, not fabricated confirmation. Stock IPC exposes acceptance, not a proof of displayed pixels. Existing wallpaper rotation is never reconfigured.

The one-level pending/active receipt is in `$HOME/.local/state/omarchy-wallpaper-browser/restore.json`. Restore accepts the matching original ID or `restore:last`, including after original sources vanish, and requires the actual current link and owned current/previous snapshot hashes to match. A different selected ID, external wallpaper choice or in-place snapshot edit is refused. Missing/unknown previous safe membership does not invent a snapshot. Successful Restore performs the same confirmed setter/dispatch path and clears the receipt.

Snapshots are limited to16 files /256 MiB total,32 MiB each; no automatic pruning or background quota growth occurs. Preserve active/Restore-owned files when deliberately pruning by hand. This is not a general history/cleanup service or a complete undo of external programs.

Common display keys are literal, independent enum options saved/reset by the existing helper. Defaults preserve earlier behavior; native UI applies density/layout and retains display ordering. Backend `sortOrder` runs after saved domain filters and before the 256-row cap within the existing 512-file membership: `default` preserves scan order, `name` compares lowercase labels in UTF-16 ordinal order (not locale/casefold), and `favorites-first` stably partitions favorites using bounded set membership. Equal keys preserve scan order. Transient search and native Favorites/Recent/collection views remain local to returned rows; they do not trigger a broader scan. These keys do not change source tokens, safe roots, fresh action membership, Restore, store budgets or wire framing. Existing stores that omit the new keys receive defaults in memory, without a read-time migration write.

## Bounds and evidence

At most1024 entries/directory (overfull directories skipped),512 roots/files,256 filtered rows,32 MiB/image,64 KiB ordinary metadata reads and8192-byte receipts. Directory FDs and O_NOFOLLOW protect every traversed component. Filenames and labels are bounded plain text. Image signature/PNG dimension guards remain; Qt/codec hostile-media rendering is outside this backend suite.

Subprocess argv is literal with no shell, evaluation or arbitrary template execution. Stdout/stderr each cap64 KiB; deadlines and process-group termination cover overflow, cancellation and pipe-holding descendants. State uses an exclusive private regular-file lock and atomic/fsynced writes. Same-UID malicious writers or deliberately escaped process sessions are outside that guarantee.

Only run tests through the shipped offline runner, with existing private `TMPDIR`:

```sh
env -u PYTHONPATH /usr/bin/python3 scripts/run-tests.py --backend-only
```

It clears HOME/env, masks production setters, uses offline bwrap with no host display/compositor sockets and controlled fixture providers. The shipped `tests/test_v040.py` covers common enum persistence/rejection/Reset and the new domain behavior; existing backend, v0.3 and store regressions remain. Historical v0.3 receipts, when present under `release-output/v030-tdd/` and `release-output/v030-result.json`, are not v0.4 verification. No GUI, live wallpaper changes, hosted CI, installation or publication is implied. Source/asset rights are in NOTICE.backend.md and `runtime/0.4.1/data/source-ledger.json`; no artwork is bundled.
