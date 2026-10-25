#!/usr/bin/env python3
"""Inspect a single Minecraft ARM64 APK before applying the Nova game-side loader patch.

This is a static compatibility check. It never modifies the APK and does not
claim that native hooks execute successfully at runtime.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path

ANDROID_NS = "http://schemas.android.com/apk/res/android"
TARGET_ACTIVITY = "com.mojang.minecraftpe.MainActivity"
TARGET_METHOD_PARAMS = ("Landroid/os/Bundle;",)


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def read_uleb(data: bytes, off: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if off >= len(data) or shift >= 35:
            raise ValueError("Malformed ULEB128 in DEX")
        byte = data[off]
        off += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, off
        shift += 7


def read_length8(data: bytes, off: int) -> tuple[int, int]:
    first = data[off]
    if first & 0x80:
        return ((first & 0x7F) << 8) | data[off + 1], off + 2
    return first, off + 1


def read_length16(data: bytes, off: int) -> tuple[int, int]:
    first = u16(data, off)
    if first & 0x8000:
        return ((first & 0x7FFF) << 16) | u16(data, off + 2), off + 4
    return first, off + 2


def parse_string_pool(data: bytes, pos: int, size: int) -> list[str]:
    if u16(data, pos) != 0x0001:
        raise ValueError("Expected Android XML string pool")
    header_size = u16(data, pos + 2)
    string_count = u32(data, pos + 8)
    flags = u32(data, pos + 16)
    strings_start = u32(data, pos + 20)
    offsets_base = pos + header_size
    base = pos + strings_start
    result: list[str] = []
    utf8 = bool(flags & 0x100)
    for i in range(string_count):
        p = base + u32(data, offsets_base + i * 4)
        if utf8:
            _, p = read_length8(data, p)  # UTF-16 code unit count
            byte_count, p = read_length8(data, p)
            raw = data[p:p + byte_count]
            result.append(raw.decode("utf-8", errors="replace"))
        else:
            char_count, p = read_length16(data, p)
            raw = data[p:p + char_count * 2]
            result.append(raw.decode("utf-16le", errors="replace"))
    return result


def parse_binary_manifest(data: bytes) -> dict[str, object]:
    if len(data) < 8 or u16(data, 0) != 0x0003:
        raise ValueError("AndroidManifest.xml is not Android binary XML")
    total_size = u32(data, 4)
    if total_size > len(data):
        raise ValueError("Truncated AndroidManifest.xml")
    strings: list[str] = []
    package = ""
    version_name = ""
    activities: list[dict[str, str]] = []
    application: dict[str, str] = {}
    pos = u16(data, 2)
    while pos + 8 <= total_size:
        typ = u16(data, pos)
        header_size = u16(data, pos + 2)
        size = u32(data, pos + 4)
        if size < 8 or pos + size > total_size:
            raise ValueError("Malformed Android binary XML chunk")
        if typ == 0x0001:
            strings = parse_string_pool(data, pos, size)
        elif typ == 0x0102 and strings:  # START_TAG
            name_idx = u32(data, pos + 20)
            tag = strings[name_idx] if name_idx < len(strings) else ""
            attr_start = u16(data, pos + 24)
            attr_size = u16(data, pos + 26)
            attr_count = u16(data, pos + 28)
            attrs: dict[str, str] = {}
            first_attr = pos + 16 + attr_start
            if attr_size < 20:
                raise ValueError("Malformed Android XML attribute size")
            for i in range(attr_count):
                a = first_attr + i * attr_size
                attr_name_idx = u32(data, a + 4)
                raw_idx = u32(data, a + 8)
                value_type = data[a + 15]
                value_data = u32(data, a + 16)
                key = strings[attr_name_idx] if attr_name_idx < len(strings) else f"attr_{attr_name_idx}"
                if raw_idx != 0xFFFFFFFF and raw_idx < len(strings):
                    value = strings[raw_idx]
                elif value_type == 0x03 and value_data < len(strings):
                    value = strings[value_data]
                elif value_type == 0x12:
                    value = "true" if value_data else "false"
                elif value_type in (0x10, 0x11):
                    value = str(value_data)
                else:
                    value = f"@0x{value_data:x}"
                attrs[key] = value
            if tag == "manifest":
                package = attrs.get("package", "")
                version_name = attrs.get("versionName", "")
            elif tag == "application":
                application = {"name": attrs.get("name", ""), "process": attrs.get("process", "")}
            elif tag == "activity":
                activity_name = attrs.get("name", "")
                if activity_name:
                    activities.append({"name": activity_name, "process": attrs.get("process", "")})
        pos += size
    if not package:
        raise ValueError("Could not find package attribute in AndroidManifest.xml")
    return {"package": package, "version_name": version_name, "application": application, "activities": activities}


def dex_strings(data: bytes) -> list[str]:
    count, off = struct.unpack_from("<II", data, 56)
    out: list[str] = []
    for i in range(count):
        string_off = u32(data, off + i * 4)
        _, p = read_uleb(data, string_off)
        end = data.find(b"\0", p)
        if end < 0:
            raise ValueError("Unterminated DEX string")
        # Target descriptors and method names are ASCII; replacement is enough
        # for any unrelated modified-UTF-8 strings in the same DEX.
        out.append(data[p:end].decode("utf-8", errors="replace"))
    return out


def dex_method_table(data: bytes, strings: list[str]) -> tuple[list[str], list[tuple[str, str, tuple[str, ...], str]]]:
    type_count, type_off = struct.unpack_from("<II", data, 64)
    type_desc = [strings[u32(data, type_off + i * 4)] for i in range(type_count)]

    proto_count, proto_off = struct.unpack_from("<II", data, 72)
    protos: list[tuple[str, tuple[str, ...]]] = []
    for i in range(proto_count):
        _, return_idx, params_off = struct.unpack_from("<III", data, proto_off + i * 12)
        params: tuple[str, ...] = ()
        if params_off:
            n = u32(data, params_off)
            params = tuple(type_desc[u16(data, params_off + 4 + j * 2)] for j in range(n))
        protos.append((type_desc[return_idx], params))

    method_count, method_off = struct.unpack_from("<II", data, 88)
    methods: list[tuple[str, str, tuple[str, ...], str]] = []
    for i in range(method_count):
        class_idx, proto_idx, name_idx = struct.unpack_from("<HHI", data, method_off + i * 8)
        return_desc, params = protos[proto_idx]
        methods.append((type_desc[class_idx], strings[name_idx], params, return_desc))
    return type_desc, methods


def dex_code_has_super_oncreate(data: bytes, code_off: int, methods: list[tuple[str, str, tuple[str, ...], str]]) -> bool:
    if not code_off:
        return False
    insns_size = u32(data, code_off + 12)
    insns_off = code_off + 16
    insns = struct.unpack_from("<" + "H" * insns_size, data, insns_off)
    i = 0
    while i < len(insns):
        unit = insns[i]
        opcode = unit & 0xFF
        if opcode in (0x6F, 0x75) and i + 2 < len(insns):  # invoke-super / invoke-super-range
            method_idx = insns[i + 1]
            if method_idx < len(methods):
                _, name, params, ret = methods[method_idx]
                if name == "onCreate" and params == TARGET_METHOD_PARAMS and ret == "V":
                    return True

        # Standard DEX instruction widths in 16-bit code units. This prevents
        # matching an invoke opcode embedded in an operand of another instruction.
        if opcode == 0x00 and unit in (0x0100, 0x0200, 0x0300):
            if unit == 0x0100 and i + 1 < len(insns):  # packed-switch payload
                width = 4 + insns[i + 1] * 2
            elif unit == 0x0200 and i + 1 < len(insns):  # sparse-switch payload
                width = 2 + insns[i + 1] * 4
            elif unit == 0x0300 and i + 3 < len(insns):  # fill-array-data payload
                element_width = insns[i + 1]
                element_count = insns[i + 2] | (insns[i + 3] << 16)
                width = 4 + (element_width * element_count + 1) // 2
            else:
                width = 1
        elif opcode in (0x03, 0x06, 0x09):
            width = 3
        elif opcode in (0x02, 0x05, 0x08, 0x13, 0x15, 0x16, 0x19, 0x1A, 0x1C,
                        0x1F, 0x20, 0x22, 0x23, 0x29, 0x2D, 0x2E, 0x2F, 0x30,
                        0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
                        0x3A, 0x3B, 0x3C, 0x3D):
            width = 2
        elif 0x44 <= opcode <= 0x72 and opcode not in (0x73,):
            width = 2 if opcode < 0x6E else 3
        elif 0x74 <= opcode <= 0x78:
            width = 3
        elif 0x90 <= opcode <= 0xAF or 0xD0 <= opcode <= 0xE2:
            width = 2
        elif opcode in (0x14, 0x17, 0x1B, 0x2A, 0x2B, 0x2C, 0x24, 0x25, 0x26, 0x6E, 0x70, 0x71, 0x72):
            width = 3
        elif opcode in (0x18,):
            width = 5
        elif opcode in (0xFA, 0xFB):
            width = 4
        elif opcode in (0xFC, 0xFD):
            width = 3
        elif opcode in (0xFE, 0xFF):
            width = 2
        else:
            width = 1
        i += max(width, 1)
    return False


def inspect_dex(data: bytes, dex_name: str) -> tuple[bool, str]:
    if len(data) < 112 or not data.startswith(b"dex\n"):
        return False, f"{dex_name}: not a valid DEX file"
    strings = dex_strings(data)
    types, methods = dex_method_table(data, strings)
    method_count, method_off = struct.unpack_from("<II", data, 88)
    class_count, class_off = struct.unpack_from("<II", data, 96)
    found_method = False
    found_super_call = False
    for ci in range(class_count):
        class_idx, _, _, _, _, _, class_data_off, _ = struct.unpack_from("<IIIIIIII", data, class_off + ci * 32)
        if class_idx >= len(types) or types[class_idx] != "Lcom/mojang/minecraftpe/MainActivity;" or not class_data_off:
            continue
        p = class_data_off
        static_fields, p = read_uleb(data, p)
        instance_fields, p = read_uleb(data, p)
        direct_count, p = read_uleb(data, p)
        virtual_count, p = read_uleb(data, p)
        for _ in range(static_fields + instance_fields):
            _, p = read_uleb(data, p)
            _, p = read_uleb(data, p)
        for group_count in (direct_count, virtual_count):
            method_idx = 0
            for _ in range(group_count):
                diff, p = read_uleb(data, p)
                method_idx += diff
                _, p = read_uleb(data, p)  # access flags
                code_off, p = read_uleb(data, p)
                if method_idx >= method_count:
                    raise ValueError(f"Malformed method index in {dex_name}")
                _, name, params, ret = methods[method_idx]
                if name == "onCreate" and params == TARGET_METHOD_PARAMS and ret == "V":
                    found_method = True
                    found_super_call = dex_code_has_super_oncreate(data, code_off, methods)
    if not found_method:
        return False, f"{dex_name}: MainActivity.onCreate(Bundle) not found"
    return True, f"{dex_name}: MainActivity.onCreate(Bundle) found; matching invoke-super={'yes' if found_super_call else 'no'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path, help="Single complete ARM64 APK; not an .apks bundle or split APK")
    args = parser.parse_args()
    apk = args.apk.expanduser().resolve()
    if not apk.is_file():
        print(f"ERROR: APK not found: {apk}", file=sys.stderr)
        return 2
    try:
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
            if "AndroidManifest.xml" not in names:
                raise ValueError("APK is missing AndroidManifest.xml; input may be an APK-set wrapper")
            if any(name.lower().endswith(".apk") for name in names):
                raise ValueError("APK contains nested APKs; provide a single complete APK, not an .apks wrapper")
            native_path = "lib/arm64-v8a/libminecraftpe.so"
            if native_path not in names:
                raise ValueError(f"Missing {native_path}; the input is not the required ARM64 game APK")
            manifest = parse_binary_manifest(zf.read("AndroidManifest.xml"))
            activity_names = {x["name"] for x in manifest["activities"]}  # type: ignore[index]
            if TARGET_ACTIVITY not in activity_names:
                raise ValueError(f"Manifest does not register expected activity {TARGET_ACTIVITY}")
            application = manifest["application"]
            if not isinstance(application, dict) or application.get("name") not in ("", "android.app.Application"):
                raise ValueError("Target APK uses a non-default Application class; bootstrap integration would need a preserving subclass")
            if application.get("process"):
                raise ValueError("Target Application declares android:process; this patcher expects the default app process")
            dex_names = sorted(n for n in names if n == "classes.dex" or (n.startswith("classes") and n.endswith(".dex")))
            main_activity_ok = False
            main_activity_report = ""
            for dex_name in dex_names:
                ok, report = inspect_dex(zf.read(dex_name), dex_name)
                if ok:
                    main_activity_report = report
                main_activity_ok = main_activity_ok or ok
            if not main_activity_ok:
                raise ValueError("Could not find MainActivity.onCreate(Bundle) in the APK DEX files")
            if "invoke-super=yes" not in main_activity_report:
                raise ValueError("MainActivity.onCreate(Bundle) was found, but no matching invoke-super onCreate(Bundle) was proven")
            lib_size = zf.getinfo(native_path).file_size
            print("PASS: one complete APK archive (not a split wrapper)")
            print(f"Package: {manifest['package']}")
            print(f"Version: {manifest['version_name'] or '(versionName absent)'}")
            print(f"Registered target Activity: {TARGET_ACTIVITY}")
            print(f"Original Application class: {application.get('name') or '(default)'}")
            print(f"Application process: {application.get('process') or '(default app process)'}")
            print(f"Native engine: {native_path} ({lib_size:,} bytes)")
            print(main_activity_report)
            print("NOTE: static compatibility only; this does not build the patched APK or prove hooks run at runtime.")
            return 0
    except (OSError, ValueError, zipfile.BadZipFile, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
