#!/usr/bin/env python3
"""Verify culler signatures against disassembly and optionally the exact APK ELF.

Without --apk this checks the fixed bytes against the labelled disassembly entries.
With --apk it additionally verifies unique matches in executable PT_LOAD segments
of that APK's ARM64 libminecraftpe.so at the expected virtual addresses.
"""
from __future__ import annotations
import argparse
import re
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASM_PATH = ROOT / "reference" / "culler_asm.txt"
PACK_PATH = ROOT / "reference" / "nova_client-0.1.0-arm64-v8a.levipack"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INSN = re.compile(r"0x([0-9a-fA-F]+)\s+((?:[0-9a-fA-F]{2}){4})\s+")


def load_signatures() -> list[tuple[str, str, int, list[str], str]]:
    asm = ANSI.sub("", ASM_PATH.read_text(errors="replace"))
    byte_at: dict[int, int] = {}
    for line in asm.splitlines():
        m = INSN.search(line)
        if m:
            addr = int(m.group(1), 16)
            for i, value in enumerate(bytes.fromhex(m.group(2))):
                byte_at[addr + i] = value

    with zipfile.ZipFile(PACK_PATH) as pack:
        ini = pack.read("nova_client/resources/signatures.ini").decode("utf-8", "replace")

    result = []
    for name, expected_slot in (("cullerQueryA", "Cached_slot8"), ("cullerQueryB", "Cached_slot11")):
        addr_match = re.search(rf"^#\s*{name}\s*=.*?@\s*(0x[0-9a-fA-F]+)", ini, re.M)
        pattern_match = re.search(rf"^{name}=([^\r\n]+)", ini, re.M)
        if not addr_match or not pattern_match:
            raise RuntimeError(f"{name}: signature/address missing in pack")
        addr = int(addr_match.group(1), 16)
        tokens = pattern_match.group(1).split()
        if not re.search(rf"^=====\s*{expected_slot}\s*@\s*0x0*{addr:x}\b", asm, re.M | re.I):
            raise RuntimeError(f"{name}: disassembly block {expected_slot} at {addr:#x} missing")
        missing = []
        mismatched = []
        for i, token in enumerate(tokens):
            actual = byte_at.get(addr + i)
            if actual is None:
                missing.append(i)
            elif token not in ("?", "??") and actual != int(token, 16):
                mismatched.append((i, token, actual))
        if missing or mismatched:
            raise RuntimeError(f"{name}: disassembly mismatch; missing={missing[:8]} mismatched={mismatched[:8]}")
        result.append((name, expected_slot, addr, tokens, pattern_match.group(1)))
    return result


def scan_elf_executable_segments(elf: bytes, signatures: list[tuple[str, str, int, list[str], str]]) -> None:
    if len(elf) < 64 or elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        raise RuntimeError("libminecraftpe.so is not ELF64 little-endian")
    machine = struct.unpack_from("<H", elf, 18)[0]
    if machine != 183:
        raise RuntimeError(f"libminecraftpe.so is not AArch64 (e_machine={machine})")
    phoff = struct.unpack_from("<Q", elf, 32)[0]
    phentsize = struct.unpack_from("<H", elf, 54)[0]
    phnum = struct.unpack_from("<H", elf, 56)[0]
    if phentsize < 56 or phoff + phentsize * phnum > len(elf):
        raise RuntimeError("Malformed ELF program-header table")
    executable = []
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, p_flags = struct.unpack_from("<II", elf, off)
        p_offset, p_vaddr, _, p_filesz, p_memsz = struct.unpack_from("<QQQQQ", elf, off + 8)
        if p_type == 1 and p_flags & 1 and p_filesz:
            if p_offset + p_filesz > len(elf):
                raise RuntimeError("Executable PT_LOAD extends past ELF file")
            executable.append((p_offset, p_vaddr, p_filesz, p_memsz))
    if not executable:
        raise RuntimeError("ELF has no file-backed executable PT_LOAD segment")

    for name, slot, expected_va, tokens, _ in signatures:
        pattern = [None if t in ("?", "??") else int(t, 16) for t in tokens]
        fixed_prefix = bytes(int(x) for x in pattern[:12])
        hits: list[tuple[int, int]] = []
        for file_off, vaddr, filesz, _ in executable:
            end = file_off + filesz
            cursor = file_off
            while True:
                found = elf.find(fixed_prefix, cursor, end)
                if found < 0:
                    break
                if found + len(pattern) <= end and all(
                    expected is None or elf[found + i] == expected
                    for i, expected in enumerate(pattern)
                ):
                    hits.append((found, vaddr + found - file_off))
                cursor = found + 1
        if len(hits) != 1:
            raise RuntimeError(f"{name}: expected exactly one match in executable PT_LOADs; found {len(hits)}")
        file_off, virtual_address = hits[0]
        if virtual_address != expected_va:
            raise RuntimeError(
                f"{name}: signature found at VA {virtual_address:#x}, but supplied analysis expects {expected_va:#x}"
            )
        print(f"PASS {name}: unique executable-segment match at VA {virtual_address:#x} (file offset {file_off:#x})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, help="Also scan the exact target APK's libminecraftpe.so executable segments")
    args = parser.parse_args()
    try:
        signatures = load_signatures()
        for name, slot, addr, tokens, _ in signatures:
            fixed = sum(t not in ("?", "??") for t in tokens)
            print(f"PASS {name}: {len(tokens)} bytes at {addr:#x}; {fixed} fixed bytes match {slot}")
        if args.apk:
            with zipfile.ZipFile(args.apk) as zf:
                info = zf.getinfo("lib/arm64-v8a/libminecraftpe.so")
                print(f"Scanning {info.file_size:,}-byte libminecraftpe.so from {args.apk}")
                elf = zf.read(info.filename)
            scan_elf_executable_segments(elf, signatures)
        else:
            print("NOTE: no APK supplied; this verifies only the pack signatures against the supplied disassembly. Runtime scanning is still required.")
        return 0
    except (RuntimeError, OSError, KeyError, zipfile.BadZipFile, struct.error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
