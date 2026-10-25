# Game-side loader integration

## Mechanism

The target Minecraft APK manifest uses the framework's standard `android.app.Application` and has no custom `android:process`. The patch replaces that Application class with `com.nova.standalone.NovaBootstrapApplication`, whose `onCreate()` calls `super.onCreate()` and then loads `libnova_core.so` and starts the bounded native watcher. This runs within the Minecraft app process. It does not use the separate Nova launcher as a process injector.

The Java bootstrap is compiled by GitHub Actions into `nova-bootstrap.dex`. The patcher appends that as the next available DEX file (for the inspected target, `classes6.dex`) and adds `lib/arm64-v8a/libnova_core.so`. Existing DEX files are intentionally not disassembled: `apktool d --no-src --no-res` keeps them raw, and the patcher compares each original DEX SHA-256 after rebuild and again before success.

## Why the patch avoids editing existing DEX

The bootstrap requires no existing Minecraft method to be altered. Keeping every original `classes*.dex` entry byte-for-byte unchanged removes the need to reassemble Mojang's original code and makes the change smaller and easier to audit. Only the manifest, a new DEX, and the Nova native library are added/changed.

## Build and patch

1. Run `.github/workflows/build.yml` in GitHub Actions.
2. Download artifacts `NovaStandalone-native-core-arm64` and `NovaStandalone-game-bootstrap-dex`.
3. On Termux, install `python`, `apktool`, `aapt2`, `zipalign`, `apksigner`, and `openjdk-21`.
4. Run `tools/inspect_minecraft_apk.py` on the original single APK.
5. Run `tools/patch_minecraft.py` with `--apk`, `--native-lib`, `--bootstrap-dex`, and `--out`.

The script uses Termux's native `aapt2` explicitly (`apktool b --use-aapt2 -a "$(command -v aapt2)"`), page-aligns the built APK, signs it with a persistent local key, verifies the signature/alignment, and refuses success if the original DEX hashes changed.

## Limitations

- The actual native core build, APK rebuild, install, and runtime hooks have not yet been validated in this environment.
- The target is specifically Minecraft Bedrock 1.26.50.4 ARM64; signatures must not be reused for another build without re-analysis.
- The target function prototypes and hook safety need on-device verification.
- Renaming the package may break Microsoft/Xbox sign-in and licensing; keeping the package requires uninstalling the store-signed app first and risks private app data.
- Both culling hooks are currently pass-through instrumentation only; no FPS gain is claimed.
