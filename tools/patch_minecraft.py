#!/usr/bin/env python3
"""Patch Minecraft 1.26.50.4 ARM64 to load Nova from inside the game process.

The patch changes the manifest Application class and appends a new DEX. Existing
Minecraft classes*.dex entries are preserved byte-for-byte (checked after Apktool).
No cross-process injector, root, or launcher process is used.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
TARGET_PACKAGE = "com.mojang.minecraftpe"
TARGET_VERSION = "1.26.50.4"
BOOTSTRAP_APPLICATION = "com.nova.standalone.NovaBootstrapApplication"
BOOTSTRAP_DEX_CLASSES = (
    b"Lcom/nova/standalone/NovaBootstrapApplication;",
    b"Lcom/nova/standalone/NativeBootstrap;",
)
NATIVE_LIB_PATH = "lib/arm64-v8a/libnova_core.so"


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required tool '{name}' was not found on PATH")
    return path


def check_elf_arm64(path: Path, label: str) -> None:
    raw = path.read_bytes()[:20]
    if len(raw) < 20 or raw[:4] != b"\x7fELF":
        raise RuntimeError(f"{label} is not an ELF file: {path}")
    if raw[4] != 2 or raw[5] != 1:
        raise RuntimeError(f"{label} must be ELF64 little-endian (ARM64 Android)")
    machine = struct.unpack_from("<H", raw, 18)[0]
    if machine != 183:
        raise RuntimeError(f"{label} is not AArch64 (ELF e_machine={machine})")


def is_split_bundle(apk: Path) -> bool:
    try:
        with zipfile.ZipFile(apk) as zf:
            names = zf.namelist()
            return any(n.lower().endswith(".apk") for n in names) and "AndroidManifest.xml" not in names
    except zipfile.BadZipFile:
        return False


def find_package(manifest: Path) -> str:
    root = ET.parse(manifest).getroot()
    package = root.attrib.get("package", "")
    if not package:
        raise RuntimeError("Could not read package attribute from decoded AndroidManifest.xml")
    return package


def clone_manifest_package(manifest: Path, suffix: str) -> tuple[str, str]:
    """Rename package metadata so the original app can remain installed alongside the test clone."""
    tree = ET.parse(manifest)
    root = tree.getroot()
    old = root.attrib.get("package", "")
    if not old:
        raise RuntimeError("Manifest does not have a package name")
    if not suffix:
        return old, old
    if not re.fullmatch(r"(?:\.[A-Za-z][A-Za-z0-9_]*)+", suffix):
        raise RuntimeError("Package suffix must look like '.nova' or '.nova.dev'")
    new = old + suffix
    root.set("package", new)

    component_tags = {
        "application", "activity", "activity-alias", "service", "receiver", "provider",
    }
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag in component_tags:
            for key in (ANDROID + "name", ANDROID + "targetActivity"):
                value = elem.attrib.get(key)
                if not value or value.startswith("android.") or value.startswith("java."):
                    continue
                # Keep Java component class names unchanged; they identify DEX classes,
                # not the installed package ID.
                if value.startswith("."):
                    elem.set(key, old + value)
                elif "." not in value:
                    elem.set(key, old + "." + value)
        authority_key = ANDROID + "authorities"
        authorities = elem.attrib.get(authority_key)
        if authorities:
            rewritten = []
            for item in authorities.split(";"):
                if item == old or item.startswith(old + "."):
                    item = new + item[len(old):]
                rewritten.append(item)
            elem.set(authority_key, ";".join(rewritten))

        if tag in {"permission", "permission-tree", "permission-group", "uses-permission", "uses-permission-sdk-23", "uses-permission-sdk-m"}:
            name_key = ANDROID + "name"
            name = elem.attrib.get(name_key, "")
            if name == old or name.startswith(old + "."):
                elem.set(name_key, new + name[len(old):])
        for attr_name in ("permission", "readPermission", "writePermission"):
            key = ANDROID + attr_name
            value = elem.attrib.get(key, "")
            if value == old or value.startswith(old + "."):
                elem.set(key, new + value[len(old):])
        affinity_key = ANDROID + "taskAffinity"
        affinity = elem.attrib.get(affinity_key, "")
        if affinity == old or affinity.startswith(old + "."):
            elem.set(affinity_key, new + affinity[len(old):])

    ET.register_namespace("android", ANDROID_NS)
    tree.write(manifest, encoding="utf-8", xml_declaration=True)
    return old, new


def set_bootstrap_application(manifest: Path) -> None:
    """Set the game's standard Application to a subclass that loads Nova in-process."""
    tree = ET.parse(manifest)
    root = tree.getroot()
    app = root.find("application")
    if app is None:
        raise RuntimeError("AndroidManifest.xml has no application element")
    name_key = ANDROID + "name"
    current = app.attrib.get(name_key, "")
    if current not in ("", "android.app.Application"):
        raise RuntimeError(
            f"Unexpected Application class {current!r}; refusing to replace an app-specific Application blindly"
        )
    process = app.attrib.get(ANDROID + "process", "")
    if process:
        raise RuntimeError(f"Application has custom android:process={process!r}; this patch expects the default game process")
    app.set(name_key, BOOTSTRAP_APPLICATION)
    ET.register_namespace("android", ANDROID_NS)
    tree.write(manifest, encoding="utf-8", xml_declaration=True)


def get_dex_entries(names: list[str] | set[str]) -> list[str]:
    return sorted(
        (name for name in names if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name)),
        key=lambda name: 1 if name == "classes.dex" else int(name[len("classes"):-len(".dex")]),
    )


def next_dex_name(names: list[str] | set[str]) -> str:
    dex_entries = get_dex_entries(names)
    indexes = [1 if name == "classes.dex" else int(name[len("classes"):-len(".dex")]) for name in dex_entries]
    next_index = max(indexes, default=0) + 1
    return "classes.dex" if next_index == 1 else f"classes{next_index}.dex"


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_bootstrap_dex(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 112 or not data.startswith(b"dex\n"):
        raise RuntimeError("Bootstrap input is not a valid DEX file")
    missing = [descriptor.decode("ascii") for descriptor in BOOTSTRAP_DEX_CLASSES if descriptor not in data]
    if missing:
        raise RuntimeError("Bootstrap DEX is missing required class descriptor(s): " + ", ".join(missing))


def append_game_side_payload(
    unsigned_apk: Path,
    bootstrap_dex: Path,
    native_lib: Path,
    bootstrap_entry: str,
    original_dex_hashes: dict[str, str],
) -> None:
    """Append the new DEX and .so without rebuilding or recompressing the other ZIP entries."""
    with zipfile.ZipFile(unsigned_apk, "a", compression=zipfile.ZIP_STORED) as zf:
        rebuilt_names = set(zf.namelist())
        for name, expected_hash in original_dex_hashes.items():
            if name not in rebuilt_names or file_sha256(zf.read(name)) != expected_hash:
                raise RuntimeError(f"Apktool changed or dropped original DEX {name}; refusing to continue")
        if bootstrap_entry in rebuilt_names:
            raise RuntimeError(f"Rebuilt APK already contains {bootstrap_entry}; refusing a duplicate DEX entry")
        if NATIVE_LIB_PATH in rebuilt_names:
            raise RuntimeError(f"Rebuilt APK unexpectedly already contains {NATIVE_LIB_PATH}")
        zf.write(bootstrap_dex, arcname=bootstrap_entry, compress_type=zipfile.ZIP_STORED)
        zf.write(native_lib, arcname=NATIVE_LIB_PATH, compress_type=zipfile.ZIP_STORED)

    with zipfile.ZipFile(unsigned_apk) as zf:
        final_names = set(zf.namelist())
        if NATIVE_LIB_PATH not in final_names or bootstrap_entry not in final_names:
            raise RuntimeError("Unsigned APK is missing the bootstrap DEX or libnova_core.so")
        for name, expected_hash in original_dex_hashes.items():
            if name not in final_names or file_sha256(zf.read(name)) != expected_hash:
                raise RuntimeError(f"Original DEX bytes changed after adding new entries: {name}")


def inspect_target(apk: Path) -> str:
    inspector = Path(__file__).with_name("inspect_minecraft_apk.py")
    if not inspector.is_file():
        raise RuntimeError(f"Missing static verifier: {inspector}")
    result = subprocess.run(
        [sys.executable, str(inspector), "--apk", str(apk)],
        check=False, capture_output=True, text=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        raise RuntimeError("Target APK static verification failed: " + (result.stderr.strip() or "see verifier output"))
    output = result.stdout
    if f"Package: {TARGET_PACKAGE}" not in output or f"Version: {TARGET_VERSION}" not in output:
        raise RuntimeError(f"The hooks target {TARGET_PACKAGE} {TARGET_VERSION}; refusing to patch a different package/version")
    if "matching invoke-super=yes" not in output:
        raise RuntimeError("Could not prove the target Activity has the expected onCreate superclass call")

    # Verify against the actual APK ELF, not only the supplied disassembly excerpt.
    signature_verifier = Path(__file__).with_name("verify_signatures.py")
    signature_result = subprocess.run(
        [sys.executable, str(signature_verifier), "--apk", str(apk)],
        check=False, capture_output=True, text=True,
    )
    if signature_result.stdout:
        print(signature_result.stdout, end="" if signature_result.stdout.endswith("\n") else "\n")
    if signature_result.returncode != 0:
        raise RuntimeError(
            "Target ELF signature verification failed: "
            + (signature_result.stderr.strip() or "see verifier output")
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path, help="Single complete Minecraft 1.26.50.4 ARM64 APK")
    parser.add_argument("--native-lib", required=True, type=Path, help="Built libnova_core.so (ELF64 AArch64)")
    parser.add_argument("--bootstrap-dex", required=True, type=Path, help="D8-compiled bootstrap DEX from GitHub Actions")
    parser.add_argument("--out", required=True, type=Path, help="Patched and signed APK output path")
    parser.add_argument("--keep-package", action="store_true", help="Keep the original package ID; the signed store app must be uninstalled first")
    parser.add_argument("--package-suffix", default=".nova", help="Clone package suffix; default .nova to keep the original install")
    parser.add_argument("--work-dir", type=Path, help="Temporary workspace; default is the shell's temp directory")
    parser.add_argument("--keystore", type=Path, help="Persistent signing key; default is ~/.nova/nova-debug.keystore")
    parser.add_argument("--keep-work", action="store_true", help="Keep the large decoded workspace for debugging")
    args = parser.parse_args()

    apk = args.apk.expanduser().resolve()
    core = args.native_lib.expanduser().resolve()
    bootstrap_dex = args.bootstrap_dex.expanduser().resolve()
    out = args.out.expanduser().resolve()
    for path, label in ((apk, "Minecraft APK"), (core, "native core"), (bootstrap_dex, "bootstrap DEX")):
        if not path.is_file():
            raise RuntimeError(f"{label} does not exist: {path}")
    if out in (apk, core, bootstrap_dex):
        raise RuntimeError("Output path must not overwrite the input APK, native library, or bootstrap DEX")
    if is_split_bundle(apk):
        raise RuntimeError("Input is an .apks/split bundle, not one complete APK")
    check_elf_arm64(core, "libnova_core.so")
    check_bootstrap_dex(bootstrap_dex)
    inspect_target(apk)

    with zipfile.ZipFile(apk) as zf:
        original_names = set(zf.namelist())
        original_dex = {
            name: file_sha256(zf.read(name)) for name in get_dex_entries(original_names)
        }
        if NATIVE_LIB_PATH in original_names:
            raise RuntimeError(f"Input APK unexpectedly already contains {NATIVE_LIB_PATH}")
        if "AndroidManifest.xml" not in original_names or "lib/arm64-v8a/libminecraftpe.so" not in original_names:
            raise RuntimeError("Input APK is missing the manifest or target ARM64 engine library")
    bootstrap_entry = next_dex_name(original_names)
    print("Original DEX files will be preserved and the bootstrap will be added as " + bootstrap_entry)

    apktool = require_tool("apktool")
    aapt2 = require_tool("aapt2")
    zipalign = require_tool("zipalign")
    apksigner = require_tool("apksigner")
    keytool = require_tool("keytool")
    require_tool("java")

    out.parent.mkdir(parents=True, exist_ok=True)
    work_parent = args.work_dir.expanduser().resolve() if args.work_dir else Path(tempfile.gettempdir()).resolve()
    work_parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="nova-apkpatch-", dir=work_parent))
    decoded = temp / "decoded"
    unsigned = temp / "unsigned.apk"
    aligned = temp / "aligned.apk"
    keystore = args.keystore.expanduser().resolve() if args.keystore else (Path.home() / ".nova" / "nova-debug.keystore")
    try:
        # --no-src is important: this patch only adds a fresh DEX and must not reassemble
        # Minecraft's original classes*.dex bytecode. --no-res leaves resources.arsc intact.
        run([apktool, "d", "--no-src", "--no-res", "-f", str(apk), "-o", str(decoded)])
        manifest = decoded / "AndroidManifest.xml"
        old_pkg = find_package(manifest)
        if not args.keep_package:
            if not args.package_suffix:
                raise RuntimeError("Empty package suffix is unsafe; use --keep-package explicitly to retain package name")
            original, cloned = clone_manifest_package(manifest, args.package_suffix)
            print(f"Manifest package: {original} -> {cloned}")
        else:
            print(f"Keeping package {old_pkg}; installing requires removing the signed store app first")
        set_bootstrap_application(manifest)
        print(f"Configured game-side Application bootstrap: {BOOTSTRAP_APPLICATION}")

        # Keep the decoded project free of extracted DEX additions. The new DEX and native
        # library are appended to the unsigned ZIP after Apktool rebuilds the manifest.
        run([apktool, "b", "--use-aapt2", "-a", aapt2, str(decoded), "-o", str(unsigned)])

        append_game_side_payload(unsigned, bootstrap_dex, core, bootstrap_entry, original_dex)

        run([zipalign, "-p", "-f", "4", str(unsigned), str(aligned)])
        if not keystore.is_file():
            keystore.parent.mkdir(parents=True, exist_ok=True)
            run([
                keytool, "-genkeypair", "-noprompt", "-v", "-keystore", str(keystore),
                "-alias", "nova", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                "-storepass", "nova-local-only", "-keypass", "nova-local-only", "-dname", "CN=Nova Local Build",
            ])
        run([
            apksigner, "sign", "--ks", str(keystore), "--ks-key-alias", "nova",
            "--ks-pass", "pass:nova-local-only", "--key-pass", "pass:nova-local-only",
            "--v1-signing-enabled", "false", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
            "--out", str(out), str(aligned),
        ])
        run([apksigner, "verify", "--verbose", str(out)])
        run([zipalign, "-p", "-c", "-v", "4", str(out)])
        with zipfile.ZipFile(out) as zf:
            final_names = set(zf.namelist())
            if NATIVE_LIB_PATH not in final_names or bootstrap_entry not in final_names:
                raise RuntimeError("Final signed APK is missing the native core or bootstrap DEX")
            for name, expected_hash in original_dex.items():
                if file_sha256(zf.read(name)) != expected_hash:
                    raise RuntimeError(f"Final APK did not preserve original DEX bytes: {name}")
        print(f"SUCCESS: signed APK created at {out}")
        print(f"Game-side bootstrap DEX: {bootstrap_entry}")
        print("Original Minecraft DEX files verified byte-for-byte unchanged.")
        print("This confirms packaging and signatures only; it does not prove Minecraft starts or the native hooks execute.")
        if not args.keep_package:
            print("Clone package caveat: Microsoft/Xbox sign-in or licensing may fail under a renamed package; worlds are not copied to the clone.")
        return 0
    finally:
        if args.keep_work:
            print(f"Keeping work directory: {temp}")
        else:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, zipfile.BadZipFile, ET.ParseError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
