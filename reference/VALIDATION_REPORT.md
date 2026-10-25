# Nova game-side bootstrap and culling-hook validation report

Date: 2026-10-25

## Passed in this workspace

- `python3 tools/inspect_minecraft_apk.py --apk /mnt/data/minecraft-26-50-arm64-v8a.apk`: verifies a single complete APK, package `com.mojang.minecraftpe`, version `1.26.50.4`, registered `MainActivity`, default `android.app.Application`, default process, ARM64 `libminecraftpe.so`, and `MainActivity.onCreate(Bundle)` with a superclass `onCreate(Bundle)` call.
- `python3 tools/verify_signatures.py --apk /mnt/data/minecraft-26-50-arm64-v8a.apk`: `cullerQueryA` fixed 40/40 bytes match `Cached_slot8` at `0xb307358`; `cullerQueryB` has 48 fixed bytes matching `Cached_slot11` at `0xb307e30` and 8 wildcard bytes.
- The exact APK's ELF64 AArch64 `libminecraftpe.so` was scanned across executable file-backed `PT_LOAD` segments. Each signature has exactly one match, and each runtime virtual address equals the analyzed address: A at `0xb307358`, B at `0xb307e30`.
- `tools/patch_minecraft.py` now runs this exact-APK ELF/signature check before decoding or rebuilding the APK; a mismatch or ambiguous signature aborts the patch.
- `python3 tools/test_patch_minecraft.py`: package rewrite, Application class rewrite, DEX naming, and bootstrap DEX descriptor checks pass.
- Python syntax checks for the patcher, APK inspector, signature verifier and tests pass.
- The manifest integration design adds a new `classesN.dex` and the native library after the Apktool rebuild; the patcher checks that original DEX hashes remain unchanged.

## Not yet verified

- Android NDK/CMake compile/link of `libnova_core.so` and D8 compilation of `nova-bootstrap.dex` on GitHub Actions.
- Apktool rebuild, ZIP alignment, and signing of a patched 363 MB APK (the required Android/Apktool tooling is unavailable in this workspace).
- Installation and launch of the patched APK on a device.
- Successful Dobby installation and hook calls in the running game, stability, visual correctness, or FPS improvement. Static ELF uniqueness is now verified, but the runtime scanner and hook installation still require on-device validation.

The hooks remain pass-through instrumentation and are not claimed to improve FPS. Static signature matches in the supplied disassembly do not prove runtime hook execution.
