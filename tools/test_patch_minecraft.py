#!/usr/bin/env python3
"""Focused tests for manifest rewrite, DEX naming, and bootstrap validation."""
from __future__ import annotations

import importlib.util
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).with_name("patch_minecraft.py")
spec = importlib.util.spec_from_file_location("nova_minecraft_patcher", SCRIPT)
assert spec and spec.loader
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
ANDROID = "{http://schemas.android.com/apk/res/android}"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nova-patcher-test-") as td:
        root = Path(td)
        manifest = root / "AndroidManifest.xml"
        manifest.write_text(
            '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.mojang.minecraftpe">
              <uses-permission android:name="com.mojang.minecraftpe.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>
              <application android:name="android.app.Application" android:label="Minecraft">
                <activity android:name="com.mojang.minecraftpe.MainActivity" android:taskAffinity="com.mojang.minecraftpe"/>
                <provider android:name="androidx.core.content.FileProvider" android:authorities="com.mojang.minecraftpe.provider"/>
              </application>
              <permission android:name="com.mojang.minecraftpe.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>
            </manifest>''',
            encoding="utf-8",
        )
        old, new = patcher.clone_manifest_package(manifest, ".nova")
        assert old == "com.mojang.minecraftpe"
        assert new == "com.mojang.minecraftpe.nova"
        patcher.set_bootstrap_application(manifest)
        document = ET.parse(manifest).getroot()
        assert document.get("package") == new
        application = document.find("./application")
        assert application is not None
        assert application.get(ANDROID + "name") == patcher.BOOTSTRAP_APPLICATION
        activity = document.find("./application/activity")
        assert activity is not None
        assert activity.get(ANDROID + "name") == "com.mojang.minecraftpe.MainActivity"
        assert activity.get(ANDROID + "taskAffinity") == new
        provider = document.find("./application/provider")
        assert provider is not None
        assert provider.get(ANDROID + "authorities") == new + ".provider"
        uses_permission = document.find("./uses-permission")
        assert uses_permission is not None
        assert uses_permission.get(ANDROID + "name") == new + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"

        dex_names = {"classes.dex", "classes2.dex", "classes3.dex", "classes4.dex", "classes5.dex"}
        assert patcher.next_dex_name(dex_names) == "classes6.dex"
        assert patcher.next_dex_name({"classes.dex", "classes2.dex", "classes10.dex"}) == "classes11.dex"
        assert patcher.next_dex_name(set()) == "classes.dex"

        fake_dex = root / "bootstrap.dex"
        fake_dex.write_bytes(
            b"dex\n039\0" + b"\0" * 104 + b"Lcom/nova/standalone/NovaBootstrapApplication;"
            + b"\0" + b"Lcom/nova/standalone/NativeBootstrap;"
        )
        patcher.check_bootstrap_dex(fake_dex)

        # Exercise ZIP append mode: existing DEX entries must remain byte-identical.
        fake_core = root / "libnova_core.so"
        fake_core.write_bytes(b"fake native core bytes for zip-unit-test")
        fake_apk = root / "unsigned.apk"
        original_classes = b"original dex placeholder"
        with zipfile.ZipFile(fake_apk, "w") as zf:
            zf.writestr("classes.dex", original_classes)
            zf.writestr("AndroidManifest.xml", b"manifest placeholder")
        original_hashes = {"classes.dex": patcher.file_sha256(original_classes)}
        patcher.append_game_side_payload(fake_apk, fake_dex, fake_core, "classes2.dex", original_hashes)
        with zipfile.ZipFile(fake_apk) as zf:
            assert zf.read("classes.dex") == original_classes
            assert zf.read("classes2.dex") == fake_dex.read_bytes()
            assert zf.read(patcher.NATIVE_LIB_PATH) == fake_core.read_bytes()

        fake_dex.write_bytes(b"not a dex")
        try:
            patcher.check_bootstrap_dex(fake_dex)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Invalid bootstrap DEX must be refused")

    print("PASS: package clone, Application bootstrap manifest, DEX naming/validation, ZIP payload append, original DEX preservation")


if __name__ == "__main__":
    main()
