# Backend source and asset notice

The new Python backend/tests are original narrow adapters, distributed under this project's GPL-3.0-only license. Their common local safety routines are copied independently into each of these three projects; none imports another plugin or a shared installed runtime.

HyDE-Project/HyDE, pinned commit `fa2fd57afc7203b1b5653d3271f3a57ea3873f97`, is a behavior/design reference. The audited cached LICENSE is GNU GPL version 3. No HyDE shell/Lua/Rofi source, installer/init/eval, templates, art, fonts or datasets are redistributed here. Useful local-thumbnail/explicit-selection and palette-preview behavior was reimplemented through installed Omarchy/ImageMagick interfaces instead of running donor scripts. Wallbash target headers/postprocessing are explicitly excluded. The planned wallpaper/core.sh was not in the audited cached source subset and was not copied or executed.

Installed Omarchy source is read-only interface evidence, not vendored code. Its installed package declares MIT. ImageMagick is called as a system CLI under its installed ImageMagick license, not bundled or linked into this package. Matugen and Aether are not runtime dependencies; no code from them is copied. Installed package version/license fields and exact source SHA-256 values are recorded below and in runtime/0.4.1/data/source-ledger.json. The cache's source-tree metadata records the pinned commit; these hashes attest the exact bytes reviewed, not an independent signed/reproducible-build proof.

No third-party image/font/dataset assets ship with this backend. Runtime catalogues reference existing local installed/user files without copying them into the package. Their owners retain their own rights; a system theme's package license is not blanket permission to redistribute every wallpaper. Test color images are generated in the private fixture using stdlib PNG encoding (or converted with installed ImageMagick), not copied artwork. Wallpaper snapshots and palette exports are explicit local user operations, not bundled redistributions.

## Exact inspected sources

| Source | SHA-256 |
| --- | --- |
| `HyDE/LICENSE` | `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` |
| `HyDE/Configs/.local/lib/hyde/wallpaper/select.sh` | `4eca5c59865f5d503a21a118dd896e91c60693a55365214b219f3b6cb8974c0b` |
| `/usr/share/omarchy/bin/omarchy-theme-bg-set` | `865beafef7643dc4aac6f2eca06d68808c197d39fba7fedaba75892cfd050a1a` |
| `/usr/share/omarchy/bin/omarchy-shell` | `fd1ac699c5d83846c8eb530ead22792be61b9704f68e64caa63f7243d170fa24` |
| `/usr/share/omarchy/shell/plugins/background/Background.qml` | `bc0825a58c0cd01fe680c4628e1836e1cad74c7767941bb2e245b8394787f15c` |

Package metadata: Name            : omarchy / Version         : 4.5.0rc2-1 / Licenses        : MIT.
