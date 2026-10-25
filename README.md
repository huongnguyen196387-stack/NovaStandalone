# Nova Client — Android ARM64 launcher + game-side bootstrap + culling hooks

Target: Android `arm64-v8a`; supplied signature metadata identifies Minecraft Bedrock `1.26.50.4` (build-id `afa62bd3385161f1facfe36aad58ac63998f46d2`).

## Implemented

- Android launcher UI and a native C++ shared library (`libnova_core.so`).
- Dobby integration configured in CMake.
- Runtime scanning of executable `PT_LOAD` segments in the *current process* for the two signatures in `reference/nova_client-0.1.0-arm64-v8a.levipack`.
- Exact-one-match guard for every culler target that is not already hooked. The installer refuses missing or ambiguous signatures rather than falling back to a hard-coded address.
- Two pass-through instrumentation hooks for `LevelCullerCachedBase` vtable slots 8 and 11 (`cullerQueryA` / `cullerQueryB`). They forward to the original function and count calls; they do **not** alter the result, and no FPS improvement is claimed.
- `NovaInitializeCullingHooks()` and `NovaGetCullingHookStatus()` exports for a compatible game-side loader to call. `JNI_OnLoad` makes a guarded initialization attempt.
- A CI verification step checks the fixed signature bytes against the supplied `culler_asm.txt` excerpt before building.

## Game-side loading path

The project now includes a game-side bootstrap path for the inspected single APK: `tools/game-bootstrap-src/.../NovaBootstrapApplication.java` is compiled into a new DEX, and `tools/patch_minecraft.py` builds a separate Nova game APK with package `com.mojang.minecraftpe.nova`. Its Application bootstrap loads `libnova_core.so` inside the game process and starts a bounded watcher that waits for `libminecraftpe.so` before attempting the hooks. The launcher is configured to open this patched Nova package, not the store-installed Minecraft package.

The original installed game can remain installed when using the default `.nova` package suffix. This does not bypass Minecraft licensing; the cloned package may have Microsoft/Xbox sign-in or entitlement limitations. The APK patcher refuses mismatched package/version/signatures and verifies that original DEX payloads are preserved. The bootstrap and hook path still require an actual Android build, APK patch/sign, install, and on-device runtime test before they can be called working.

## Evidence and limits

`tools/verify_signatures.py` confirms that the 40-byte `cullerQueryA` pattern and the 56-byte `cullerQueryB` pattern match the labelled function entries in the supplied disassembly (all fixed bytes match; 8 bytes in the second pattern are wildcards). This is an excerpt-level check, not proof that those signatures are unique in the whole original ELF. At runtime the installer scans executable load segments and requires exactly one match for each target still needing a hook. The original pack's metadata says these signatures were checked as unique for the target build, but this project independently verifies uniqueness only when running against the actual mapped game library.

The target methods return their predicate in `w0` and receive their first two machine-word arguments in `x0` and `x1` in the supplied disassembly. The code treats the second argument as opaque and uses a `bool` pass-through prototype. This prototype is consistent with the observed return behavior but still needs device validation against the exact installed binary.

## Build via GitHub Actions

Push this extracted project to a GitHub repository and run the `Build Nova Standalone + Game Bootstrap` workflow. It verifies signatures against the supplied disassembly, builds the launcher/native core, compiles the game-side bootstrap DEX, and uploads `NovaStandalone-debug-apk`, `NovaStandalone-native-core-arm64`, and `NovaStandalone-game-bootstrap-dex`. Build success has not been verified from this workspace. A successful build proves compilation only, not that the patched game installs, starts, or calls the hooks.

## Next step for an actual performance change

First load this library in the game's process and confirm both hook logs and call counters. Only then should the hook behavior be changed: the disassembly alone does not establish which predicate values can safely be overridden without breaking block visibility, world rendering, or gameplay.

## Patch the game APK on Termux

Download the three Actions artifacts and place the original single-file ARM64 Minecraft 1.26.50.4 APK, `libnova_core.so`, and `nova-bootstrap.dex` in accessible paths. Install the required Termux tools (`python`, `openjdk-21`, `apktool`, `aapt2`, `zipalign`, and `apksigner`) and run:

```sh
python tools/inspect_minecraft_apk.py --apk /path/to/minecraft-26-50-arm64-v8a.apk
python tools/patch_minecraft.py \
  --apk /path/to/minecraft-26-50-arm64-v8a.apk \
  --native-lib /path/to/libnova_core.so \
  --bootstrap-dex /path/to/nova-bootstrap.dex \
  --out /path/to/Nova-Minecraft-1.26.50.4.apk
```

Install the generated APK and the launcher APK. Open Nova Launcher and tap **PLAY MINECRAFT**. Check Android Logcat for `NovaBootstrap` and `NovaCulling`. Hook install messages alone are not enough: look for the `cullerQueryA hook was called` and `cullerQueryB hook was called` logs to confirm runtime calls.
