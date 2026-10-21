#!/usr/bin/env python3
"""断言 .dbxp 包内 sidecar 二进制的真实 CPU 架构与格式。

为什么需要这个：plugin-cli 会按当前宿主架构（或 GOARCH 环境变量）编译 Go
sidecar，产物名与 manifest 声明都是按 target 命名的。一旦架构搞错，得到的
是一个"名字叫 x64、内容是 arm64"的包——它能通过所有结构校验、能安装，
只在目标机器启动 sidecar 时崩溃。因此必须在打包后检查二进制本身。

用法：
    python scripts/assert_binary_arch.py dist/com.dbx.ratel-0.1.3-darwin-x64.dbxp \
        --expect-arch x86_64 --expect-format macho
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile

CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C
CPU_ARM64_32 = 12  # mach-o 旧 arm 标记


def detect_pe(buf: bytes) -> tuple[str, str]:
    if buf[:2] != b"MZ":
        raise ValueError("not a PE file (missing MZ)")
    pe_off = struct.unpack("<I", buf[0x3C:0x40])[0]
    if buf[pe_off : pe_off + 4] != b"PE\0\0":
        raise ValueError("not a PE file (missing PE signature)")
    machine = struct.unpack("<H", buf[pe_off + 4 : pe_off + 6])[0]
    arch = {0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "x86"}.get(machine, f"unknown(0x{machine:x})")
    return "pe", arch


def detect_elf(buf: bytes) -> tuple[str, str]:
    if buf[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    endian = "<" if buf[5] == 1 else ">"
    machine = struct.unpack(f"{endian}H", buf[18:20])[0]
    arch = {0x3E: "x86_64", 0xB7: "arm64", 0x03: "x86"}.get(machine, f"unknown(0x{machine:x})")
    return "elf", arch


def detect_macho(buf: bytes) -> tuple[str, str]:
    magic = struct.unpack(">I", buf[:4])[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        # universal binary：列出全部架构
        n = struct.unpack(">I", buf[4:8])[0]
        archs = []
        for i in range(n):
            off = 8 + i * 20
            cputype = struct.unpack(">i", buf[off : off + 4])[0]
            archs.append({CPU_X86_64: "x86_64", CPU_ARM64: "arm64"}.get(cputype, hex(cputype)))
        # 期望单一架构时，把 universal 当作不匹配，交给调用方报错
        return "macho", "+".join(archs)
    magic_le = struct.unpack("<I", buf[:4])[0]
    if magic_le not in (0xFEEDFACF, 0xFEEDFACE):
        raise ValueError(f"not a Mach-O file (magic=0x{magic_le:x})")
    cputype = struct.unpack("<i", buf[4:8])[0]
    arch = {CPU_X86_64: "x86_64", CPU_ARM64: "arm64", CPU_ARM64_32: "arm"}.get(cputype, f"unknown(0x{cputype:x})")
    return "macho", arch


DETECTORS = {"pe": detect_pe, "elf": detect_elf, "macho": detect_macho}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", help=".dbxp 包路径")
    ap.add_argument("--expect-arch", required=True, help="期望架构：x86_64 或 arm64")
    ap.add_argument("--expect-format", required=True, choices=sorted(DETECTORS), help="期望可执行格式")
    args = ap.parse_args()

    with zipfile.ZipFile(args.package) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        exe = manifest["entrypoints"]["backend"]["executable"]
        if exe not in zf.namelist():
            print(f"FAIL: manifest 声明的 {exe!r} 不在包内", file=sys.stderr)
            print(f"  包内 bin/ 下实际有：{[n for n in zf.namelist() if n.startswith('bin/')]}", file=sys.stderr)
            return 1
        buf = zf.read(exe)

    try:
        fmt, arch = DETECTORS[args.expect_format](buf)
    except ValueError as exc:
        print(f"FAIL: {exe}: {exc}", file=sys.stderr)
        return 1

    if fmt != args.expect_format:
        print(f"FAIL: {exe}: 格式是 {fmt}，期望 {args.expect_format}", file=sys.stderr)
        return 1
    if arch != args.expect_arch:
        print(
            f"FAIL: {exe}: 架构是 {arch}，期望 {args.expect_arch}。\n"
            f"      构建目标架构与产物不符——通常是因为 CLI 内部的 go build 按宿主架构编译，\n"
            f"      需要在打包前注入 GOARCH（见 scripts/package.sh）。",
            file=sys.stderr,
        )
        return 1

    print(f"OK  {exe}: {fmt} / {arch} ({len(buf):,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
