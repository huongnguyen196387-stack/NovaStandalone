# Static analysis of the supplied Minecraft APK

Inspection command:

```sh
python3 tools/inspect_minecraft_apk.py --apk /mnt/data/minecraft-26-50-arm64-v8a.apk
```

Observed output:

```text
PASS: one complete APK archive (not a split wrapper)
Package: com.mojang.minecraftpe
Version: 1.26.50.4
Registered target Activity: com.mojang.minecraftpe.MainActivity
Original Application class: android.app.Application
Application process: (default app process)
Native engine: lib/arm64-v8a/libminecraftpe.so (328,419,680 bytes)
classes3.dex: MainActivity.onCreate(Bundle) found; matching invoke-super=yes
NOTE: static compatibility only; this does not build the patched APK or prove hooks run at runtime.
```

The exact DEX contains `Lcom/mojang/minecraftpe/MainActivity;` with `onCreate(Landroid/os/Bundle;)V`, and its bytecode invokes a superclass `onCreate(Landroid/os/Bundle;)V`. No existing `MainActivity` bytecode is modified by the current integration path; the bootstrap is installed through the manifest's Application class instead.
