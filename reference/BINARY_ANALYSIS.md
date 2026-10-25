# Binary analysis notes from supplied files

## Nova ARM64 `libnova_client.so`

`file` reports ELF64 AArch64, Android API 28, stripped. `nm -D -C` reported undefined symbols for Levi/preloader APIs, including `pl::memory::hook`, `pl::memory::unhook`, `pl::memory::resolveSignature`, input callback registration, mod menu drawing and registration, and `pl::mod::NativeMod::current()`. The binary has `PLGetModRegistration`, indicating it was packaged as a preloader-native mod.

## Flarial 1.4.21 APK set

The supplied APKS contains `base.apk` and `split_config.arm64_v8a.apk`. The ARM64 split contains `libflarialclient.so` (~12.2 MB), with Android/EGL/GLES dependencies and `JNI_OnLoad`. This proves the APK ships a native client library, not that the private client implementation can be copied into Nova. It is used only as a structural reference.

## Target Minecraft APK

See `GAME_APK_ANALYSIS.md`. The supplied single APK reports version 1.26.50.4, includes `lib/arm64-v8a/libminecraftpe.so`, and uses the standard framework `android.app.Application` class with the default process. The new game-side bootstrap swaps in a subclass of `Application` and leaves all existing DEX files byte-for-byte unchanged.

## Culling signatures

The supplied disassembly includes `Cached_slot8 @ 0xb307358` and `Cached_slot11 @ 0xb307e30`. Fixed bytes in the ARM64 LeviPack signatures match those disassembly entries. Runtime scanning searches executable load segments of the live `libminecraftpe.so` and refuses missing or ambiguous matches. A disassembly excerpt does not establish uniqueness in the complete ELF.
